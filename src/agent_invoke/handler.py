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
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

COOKIE_NAME = "agent_session"
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9-]{33,128}$")
AUTH_COOKIE_NAME = "auth_session"
AUTH_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
DEFAULT_FILE_PROMPT = "Describe this file."
IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
DOCUMENT_MEDIA_TYPES = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_IMAGE_BYTES = 3_750_000
MAX_DOCUMENT_BYTES = 4_500_000
KEY_PATTERN = re.compile(r"^u/[0-9a-f]{32}/[0-9a-f]{32}$")
SIZE_ERROR_TERMS = ("too long", "too large", "exceeds", "size", "limit")
MODEL_LIMIT_MESSAGE = "File exceeds the model size limit"


class FileRejected(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


def _owner_prefix(user: str) -> str:
    return "u/" + hashlib.sha256(user.encode("utf-8")).hexdigest()[:32] + "/"


def _load_file(file: Any, user: str) -> dict[str, str]:
    key = file.get("key") if isinstance(file, dict) else None
    if not isinstance(key, str) or not KEY_PATTERN.match(key) or not key.startswith(_owner_prefix(user)):
        raise FileRejected(400, "A valid file is required")
    bucket = os.environ["UPLOAD_BUCKET"]
    s3 = boto3.client("s3")
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            raise FileRejected(400, "A valid file is required") from error
        raise
    media_type = head.get("ContentType")
    if media_type in IMAGE_MEDIA_TYPES:
        kind, limit = "image", MAX_IMAGE_BYTES
    elif media_type in DOCUMENT_MEDIA_TYPES:
        kind, limit = "document", MAX_DOCUMENT_BYTES
    else:
        raise FileRejected(400, "A valid file is required")
    if head.get("ContentLength", 0) > limit:
        raise FileRejected(413, MODEL_LIMIT_MESSAGE)
    data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if not data:
        raise FileRejected(400, "A valid file is required")
    return {"mediaType": media_type, "data": base64.b64encode(data).decode("ascii"), "kind": kind}


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


def _agent_text(result: dict[str, Any]) -> tuple[str, bool]:
    content = result.get("response", b"")
    if hasattr(content, "read"):
        content = content.read()
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    if not isinstance(content, str):
        raise ValueError("Unexpected AgentCore response")
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return content, False
    if isinstance(decoded, dict) and isinstance(decoded.get("response"), str):
        return decoded["response"], decoded.get("blocked") is True
    if isinstance(decoded, str):
        return decoded, False
    raise ValueError("Unexpected AgentCore response")


def _error_status(error: Exception) -> int:
    if isinstance(error, (ReadTimeoutError, ConnectTimeoutError, TimeoutError)):
        return 504
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "").lower()
        if "guardrail" in code:
            return 422
        message = error.response.get("Error", {}).get("Message", "").lower()
        if code == "validationexception" and any(term in message for term in SIZE_ERROR_TERMS):
            return 413
    return 502


def _log(session_id: str, status: int, started: float, error: Exception | None = None) -> None:
    correlation = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    entry: dict[str, Any] = {
        "session": correlation,
        "status": status,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }
    if error is not None:
        entry["error_type"] = type(error).__name__
        if isinstance(error, ClientError):
            entry["error_code"] = error.response.get("Error", {}).get("Code", "")
            metadata = error.response.get("ResponseMetadata", {})
            entry["http_status"] = metadata.get("HTTPStatusCode")
            entry["request_id"] = metadata.get("RequestId")
            if entry["error_code"] == "RuntimeClientError":
                entry["hint"] = "Agent container raised an error; see log group /aws/bedrock-agentcore/runtimes/"
    logger.info(json.dumps(entry))


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

    agent_payload: dict[str, Any] = {"prompt": prompt}
    if has_file:
        try:
            agent_payload["file"] = _load_file(body["file"], user)
        except FileRejected as rejected:
            return _response(rejected.status, {"error": rejected.message})
        except (BotoCoreError, ClientError, KeyError, OSError) as error:
            logger.error(json.dumps({"status": 502, "error_type": type(error).__name__}))
            return _response(502, {"error": "Agent request failed"})

    session_id = None if new_session else _cookie_session(event)
    cookies = None
    if session_id is None:
        session_id = str(uuid.uuid4())
        cookies = [_session_cookie(session_id)]

    runtime_session_id = hashlib.sha256(f"{user}:{session_id}".encode("utf-8")).hexdigest()
    try:
        parameter_name = os.environ["AGENT_RUNTIME_ARN_PARAM"]
        runtime_arn = boto3.client("ssm").get_parameter(Name=parameter_name, WithDecryption=False)["Parameter"]["Value"]
        result = boto3.client("bedrock-agentcore").invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=runtime_session_id,
            payload=json.dumps(agent_payload).encode("utf-8"),
            qualifier="DEFAULT",
        )
        text, blocked = _agent_text(result)
    except (BotoCoreError, ClientError, KeyError, ValueError, OSError) as error:
        status_code = _error_status(error)
        _log(session_id, status_code, started, error)
        message = {422: "Request blocked by safety policy", 504: "Agent request timed out", 413: MODEL_LIMIT_MESSAGE}.get(
            status_code, "Agent request failed"
        )
        return _response(status_code, {"error": message})

    if blocked:
        _log(session_id, 422, started)
        return _response(422, {"error": "Request blocked by safety policy"}, cookies)

    _log(session_id, 200, started)
    return _response(200, {"response": text}, cookies)
