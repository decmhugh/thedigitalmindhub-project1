import base64
import hashlib
import io
import json
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from src.agent_mcp import handler

RUNTIME_ARN = "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/test"
PROTOCOL_VERSION = "2025-11-25"


class SessionTable:
    def __init__(self):
        self.items = {}

    def put_item(self, Item):
        self.items[Item["session_id"]] = Item

    def get_item(self, Key, **kwargs):
        item = self.items.get(Key["session_id"])
        return {"Item": item} if item else {}

    def update_item(self, Key, ExpressionAttributeValues, **kwargs):
        self.items[Key["session_id"]]["expires_at"] = ExpressionAttributeValues[":expires_at"]

    def delete_item(self, Key):
        self.items.pop(Key["session_id"], None)


def event(method, body=None, session_id=None, protocol_version=PROTOCOL_VERSION):
    headers = {"accept": "application/json, text/event-stream"}
    if body is not None:
        headers["content-type"] = "application/json"
    if session_id:
        headers["mcp-session-id"] = session_id
    if protocol_version:
        headers["mcp-protocol-version"] = protocol_version
    return {
        "body": json.dumps(body) if body is not None else None,
        "headers": headers,
        "requestContext": {"http": {"method": method}},
    }


def initialize(table, request_id=1):
    result = handler.lambda_handler(
        event(
            "POST",
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
            },
            protocol_version=None,
        ),
        None,
    )
    session_id = result["headers"]["MCP-Session-Id"]
    table_id = session_id
    assert table.items[table_id]["protocol_version"] == PROTOCOL_VERSION
    return result, session_id


def configure_clients(monkeypatch, runtime_response=None, runtime_error=None):
    monkeypatch.setenv("AGENT_RUNTIME_ARN_PARAM", "/test/agent/runtime-arn")
    monkeypatch.setenv("MCP_SESSION_TABLE", "test-agent-mcp-sessions")
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    s3 = Mock()
    s3.generate_presigned_post.return_value = {"url": "https://s3.example/test-uploads", "fields": {"k": "v"}}
    s3.head_object.return_value = {"ContentType": "image/png", "ContentLength": 21}
    s3.get_object.side_effect = lambda **kwargs: {"Body": io.BytesIO(b"\x89PNG fake image bytes")}
    table = SessionTable()
    dynamodb = Mock()
    dynamodb.Table.return_value = table
    ssm = Mock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": RUNTIME_ARN}}
    runtime = Mock()
    if runtime_error:
        runtime.invoke_agent_runtime.side_effect = runtime_error
    else:
        runtime.invoke_agent_runtime.return_value = runtime_response or {
            "response": io.BytesIO(b'{"response":"Hello from agent"}'),
        }
    client_factory = Mock(
        side_effect=lambda service, **kwargs: {"ssm": ssm, "bedrock-agentcore": runtime, "s3": s3}[service]
    )
    resource_factory = Mock(return_value=dynamodb)
    runtime.s3 = s3
    return table, runtime, patch.object(handler.boto3, "client", client_factory), patch.object(
        handler.boto3, "resource", resource_factory
    )


def rpc_response(result):
    return json.loads(result["body"])


