import base64
import hashlib
import json
import logging
import os
import secrets
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
DEFAULT_PROTOCOL_VERSION = "2025-11-25"
SESSION_TTL_SECONDS = 24 * 60 * 60
TOOL_NAME = "ask_agent"
MAX_BODY_BYTES = 1_000_000


def _json_response(
    status: int,
    body: dict[str, Any],
    session_id: str | None = None,
) -> dict[str, Any]:
    headers = {"content-type": "application/json"}
    if session_id is not None:
        headers["MCP-Session-Id"] = session_id
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


def _empty_response(status: int, headers: dict[str, str] | None = None) -> dict[str, Any]:
    return {"statusCode": status, "headers": headers or {}, "body": ""}


def _rpc_error(
    request_id: Any,
    code: int,
    message: str,
    status: int = 200,
    session_id: str | None = None,
) -> dict[str, Any]:
    return _json_response(
        status,
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        session_id,
    )


def _header(event: dict[str, Any], name: str) -> str | None:
    headers = event.get("headers")
    if not isinstance(headers, dict):
        return None
    wanted = name.lower()
    value = next((value for key, value in headers.items() if key.lower() == wanted), None)
    return value if isinstance(value, str) else None


def _body(event: dict[str, Any]) -> dict[str, Any] | None:
    body = event.get("body")
    if event.get("isBase64Encoded") and isinstance(body, str):
        try:
            body = base64.b64decode(body, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return None
    if not isinstance(body, str) or len(body.encode("utf-8")) > MAX_BODY_BYTES:
        return None
    try:
        decoded = json.loads(body)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _accepts_json_and_event_stream(event: dict[str, Any]) -> bool:
    accept = _header(event, "accept")
    if not accept:
        return False
    media_types = {part.split(";", 1)[0].strip().lower() for part in accept.split(",")}
    return "application/json" in media_types and "text/event-stream" in media_types


def _table():
    table_name = os.environ["MCP_SESSION_TABLE"]
    return boto3.resource("dynamodb").Table(table_name)


def _new_session(table: Any, protocol_version: str) -> str:
    session_id = secrets.token_urlsafe(32)
    now = int(time.time())
    table.put_item(
        Item={
            "session_id": session_id,
            "runtime_session_id": str(uuid.uuid4()),
            "protocol_version": protocol_version,
            "expires_at": now + SESSION_TTL_SECONDS,
        }
    )
    return session_id


def _active_session(table: Any, session_id: str) -> dict[str, Any] | None:
    result = table.get_item(Key={"session_id": session_id}, ConsistentRead=True)
    item = result.get("Item")
    if not isinstance(item, dict):
        return None
    now = int(time.time())
    if not isinstance(item.get("expires_at"), (int, float)) or item["expires_at"] <= now:
        table.delete_item(Key={"session_id": session_id})
        return None
    table.update_item(
        Key={"session_id": session_id},
        UpdateExpression="SET expires_at = :expires_at",
        ExpressionAttributeValues={":expires_at": now + SESSION_TTL_SECONDS},
    )
    return item


def _initialize(request: dict[str, Any], table: Any) -> dict[str, Any]:
    request_id = request.get("id")
    params = request.get("params")
    version = params.get("protocolVersion") if isinstance(params, dict) else None
    if (
        request.get("jsonrpc") != "2.0"
        or not isinstance(request_id, (str, int))
        or isinstance(request_id, bool)
        or not isinstance(version, str)
    ):
        return _rpc_error(request_id, -32600, "Invalid initialize request", status=400)

    negotiated_version = version if version in SUPPORTED_PROTOCOL_VERSIONS else DEFAULT_PROTOCOL_VERSION
    session_id = _new_session(table, negotiated_version)
    return _json_response(
        200,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": negotiated_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "project-agent", "version": "1.0.0"},
            },
        },
        session_id,
    )


def _tools_list(request_id: Any, session_id: str) -> dict[str, Any]:
    return _json_response(
        200,
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": TOOL_NAME,
                        "description": "Send a prompt to the project AI agent.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"prompt": {"type": "string", "minLength": 1}},
                            "required": ["prompt"],
                        },
                        "outputSchema": {
                            "type": "object",
                            "properties": {
                                "response": {"type": "string"},
                                "blocked": {"type": "boolean"},
                            },
                            "required": ["response"],
                        },
                    }
                ]
            },
        },
        session_id,
    )


def _agent_response(result: dict[str, Any]) -> tuple[str, bool]:
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


def _log(session_id: str, status: int, started: float, error: Exception | None = None) -> None:
    entry: dict[str, Any] = {
        "event": "agent_mcp_call",
        "session": hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12],
        "status": status,
        "latency_ms": round((time.monotonic() - started) * 1000),
    }
    if error is not None:
        entry["error_type"] = type(error).__name__
        if isinstance(error, ClientError):
            entry["error_code"] = error.response.get("Error", {}).get("Code", "")
    logger.info(json.dumps(entry))


def _log_failure(event: dict[str, Any], error: Exception) -> None:
    session_id = _header(event, "mcp-session-id")
    request_id = event.get("requestContext", {}).get("requestId", "")
    correlation_source = session_id or str(request_id)
    entry: dict[str, Any] = {
        "event": "agent_mcp_request_failed",
        "correlation": hashlib.sha256(correlation_source.encode("utf-8")).hexdigest()[:12],
        "error_type": type(error).__name__,
    }
    if isinstance(error, ClientError):
        entry["error_code"] = error.response.get("Error", {}).get("Code", "")
    logger.error(json.dumps(entry))


