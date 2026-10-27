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
CODE_TTL_SECONDS = 300
RESEND_COOLDOWN_SECONDS = 60


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status_code, "headers": {"content-type": "application/json"}, "body": json.dumps(body)}


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


def _log(phone_hash: str, status: int, started: float, error: Exception | None = None) -> None:
    entry: dict[str, Any] = {
        "route": "auth_start",
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
    body = _body(event)
    phone = body.get("phone") if body else None
    if not isinstance(phone, str) or not PHONE_PATTERN.match(phone):
        return _response(400, {"error": "A valid phone number in E.164 format is required"})

    phone_hash = ""
    try:
        pepper = boto3.client("ssm").get_parameter(Name=os.environ["AUTH_PEPPER_PARAM"], WithDecryption=True)[
            "Parameter"
        ]["Value"]
        phone_hash = _hmac(pepper, phone)
        code = f"{secrets.randbelow(10**6):06d}"
        now = int(time.time())
        try:
            boto3.client("dynamodb").put_item(
                TableName=os.environ["AUTH_TABLE"],
                Item={
                    "pk": {"S": f"CODE#{phone_hash}"},
                    "code_hash": {"S": _hmac(pepper, f"{phone_hash}:{code}")},
                    "attempts": {"N": "0"},
                    "sent_at": {"N": str(now)},
                    "expires_at": {"N": str(now + CODE_TTL_SECONDS)},
                },
                ConditionExpression="attribute_not_exists(pk) OR sent_at <= :cutoff",
                ExpressionAttributeValues={":cutoff": {"N": str(now - RESEND_COOLDOWN_SECONDS)}},
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                _log(phone_hash, 429, started)
                return _response(429, {"error": "Please wait before requesting another code"})
            raise
        boto3.client("pinpoint-sms-voice-v2").send_text_message(
            DestinationPhoneNumber=phone,
            OriginationIdentity=os.environ["SMS_SENDER_ID"],
            MessageBody=f"Your verification code is {code}. It expires in 5 minutes.",
            MessageType="TRANSACTIONAL",
        )
    except (BotoCoreError, ClientError, KeyError, OSError) as error:
        _log(phone_hash, 502, started, error)
        return _response(502, {"error": "Unable to send code"})

    _log(phone_hash, 200, started)
    return _response(200, {"status": "code_sent"})