def test_initialization_and_tool_discovery(monkeypatch):
    table, _, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        initialized, session_id = initialize(table)
        listed = handler.lambda_handler(
            event(
                "POST",
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                session_id,
            ),
            None,
        )

    assert initialized["statusCode"] == 200
    assert rpc_response(initialized)["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert initialized["headers"]["MCP-Session-Id"] == session_id
    assert [tool["name"] for tool in rpc_response(listed)["result"]["tools"]] == [
        "ask_agent",
        "read_image",
        "get_upload_url",
        "read_file",
    ]


def owner_prefix(session_id):
    return "u/" + hashlib.sha256(f"mcp:{session_id}".encode()).hexdigest()[:32] + "/"


def call_tool(session_id, name, arguments, request_id=2):
    return handler.lambda_handler(
        event(
            "POST",
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            session_id,
        ),
        None,
    )


def test_get_upload_url_returns_session_scoped_presigned_post(monkeypatch):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        result = call_tool(session_id, "get_upload_url", {"mediaType": "application/pdf", "size": 2048})

    structured = rpc_response(result)["result"]["structuredContent"]
    assert structured["url"] == "https://s3.example/test-uploads"
    assert structured["fields"] == {"k": "v"}
    assert structured["expiresIn"] == 300
    assert structured["key"].startswith(owner_prefix(session_id))
    call = runtime.s3.generate_presigned_post.call_args.kwargs
    assert call["Bucket"] == "test-uploads" and call["ExpiresIn"] == 300
    assert {"Content-Type": "application/pdf"} in call["Conditions"]
    assert ["content-length-range", 1, 2048] in call["Conditions"]
    runtime.invoke_agent_runtime.assert_not_called()


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"mediaType": "image/bmp", "size": 10},
        {"mediaType": "image/png"},
        {"mediaType": "image/png", "size": 0},
        {"mediaType": "image/png", "size": 25 * 1024 * 1024 + 1},
        {"mediaType": "image/png", "size": "10"},
    ],
)
def test_get_upload_url_rejects_invalid_arguments(monkeypatch, arguments):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        result = call_tool(session_id, "get_upload_url", arguments)

    assert rpc_response(result)["error"]["code"] == -32602
    runtime.s3.generate_presigned_post.assert_not_called()


def test_read_file_reads_s3_object_and_forwards_to_agent(monkeypatch):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        key = owner_prefix(session_id) + "a" * 32
        result = call_tool(session_id, "read_file", {"key": key, "prompt": "What is it?"})

    assert rpc_response(result)["result"]["content"] == [{"type": "text", "text": "Hello from agent"}]
    runtime.s3.head_object.assert_called_once_with(Bucket="test-uploads", Key=key)
    payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload == {
        "prompt": "What is it?",
        "file": {"mediaType": "image/png", "data": PNG_B64, "kind": "image"},
    }


def test_read_file_uses_default_prompt(monkeypatch):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        call_tool(session_id, "read_file", {"key": owner_prefix(session_id) + "a" * 32})

    payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload["prompt"] == "Describe this file."


def test_read_file_rejects_other_sessions_key(monkeypatch):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        _, first_id = initialize(table, request_id=1)
        _, second_id = initialize(table, request_id=2)
        result = call_tool(second_id, "read_file", {"key": owner_prefix(first_id) + "a" * 32})

    assert rpc_response(result)["error"]["code"] == -32602
    runtime.s3.head_object.assert_not_called()
    runtime.invoke_agent_runtime.assert_not_called()


@pytest.mark.parametrize("arguments", [{}, {"key": 5}, {"key": "u/short"}, {"key": ""}])
def test_read_file_rejects_invalid_keys(monkeypatch, arguments):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        result = call_tool(session_id, "read_file", arguments)

    assert rpc_response(result)["error"]["code"] == -32602
    runtime.invoke_agent_runtime.assert_not_called()


def test_read_file_missing_object_is_invalid_params(monkeypatch):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    runtime.s3.head_object.side_effect = ClientError({"Error": {"Code": "404", "Message": "x"}}, "HeadObject")
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        result = call_tool(session_id, "read_file", {"key": owner_prefix(session_id) + "a" * 32})

    assert rpc_response(result)["error"]["code"] == -32602
    runtime.invoke_agent_runtime.assert_not_called()


@pytest.mark.parametrize("content_type,size", [("image/png", 3_750_001), ("application/pdf", 4_500_001)])
def test_read_file_over_model_limit_is_a_tool_error(monkeypatch, content_type, size):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    runtime.s3.head_object.return_value = {"ContentType": content_type, "ContentLength": size}
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        result = call_tool(session_id, "read_file", {"key": owner_prefix(session_id) + "a" * 32})

    tool_result = rpc_response(result)["result"]
    assert tool_result["isError"] is True
    assert tool_result["content"] == [{"type": "text", "text": "File exceeds the model size limit"}]
    runtime.s3.get_object.assert_not_called()
    runtime.invoke_agent_runtime.assert_not_called()


def test_read_file_runtime_size_error_is_model_limit_error(monkeypatch):
    error = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "Input is too long for requested model"}},
        "InvokeAgentRuntime",
    )
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        result = call_tool(session_id, "read_file", {"key": owner_prefix(session_id) + "a" * 32})

    tool_result = rpc_response(result)["result"]
    assert tool_result["isError"] is True
    assert tool_result["content"][0]["text"] == "File exceeds the model size limit"
    assert "too long" not in result["body"]


