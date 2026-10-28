import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
import uuid
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
DEFAULT_PROTOCOL_VERSION = "2025-11-25"
SESSION_TTL_SECONDS = 24 * 60 * 60
TOOL_NAME = "ask_agent"
IMAGE_TOOL_NAME = "read_image"
UPLOAD_URL_TOOL_NAME = "get_upload_url"
FILE_TOOL_NAME = "read_file"
DEFAULT_IMAGE_PROMPT = "Describe this image."
DEFAULT_FILE_PROMPT = "Describe this file."
IMAGE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
DOCUMENT_MEDIA_TYPES = {
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
UPLOAD_MEDIA_TYPES = IMAGE_MEDIA_TYPES | DOCUMENT_MEDIA_TYPES
MAX_IMAGE_BYTES = 3_750_000
MAX_DOCUMENT_BYTES = 4_500_000
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_URL_EXPIRY_SECONDS = 300
KEY_PATTERN = re.compile(r"^u/[0-9a-f]{32}/[0-9a-f]{32}$")
SIZE_ERROR_TERMS = ("too long", "too large", "exceeds", "size", "limit")
MODEL_LIMIT_MESSAGE = "File exceeds the model size limit"
MAX_IMAGE_BYTES = 3_750_000
MAX_BODY_BYTES = 5_100_000


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
    expires_at = item.get("expires_at")
    # DynamoDB returns numbers as Decimal.
    if not isinstance(expires_at, (int, float, Decimal)) or isinstance(expires_at, bool) or expires_at <= now:
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
                    },
                    {
                        "name": IMAGE_TOOL_NAME,
                        "description": "Ask the project AI agent a question about an image.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "image": {"type": "string", "minLength": 1, "description": "Base64-encoded image"},
                                "mediaType": {"type": "string", "enum": sorted(IMAGE_MEDIA_TYPES)},
                                "prompt": {"type": "string"},
                            },
                            "required": ["image", "mediaType"],
                        },
                        "outputSchema": {
                            "type": "object",
                            "properties": {
                                "response": {"type": "string"},
                                "blocked": {"type": "boolean"},
                            },
                            "required": ["response"],
                        },
                    },
                    {
                        "name": UPLOAD_URL_TOOL_NAME,
                        "description": (
                            "Get a short-lived presigned S3 POST (url, fields, key) to upload an image or document "
                            "up to 25 MB, then pass the key to read_file."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "mediaType": {"type": "string", "enum": sorted(UPLOAD_MEDIA_TYPES)},
                                "size": {"type": "integer", "minimum": 1, "maximum": MAX_UPLOAD_BYTES},
                            },
                            "required": ["mediaType", "size"],
                        },
                        "outputSchema": {
                            "type": "object",
                            "properties": {
                                "url": {"type": "string"},
                                "fields": {"type": "object"},
                                "key": {"type": "string"},
                                "expiresIn": {"type": "integer"},
                            },
                            "required": ["url", "fields", "key", "expiresIn"],
                        },
                    },
                    {
                        "name": FILE_TOOL_NAME,
                        "description": "Ask the project AI agent a question about a file uploaded with get_upload_url.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"key": {"type": "string", "minLength": 1}, "prompt": {"type": "string"}},
                            "required": ["key"],
                        },
                        "outputSchema": {
                            "type": "object",
                            "properties": {
                                "response": {"type": "string"},
                                "blocked": {"type": "boolean"},
                            },
                            "required": ["response"],
                        },
                    },
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


