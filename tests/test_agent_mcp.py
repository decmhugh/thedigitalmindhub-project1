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

    def get_item(self, Key):
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
    client_factory = Mock(side_effect=lambda service, **kwargs: {"ssm": ssm, "bedrock-agentcore": runtime}[service])
    resource_factory = Mock(return_value=dynamodb)
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
    assert rpc_response(listed)["result"]["tools"][0]["name"] == "ask_agent"


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
