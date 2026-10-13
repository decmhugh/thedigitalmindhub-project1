import base64
import json
import logging
import os
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


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


def _agent_text(result: dict[str, Any]) -> str:
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
        return content
    if isinstance(decoded, dict) and isinstance(decoded.get("response"), str):
        return decoded["response"]
    if isinstance(decoded, str):
        return decoded
    raise ValueError("Unexpected AgentCore response")


def _error_status(error: Exception) -> int:
    if isinstance(error, (ReadTimeoutError, ConnectTimeoutError, TimeoutError)):
        return 504
    if isinstance(error, ClientError):
        code = error.response.get("Error", {}).get("Code", "").lower()
        if "guardrail" in code:
            return 422
    return 502


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    started = time.monotonic()
    body = _body(event)
    if body is None or not isinstance(body.get("prompt"), str) or not body["prompt"].strip():
        return _response(400, {"error": "A non-empty prompt is required"})

    claims = event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    user_id = claims.get("sub") if isinstance(claims, dict) else None
    if not isinstance(user_id, str) or not user_id:
        return _response(401, {"error": "Unauthorized"})

    session_id = body.get("session_id")
    if session_id is None:
        session_id = str(uuid.uuid4())
    elif not isinstance(session_id, str) or len(session_id) < 33:
        return _response(400, {"error": "session_id must contain at least 33 characters"})

    try:
        parameter_name = os.environ["AGENT_RUNTIME_ARN_PARAM"]
        runtime_arn = boto3.client("ssm").get_parameter(Name=parameter_name, WithDecryption=False)["Parameter"]["Value"]
        runtime = boto3.client("bedrock-agentcore")
        result = runtime.invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session_id,
            payload=json.dumps({"prompt": body["prompt"], "user_id": user_id}).encode("utf-8"),
            qualifier="DEFAULT",
        )
        text = _agent_text(result)
        logger.info(json.dumps({"session_id": session_id, "status": 200, "latency_ms": round((time.monotonic() - started) * 1000)}))
        return _response(200, {"response": text, "session_id": session_id})
    except (BotoCoreError, ClientError, KeyError, ValueError, OSError) as error:
        status_code = _error_status(error)
        logger.warning(json.dumps({"session_id": session_id, "status": status_code, "latency_ms": round((time.monotonic() - started) * 1000)}))
        message = "Request blocked by safety policy" if status_code == 422 else "Agent request timed out" if status_code == 504 else "Agent request failed"
        return _response(status_code, {"error": message})
