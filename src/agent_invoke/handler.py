import base64
import hashlib
import json
import logging
import os
import re
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

COOKIE_NAME = "agent_session"
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9-]{33,128}$")
AUTH_COOKIE_NAME = "auth_session"
AUTH_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
DEFAULT_FILE_PROMPT = "Describe this file."
KEY_PATTERN = re.compile(r"^u/[0-9a-f]{32}/[0-9a-f]{32}$")
JOB_TTL_SECONDS = 3600
LOCK_TTL_SECONDS = 360


def _owner_prefix(user: str) -> str:
    return "u/" + hashlib.sha256(user.encode("utf-8")).hexdigest()[:32] + "/"


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


def _cookie_session(event: dict[str, Any]) -> str | None:
    for cookie in event.get("cookies") or []:
        name, _, value = cookie.strip().partition("=")
        if name == COOKIE_NAME and SESSION_PATTERN.match(value):
            return value
    return None


def _authenticated_user(event: dict[str, Any]) -> str | None:
    """Return the signed-in user's phone hash for a valid auth_session cookie."""
    for cookie in event.get("cookies") or []:
        name, _, value = cookie.strip().partition("=")
        if name == AUTH_COOKIE_NAME and AUTH_TOKEN_PATTERN.match(value):
            key = {"pk": {"S": "SESSION#" + hashlib.sha256(value.encode("utf-8")).hexdigest()}}
            item = boto3.client("dynamodb").get_item(TableName=os.environ["AUTH_TABLE"], Key=key).get("Item")
            if item and int(item["expires_at"]["N"]) > int(time.time()):
                return item["phone_hash"]["S"]
    return None


def _session_cookie(session_id: str) -> str:
    return f"{COOKIE_NAME}={session_id}; Path=/agent; Secure; HttpOnly; SameSite=Strict"


def _valid_file(file: Any, user: str) -> bool:
    key = file.get("key") if isinstance(file, dict) else None
    return isinstance(key, str) and bool(KEY_PATTERN.match(key)) and key.startswith(_owner_prefix(user))


def _log(job_id: str, status: int, started: float, error: Exception | None = None) -> None:
    entry: dict[str, Any] = {
        "job": hashlib.sha256(job_id.encode("utf-8")).hexdigest()[:12],
        "status": status,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }
    if error is not None:
        entry["error_type"] = type(error).__name__
    logger.info(json.dumps(entry))


def _start_job(user: str, session_id: str, job: dict[str, Any]) -> str:
    """Take the per-session lock, store the job, start the worker. Raises ConditionalCheckFailedException if busy."""
    ddb = boto3.client("dynamodb")
    table = os.environ["JOBS_TABLE"]
    now = int(time.time())
    job_id = uuid.uuid4().hex
    lock_pk = "LOCK#" + hashlib.sha256(f"{user}:{session_id}".encode("utf-8")).hexdigest()
    ddb.put_item(
        TableName=table,
        Item={"pk": {"S": lock_pk}, "expires_at": {"N": str(now + LOCK_TTL_SECONDS)}},
        ConditionExpression="attribute_not_exists(pk) OR expires_at < :now",
        ExpressionAttributeValues={":now": {"N": str(now)}},
    )
    try:
        ddb.put_item(
            TableName=table,
            Item={
                "pk": {"S": "JOB#" + job_id},
                "owner": {"S": user},
                "status": {"S": "pending"},
                "created_at": {"N": str(now)},
                "expires_at": {"N": str(now + JOB_TTL_SECONDS)},
            },
        )
        boto3.client("lambda").invoke(
            FunctionName=os.environ["WORKER_FUNCTION_NAME"],
            InvocationType="Event",
            Payload=json.dumps({**job, "job_id": job_id, "user": user, "session_id": session_id, "lock_pk": lock_pk}).encode("utf-8"),
        )
    except Exception:
        ddb.delete_item(TableName=table, Key={"pk": {"S": lock_pk}})
        raise
    return job_id


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    started = time.monotonic()
    try:
        user = _authenticated_user(event)
    except (BotoCoreError, ClientError, KeyError, ValueError, OSError) as error:
        logger.warning(json.dumps({"status": 502, "error_type": type(error).__name__}))
        return _response(502, {"error": "Agent request failed"})
    if user is None:
        return _response(401, {"error": "Sign in required"})

    body = _body(event)
    if body is None:
        return _response(400, {"error": "A non-empty prompt is required"})
    has_file = body.get("file") is not None
    prompt = body.get("prompt")
    if has_file and (prompt is None or (isinstance(prompt, str) and not prompt.strip())):
        prompt = DEFAULT_FILE_PROMPT
    if not isinstance(prompt, str) or not prompt.strip():
        return _response(400, {"error": "A non-empty prompt is required"})
    new_session = body.get("new_session", False)
    if not isinstance(new_session, bool):
        return _response(400, {"error": "new_session must be a boolean"})
    if has_file and not _valid_file(body["file"], user):
        return _response(400, {"error": "A valid file is required"})

    session_id = None if new_session else _cookie_session(event)
    cookies = None
    if session_id is None:
        session_id = str(uuid.uuid4())
        cookies = [_session_cookie(session_id)]

    job: dict[str, Any] = {"prompt": prompt}
    if has_file:
        file = {"key": body["file"]["key"]}
        if isinstance(body["file"].get("name"), str):
            file["name"] = body["file"]["name"][:100]
        job["file"] = file
    try:
        job_id = _start_job(user, session_id, job)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return _response(409, {"error": "Still working on your last request"}, cookies)
        _log(session_id, 502, started, error)
        return _response(502, {"error": "Agent request failed"})
    except (BotoCoreError, KeyError, OSError) as error:
        _log(session_id, 502, started, error)
        return _response(502, {"error": "Agent request failed"})

    _log(job_id, 202, started)
    return _response(202, {"job_id": job_id}, cookies)
