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
    logger.info(json.dumps(entry))


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    started = time.monotonic()
    body = _body(event)
    if body is None or not isinstance(body.get("prompt"), str) or not body["prompt"].strip():
        return _response(400, {"error": "A non-empty prompt is required"})
    new_session = body.get("new_session", False)
    if not isinstance(new_session, bool):
        return _response(400, {"error": "new_session must be a boolean"})

    session_id = None if new_session else _cookie_session(event)
    cookies = None
    if session_id is None:
        session_id = str(uuid.uuid4())
        cookies = [_session_cookie(session_id)]

    try:
        parameter_name = os.environ["AGENT_RUNTIME_ARN_PARAM"]
        runtime_arn = boto3.client("ssm").get_parameter(Name=parameter_name, WithDecryption=False)["Parameter"]["Value"]
        result = boto3.client("bedrock-agentcore").invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps({"prompt": body["prompt"]}).encode("utf-8"),
            qualifier="DEFAULT",
        )
        text, blocked = _agent_text(result)
    except (BotoCoreError, ClientError, KeyError, ValueError, OSError) as error:
        status_code = _error_status(error)
        _log(session_id, status_code, started, error)
        message = {422: "Request blocked by safety policy", 504: "Agent request timed out"}.get(status_code, "Agent request failed")
        return _response(status_code, {"error": message})

    if blocked:
        _log(session_id, 422, started)
        return _response(422, {"error": "Request blocked by safety policy"}, cookies)

    _log(session_id, 200, started)
    return _response(200, {"response": text}, cookies)