def test_file_tool_logs_do_not_include_key_or_content(monkeypatch, caplog):
    table, _, client_patch, resource_patch = configure_clients(monkeypatch)
    with caplog.at_level("INFO"), client_patch, resource_patch:
        _, session_id = initialize(table)
        key = owner_prefix(session_id) + "a" * 32
        call_tool(session_id, "read_file", {"key": key, "prompt": "secret question"})

    assert key not in caplog.text
    assert PNG_B64 not in caplog.text
    assert "secret question" not in caplog.text
    assert session_id not in caplog.text


PNG_B64 = base64.b64encode(b"\x89PNG fake image bytes").decode()


def call_read_image(table, arguments):
    _, session_id = initialize(table)
    return handler.lambda_handler(
        event(
            "POST",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "read_image", "arguments": arguments},
            },
            session_id,
        ),
        None,
    )


def test_read_image_forwards_image_to_agent(monkeypatch):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        result = call_read_image(table, {"image": PNG_B64, "mediaType": "image/png", "prompt": "What is it?"})

    assert rpc_response(result)["result"]["content"] == [{"type": "text", "text": "Hello from agent"}]
    payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload == {"prompt": "What is it?", "image": {"mediaType": "image/png", "data": PNG_B64}}


def test_read_image_uses_default_prompt(monkeypatch):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        call_read_image(table, {"image": PNG_B64, "mediaType": "image/png", "prompt": "  "})

    payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload["prompt"] == "Describe this image."


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"mediaType": "image/png"},
        {"image": PNG_B64},
        {"image": PNG_B64, "mediaType": "image/bmp"},
        {"image": "not base64!!", "mediaType": "image/png"},
        {"image": "", "mediaType": "image/png"},
        {"image": PNG_B64, "mediaType": "image/png", "prompt": 5},
        {"image": base64.b64encode(b"x" * (3_750_000 + 1)).decode(), "mediaType": "image/png"},
    ],
)
def test_read_image_rejects_invalid_arguments_without_invoking_agent(monkeypatch, arguments):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        result = call_read_image(table, arguments)

    assert rpc_response(result)["error"]["code"] == -32602
    runtime.invoke_agent_runtime.assert_not_called()


def test_read_image_logs_do_not_include_image_or_prompt(monkeypatch, caplog):
    table, _, client_patch, resource_patch = configure_clients(monkeypatch)
    with caplog.at_level("INFO"), client_patch, resource_patch:
        call_read_image(table, {"image": PNG_B64, "mediaType": "image/png", "prompt": "secret question"})

    assert PNG_B64 not in caplog.text
    assert "secret question" not in caplog.text


def test_tool_call_invokes_agent_and_returns_text(monkeypatch):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        result = handler.lambda_handler(
            event(
                "POST",
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "ask_agent", "arguments": {"prompt": "hello"}},
                },
                session_id,
            ),
            None,
        )

    tool_result = rpc_response(result)["result"]
    assert tool_result["content"] == [{"type": "text", "text": "Hello from agent"}]
    runtime.invoke_agent_runtime.assert_called_once()
    assert json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"]) == {"prompt": "hello"}


def test_agent_runtime_session_is_isolated_per_mcp_session(monkeypatch):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        _, first_id = initialize(table, request_id=1)
        _, second_id = initialize(table, request_id=2)
        for session_id in (first_id, second_id):
            handler.lambda_handler(
                event(
                    "POST",
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "ask_agent", "arguments": {"prompt": "hello"}},
                    },
                    session_id,
                ),
                None,
            )

    session_ids = [call.kwargs["runtimeSessionId"] for call in runtime.invoke_agent_runtime.call_args_list]
    assert len(set(session_ids)) == 2


@pytest.mark.parametrize("arguments", [{}, {"prompt": "  "}, {"prompt": 42}])
def test_invalid_prompt_returns_tool_input_error_without_invoking_agent(monkeypatch, arguments):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        result = handler.lambda_handler(
            event(
                "POST",
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "ask_agent", "arguments": arguments},
                },
                session_id,
            ),
            None,
        )

    assert rpc_response(result)["error"]["code"] == -32602
    runtime.invoke_agent_runtime.assert_not_called()