def _call_agent(
    prompt: str,
    session: dict[str, Any],
    session_id: str,
    image: dict[str, str] | None = None,
    file: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        runtime_arn_param = os.environ["AGENT_RUNTIME_ARN_PARAM"]
        runtime_arn = boto3.client("ssm").get_parameter(
            Name=runtime_arn_param, WithDecryption=False
        )["Parameter"]["Value"]
        agent_payload: dict[str, Any] = {"prompt": prompt}
        if image is not None:
            agent_payload["image"] = image
        if file is not None:
            agent_payload["file"] = file
        result = boto3.client("bedrock-agentcore").invoke_agent_runtime(
            agentRuntimeArn=runtime_arn,
            runtimeSessionId=session["runtime_session_id"],
            payload=json.dumps(agent_payload).encode("utf-8"),
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
            if _is_size_error(error):
                _log(session_id, 413, started, error)
                return _model_limit_result()
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


def _model_limit_result() -> dict[str, Any]:
    text = MODEL_LIMIT_MESSAGE
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": {"response": text, "blocked": False},
        "isError": True,
    }


def _is_size_error(error: Exception) -> bool:
    if not isinstance(error, ClientError):
        return False
    details = error.response.get("Error", {})
    message = details.get("Message", "").lower()
    return details.get("Code", "").lower() == "validationexception" and any(
        term in message for term in SIZE_ERROR_TERMS
    )


def _owner_prefix(session_id: str) -> str:
    return "u/" + hashlib.sha256(f"mcp:{session_id}".encode("utf-8")).hexdigest()[:32] + "/"


class FileRejected(Exception):
    def __init__(self, too_large: bool = False):
        self.too_large = too_large


def _load_file(arguments: dict[str, Any], session_id: str) -> dict[str, str]:
    key = arguments.get("key")
    if not isinstance(key, str) or not KEY_PATTERN.match(key) or not key.startswith(_owner_prefix(session_id)):
        raise FileRejected()
    bucket = os.environ["UPLOAD_BUCKET"]
    s3 = boto3.client("s3")
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
            raise FileRejected() from error
        raise
    media_type = head.get("ContentType")
    if media_type in IMAGE_MEDIA_TYPES:
        kind, limit = "image", MAX_IMAGE_BYTES
    elif media_type in DOCUMENT_MEDIA_TYPES:
        kind, limit = "document", MAX_DOCUMENT_BYTES
    else:
        raise FileRejected()
    if head.get("ContentLength", 0) > limit:
        raise FileRejected(too_large=True)
    data = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    if not data:
        raise FileRejected()
    return {"mediaType": media_type, "data": base64.b64encode(data).decode("ascii"), "kind": kind}


def _upload_url(arguments: dict[str, Any], session_id: str) -> dict[str, Any] | None:
    media_type = arguments.get("mediaType")
    size = arguments.get("size")
    if (
        media_type not in UPLOAD_MEDIA_TYPES
        or not isinstance(size, int)
        or isinstance(size, bool)
        or not 1 <= size <= MAX_UPLOAD_BYTES
    ):
        return None
    key = _owner_prefix(session_id) + uuid.uuid4().hex
    post = boto3.client(
        "s3",
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}),
    ).generate_presigned_post(
        Bucket=os.environ["UPLOAD_BUCKET"],
        Key=key,
        Fields={"Content-Type": media_type, "x-amz-server-side-encryption": "AES256"},
        Conditions=[
            {"Content-Type": media_type},
            {"x-amz-server-side-encryption": "AES256"},
            ["content-length-range", 1, size],
        ],
        ExpiresIn=UPLOAD_URL_EXPIRY_SECONDS,
    )
    structured = {"url": post["url"], "fields": post["fields"], "key": key, "expiresIn": UPLOAD_URL_EXPIRY_SECONDS}
    return {"content": [{"type": "text", "text": json.dumps(structured)}], "structuredContent": structured}


def _tool_error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _tool_call(
    request_id: Any,
    request: dict[str, Any],
    session: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    params = request.get("params")
    tool_name = params.get("name") if isinstance(params, dict) else None
    if tool_name not in (TOOL_NAME, IMAGE_TOOL_NAME, UPLOAD_URL_TOOL_NAME, FILE_TOOL_NAME):
        return _rpc_error(request_id, -32602, "Unknown tool or invalid arguments", session_id=session_id)
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}

    def ok(result: dict[str, Any]) -> dict[str, Any]:
        return _json_response(200, {"jsonrpc": "2.0", "id": request_id, "result": result}, session_id)

    if tool_name == UPLOAD_URL_TOOL_NAME:
        try:
            result = _upload_url(arguments, session_id)
        except (BotoCoreError, ClientError, KeyError, OSError) as error:
            _log(session_id, 502, time.monotonic(), error)
            return ok(_tool_error("Upload request failed"))
        if result is None:
            return _rpc_error(
                request_id, -32602, "A supported mediaType and a size up to 25 MB are required", session_id=session_id
            )
        return ok(result)

    prompt = arguments.get("prompt")
    image = None
    file = None
    if tool_name == IMAGE_TOOL_NAME:
        image = _validated_image(arguments)
        if image is None or not (prompt is None or isinstance(prompt, str)):
            return _rpc_error(request_id, -32602, "A valid image and media type are required", session_id=session_id)
        prompt = prompt.strip() if isinstance(prompt, str) and prompt.strip() else DEFAULT_IMAGE_PROMPT
    elif tool_name == FILE_TOOL_NAME:
        if not (prompt is None or isinstance(prompt, str)):
            return _rpc_error(request_id, -32602, "prompt must be a string", session_id=session_id)
        prompt = prompt.strip() if isinstance(prompt, str) and prompt.strip() else DEFAULT_FILE_PROMPT
        try:
            file = _load_file(arguments, session_id)
        except FileRejected as rejected:
            if rejected.too_large:
                return ok(_model_limit_result())
            return _rpc_error(request_id, -32602, "A valid file key is required", session_id=session_id)
        except (BotoCoreError, ClientError, KeyError, OSError) as error:
            _log(session_id, 502, time.monotonic(), error)
            return ok(_tool_error("Agent request failed"))
    if not isinstance(prompt, str) or not prompt.strip():
        return _rpc_error(request_id, -32602, "A non-empty prompt is required", session_id=session_id)
    return ok(_call_agent(prompt, session, session_id, image, file))


def _validated_image(arguments: dict[str, Any]) -> dict[str, str] | None:
    data = arguments.get("image")
    media_type = arguments.get("mediaType")
    if not isinstance(data, str) or not data or media_type not in IMAGE_MEDIA_TYPES:
        return None
    if len(data) > MAX_IMAGE_BYTES * 4 // 3 + 4:
        return None
    try:
        decoded = base64.b64decode(data, validate=True)
    except ValueError:
        return None
    if not decoded or len(decoded) > MAX_IMAGE_BYTES:
        return None
    return {"mediaType": media_type, "data": data}


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