def _call_agent(prompt: str, session: dict[str, Any], session_id: str) -> dict[str, Any]:
    started = time.monotonic()
    try:
        runtime_arn_param = os.environ["AGENT_RUNTIME_ARN_PARAM"]
        runtime_arn = boto3.client("ssm").get_parameter(
            Name=runtime_arn_param, WithDecryption=False
        )["Parameter"]["Value"]
        result = boto3.client("bedrock-agentcore").invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session["runtime_session_id"],
            payload=json.dumps({"prompt": prompt}).encode("utf-8"),
            qualifier="DEFAULT",
        )
        response, blocked = _agent_response(result)
        if blocked:
            text = "Request blocked by safety policy"
            structured = {"response": text, "blocked": True}
            _log(session_id, 422, started)
            return {"content": [{"type": "text", "text": text}], "structuredContent": structured, "isError": True}
        _log(session_id, 200, started)
        return {
            "content": [{"type": "text", "text": response}],
            "structuredContent": {"response": response, "blocked": False},
        }
    except (
        BotoCoreError,
        ClientError,
        ConnectTimeoutError,
        ReadTimeoutError,
        TimeoutError,
        KeyError,
        ValueError,
        OSError,
    ) as error:
        if isinstance(error, ClientError):
            code = error.response.get("Error", {}).get("Code", "").lower()
            if "guardrail" in code:
                text = "Request blocked by safety policy"
                _log(session_id, 422, started, error)
                return {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": {"response": text, "blocked": True},
                    "isError": True,
                }
        status = 504 if isinstance(error, (ConnectTimeoutError, ReadTimeoutError, TimeoutError)) else 502
        _log(session_id, status, started, error)
        text = "Agent request timed out" if status == 504 else "Agent request failed"
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": {"response": text, "blocked": False},
            "isError": True,
        }


def _tool_call(
    request_id: Any,
    request: dict[str, Any],
    session: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    params = request.get("params")
    if not isinstance(params, dict) or params.get("name") != TOOL_NAME:
        return _rpc_error(request_id, -32602, "Unknown tool or invalid arguments", session_id=session_id)
    arguments = params.get("arguments")
    prompt = arguments.get("prompt") if isinstance(arguments, dict) else None
    if not isinstance(prompt, str) or not prompt.strip():
        return _rpc_error(request_id, -32602, "A non-empty prompt is required", session_id=session_id)
    return _json_response(
        200,
        {"jsonrpc": "2.0", "id": request_id, "result": _call_agent(prompt, session, session_id)},
        session_id,
    )


def _post(event: dict[str, Any]) -> dict[str, Any]:
    if not _accepts_json_and_event_stream(event):
        return _empty_response(406)
    content_type = _header(event, "content-type")
    if not content_type or content_type.split(";", 1)[0].strip().lower() != "application/json":
        return _empty_response(415)
    request = _body(event)
    if request is None:
        return _rpc_error(None, -32700, "Invalid JSON-RPC request", status=400)

    method = request.get("method")
    table = _table()
    if method == "initialize":
        if _header(event, "mcp-session-id") is not None:
            return _rpc_error(request.get("id"), -32600, "Initialize must not include a session ID", status=400)
        return _initialize(request, table)

    session_id = _header(event, "mcp-session-id")
    protocol_version = _header(event, "mcp-protocol-version")
    if not session_id or not protocol_version:
        return _rpc_error(request.get("id"), -32600, "Session and protocol version headers are required", status=400)
    session = _active_session(table, session_id)
    if session is None:
        return _rpc_error(request.get("id"), -32001, "MCP session is invalid or expired", status=404)
    if protocol_version != session.get("protocol_version"):
        return _rpc_error(request.get("id"), -32600, "MCP protocol version does not match the session", status=400)

    if request.get("jsonrpc") != "2.0":
        return _rpc_error(request.get("id"), -32600, "Invalid JSON-RPC request", status=400)
    request_id = request.get("id")
    has_id = isinstance(request_id, (str, int)) and not isinstance(request_id, bool)
    if isinstance(method, str) and method.startswith("notifications/") and not has_id:
        return _empty_response(202, {"MCP-Session-Id": session_id})
    if not has_id:
        return _rpc_error(None, -32600, "A request ID is required", status=400)
    if method == "tools/list":
        return _tools_list(request_id, session_id)
    if method == "tools/call":
        return _tool_call(request_id, request, session, session_id)
    return _rpc_error(request_id, -32601, "Method not found", session_id=session_id)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method == "GET":
        return _empty_response(405, {"allow": "POST, DELETE"})
    if method == "DELETE":
        session_id = _header(event, "mcp-session-id")
        protocol_version = _header(event, "mcp-protocol-version")
        if not session_id or not protocol_version:
            return _empty_response(400)
        try:
            table = _table()
            session = _active_session(table, session_id)
            if session is None:
                return _empty_response(404)
            if protocol_version != session.get("protocol_version"):
                return _empty_response(400)
            table.delete_item(Key={"session_id": session_id})
        except (BotoCoreError, ClientError, KeyError, OSError) as error:
            _log_failure(event, error)
            return _empty_response(500)
        return _empty_response(200)
    if method != "POST":
        return _empty_response(405, {"allow": "POST, GET, DELETE"})
    try:
        return _post(event)
    except (BotoCoreError, ClientError, KeyError, ValueError, OSError) as error:
        _log_failure(event, error)
        return _rpc_error(None, -32603, "Internal server error", status=500)
