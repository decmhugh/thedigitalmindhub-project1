import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")
CODE_PATTERN = re.compile(r"^\d{6}$")
MAX_ATTEMPTS = 5
SESSION_TTL_SECONDS = 43200
COOKIE_NAME = "auth_session"


def _response(status_code: int, body: dict[str, Any], cookies: list[str] | None = None) -> dict[str, Any]:
    response: dict[str, Any] = {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }
    if cookies:
        response["cookies"] = cookies
    return response


def _body(event: dict[str, Any]) -> dict[str, Any] | None:
    body = event.get("body")
    if event.get("isBase64Encoded") and isinstance(body, str):
        try:
            body = base64.b64decode(body, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    if not isinstance(body, str):
        return None
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _hmac(pepper: str, message: str) -> str:
    return hmac.new(pepper.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def _is_conditional_failure(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def _mask(phone: str) -> str:
    return phone[:4] + "*" * (len(phone) - 7) + phone[-3:]


def _log(phone_hash: str, status: int, started: float, error: Exception | None = None) -> None:
    entry: dict[str, Any] = {
        "route": "auth_verify",
        "user": phone_hash[:12],
        "status": status,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }
    if error is not None:
        entry["error_type"] = type(error).__name__
        if isinstance(error, ClientError):
            entry["error_code"] = error.response.get("Error", {}).get("Code", "")
    logger.info(json.dumps(entry))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    started = time.monotonic()
    body = _body(event) or {}
    phone, code = body.get("phone"), body.get("code")
    if (
        not isinstance(phone, str)
        or not PHONE_PATTERN.match(phone)
        or not isinstance(code, str)
        or not CODE_PATTERN.match(code)
    ):
        return _response(400, {"error": "A valid phone number and 6-digit code are required"})

    phone_hash = ""
    try:
        table = os.environ["AUTH_TABLE"]
        pepper = boto3.client("ssm").get_parameter(Name=os.environ["AUTH_PEPPER_PARAM"], WithDecryption=True)[
            "Parameter"
        ]["Value"]
        phone_hash = _hmac(pepper, phone)
        ddb = boto3.client("dynamodb")
        code_key = {"pk": {"S": f"CODE#{phone_hash}"}}
        now = int(time.time())

        item = ddb.get_item(TableName=table, Key=code_key, ConsistentRead=True).get("Item")
        if not item or int(item["expires_at"]["N"]) <= now:
            _log(phone_hash, 401, started)
            return _response(401, {"error": "Invalid or expired code"})

        try:
            ddb.update_item(
                TableName=table,
                Key=code_key,
                UpdateExpression="ADD attempts :one",
                ConditionExpression="attempts < :max",
                ExpressionAttributeValues={":one": {"N": "1"}, ":max": {"N": str(MAX_ATTEMPTS)}},
            )
        except ClientError as error:
            if _is_conditional_failure(error):
                _log(phone_hash, 429, started)
                return _response(429, {"error": "Too many attempts"})
            raise

        expected = item["code_hash"]["S"]
        if not hmac.compare_digest(expected, _hmac(pepper, f"{phone_hash}:{code}")):
            _log(phone_hash, 401, started)
            return _response(401, {"error": "Invalid or expired code"})

        try:
            ddb.delete_item(
                TableName=table,
                Key=code_key,
                ConditionExpression="code_hash = :hash",
                ExpressionAttributeValues={":hash": {"S": expected}},
            )
        except ClientError as error:
            if _is_conditional_failure(error):
                _log(phone_hash, 401, started)
                return _response(401, {"error": "Invalid or expired code"})
            raise

        try:
            ddb.put_item(
                TableName=table,
                Item={"pk": {"S": f"USER#{phone_hash}"}, "created_at": {"N": str(now)}},
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as error:
            if not _is_conditional_failure(error):
                raise

        token = secrets.token_urlsafe(32)
        ddb.put_item(
            TableName=table,
            Item={
                "pk": {"S": "SESSION#" + hashlib.sha256(token.encode("utf-8")).hexdigest()},
                "phone_hash": {"S": phone_hash},
                "phone_masked": {"S": _mask(phone)},
                "expires_at": {"N": str(now + SESSION_TTL_SECONDS)},
            },
        )
    except (BotoCoreError, ClientError, KeyError, ValueError, OSError) as error:
        _log(phone_hash, 502, started, error)
        return _response(502, {"error": "Unable to verify code"})

    _log(phone_hash, 200, started)
    cookie = f"{COOKIE_NAME}={token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL_SECONDS}"
    return _response(200, {"authenticated": True}, [cookie])