def test_guardrail_intervention_is_a_blocked_tool_result(monkeypatch):
    blocked = {"response": io.BytesIO(b'{"response":"sensitive policy text","blocked":true}')}
    table, _, client_patch, resource_patch = configure_clients(monkeypatch, runtime_response=blocked)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        result = handler.lambda_handler(
            event(
                "POST",
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "ask_agent", "arguments": {"prompt": "hello"}},
                },
                session_id,
            ),
            None,
        )

    tool_result = rpc_response(result)["result"]
    assert tool_result["isError"] is True
    assert tool_result["structuredContent"]["blocked"] is True
    assert "sensitive policy text" not in result["body"]


def test_guardrail_exception_is_a_blocked_tool_result(monkeypatch):
    error = ClientError(
        {"Error": {"Code": "GuardrailIntervened", "Message": "blocked content"}},
        "InvokeAgentRuntime",
    )
    table, _, client_patch, resource_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        result = handler.lambda_handler(
            event(
                "POST",
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "ask_agent", "arguments": {"prompt": "hello"}},
                },
                session_id,
            ),
            None,
        )

    tool_result = rpc_response(result)["result"]
    assert tool_result["isError"] is True
    assert tool_result["structuredContent"]["blocked"] is True
    assert "blocked content" not in result["body"]


def test_runtime_failure_is_generic_tool_error(monkeypatch):
    error = ClientError(
        {"Error": {"Code": "InternalServerException", "Message": "secret details"}},
        "InvokeAgentRuntime",
    )
    table, _, client_patch, resource_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        result = handler.lambda_handler(
            event(
                "POST",
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "ask_agent", "arguments": {"prompt": "hello"}},
                },
                session_id,
            ),
            None,
        )

    assert rpc_response(result)["result"]["isError"] is True
    assert "secret details" not in result["body"]


def test_session_activity_renews_expiry_and_expired_session_is_rejected(monkeypatch):
    table, runtime, client_patch, resource_patch = configure_clients(monkeypatch)
    now = 1_800_000_000
    monkeypatch.setattr(handler.time, "time", lambda: now)
    with client_patch, resource_patch:
        _, session_id = initialize(table)
        first_expiry = table.items[session_id]["expires_at"]
        monkeypatch.setattr(handler.time, "time", lambda: now + 600)
        result = handler.lambda_handler(
            event("POST", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session_id),
            None,
        )
        renewed_expiry = table.items[session_id]["expires_at"]
        monkeypatch.setattr(handler.time, "time", lambda: now + 24 * 60 * 60 + 601)
        expired = handler.lambda_handler(
            event("POST", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, session_id),
            None,
        )

    assert rpc_response(result)["result"]["tools"]
    assert renewed_expiry > first_expiry
    assert expired["statusCode"] == 404
    runtime.invoke_agent_runtime.assert_not_called()


def test_delete_terminates_only_the_selected_session(monkeypatch):
    table, _, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        _, first_id = initialize(table, request_id=1)
        _, second_id = initialize(table, request_id=2)
        deleted = handler.lambda_handler(event("DELETE", session_id=first_id), None)
        other = handler.lambda_handler(
            event("POST", {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}, second_id),
            None,
        )
        deleted_again = handler.lambda_handler(
            event("POST", {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}, first_id),
            None,
        )

    assert deleted["statusCode"] == 200
    assert other["statusCode"] == 200
    assert deleted_again["statusCode"] == 404


def test_get_is_not_supported_and_invalid_request_is_rejected(monkeypatch):
    _, _, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch:
        get_result = handler.lambda_handler(event("GET"), None)
        invalid = handler.lambda_handler(event("POST", {"not": "json-rpc"}), None)

    assert get_result["statusCode"] == 405
    assert invalid["statusCode"] == 400


def test_logs_do_not_include_prompt_or_raw_session_id(monkeypatch, caplog):
    table, _, client_patch, resource_patch = configure_clients(monkeypatch)
    with client_patch, resource_patch, caplog.at_level("INFO"):
        _, session_id = initialize(table)
        handler.lambda_handler(
            event(
                "POST",
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "ask_agent", "arguments": {"prompt": "private prompt"}},
                },
                session_id,
            ),
            None,
        )

    assert session_id not in caplog.text
    assert "private prompt" not in caplog.text
