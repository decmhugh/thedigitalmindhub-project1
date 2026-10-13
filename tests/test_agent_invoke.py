import io
import json
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from src.agent_invoke import handler

RUNTIME_ARN = "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/test"
SESSION = "a" * 36


def api_event(body, cookie=None):
    event = {"body": body, "requestContext": {}}
    if cookie:
        event["cookies"] = [cookie]
    return event


def configure_clients(monkeypatch, runtime_response=None, runtime_error=None):
    monkeypatch.setenv("AGENT_RUNTIME_ARN_PARAM", "/test/agent/runtime-arn")
    ssm = Mock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": RUNTIME_ARN}}
    runtime = Mock()
    if runtime_error:
        runtime.invoke_agent_runtime.side_effect = runtime_error
    else:
        runtime.invoke_agent_runtime.return_value = runtime_response or {
            "response": io.BytesIO(b'{"response":"Hello from agent"}'),
        }
    factory = Mock(side_effect=lambda service, **kwargs: {"ssm": ssm, "bedrock-agentcore": runtime}[service])
    return runtime, patch.object(handler.boto3, "client", factory)


def session_cookie(result):
    cookies = result.get("cookies", [])
    assert len(cookies) == 1
    return cookies[0]


def test_first_request_sets_browser_session_cookie_without_exposing_id(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"response": "Hello from agent"}
    cookie = session_cookie(result)
    assert cookie.startswith("agent_session=")
    for attribute in ("Path=/agent", "Secure", "HttpOnly", "SameSite=Strict"):
        assert attribute in cookie
    assert "Expires" not in cookie and "Max-Age" not in cookie
    session_id = cookie.split(";")[0].split("=", 1)[1]
    assert len(session_id) >= 33
    assert session_id not in result["body"]
    call = runtime.invoke_agent_runtime.call_args.kwargs
    assert call["agentRuntimeArn"] == RUNTIME_ARN
    assert call["runtimeSessionId"] == session_id
    assert json.loads(call["payload"]) == {"prompt": "hello"}


def test_existing_cookie_session_is_reused_without_new_cookie(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', f"agent_session={SESSION}"), None)

    assert result["statusCode"] == 200
    assert "cookies" not in result
    assert runtime.invoke_agent_runtime.call_args.kwargs["runtimeSessionId"] == SESSION


def test_new_session_flag_rotates_cookie(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(
            api_event('{"prompt":"hello","new_session":true}', f"agent_session={SESSION}"), None
        )

    new_id = session_cookie(result).split(";")[0].split("=", 1)[1]
    assert new_id != SESSION
    assert runtime.invoke_agent_runtime.call_args.kwargs["runtimeSessionId"] == new_id


def test_malformed_cookie_is_replaced(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', "agent_session=short"), None)

    new_id = session_cookie(result).split(";")[0].split("=", 1)[1]
    assert new_id != "short" and len(new_id) >= 33


def test_request_needs_no_authorization(monkeypatch):
    _, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 200


@pytest.mark.parametrize("body", ["not json", "{}", '{"prompt":"  "}', "null", '{"prompt":"hi","new_session":"yes"}'])
def test_invalid_body_returns_400_without_aws_calls(monkeypatch, body):
    _, client_patch = configure_clients(monkeypatch)
    with client_patch as client_factory:
        result = handler.lambda_handler(api_event(body), None)

    assert result["statusCode"] == 400
    client_factory.assert_not_called()


def test_agentcore_timeout_returns_504_without_internal_details(monkeypatch):
    error = ReadTimeoutError(endpoint_url="https://runtime.example")
    _, client_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 504
    assert "runtime.example" not in result["body"]


def test_agentcore_failure_returns_502_without_internal_details(monkeypatch):
    error = ClientError({"Error": {"Code": "InternalServerException", "Message": "secret details"}}, "InvokeAgentRuntime")
    _, client_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 502
    assert "secret details" not in result["body"]


def test_guardrail_block_returns_422_without_internal_details(monkeypatch):
    error = ClientError({"Error": {"Code": "GuardrailIntervened", "Message": "blocked content"}}, "InvokeAgentRuntime")
    _, client_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 422
    assert "blocked content" not in result["body"]


def test_runtime_reported_guardrail_block_returns_422_without_leaking_text(monkeypatch):
    blocked = {"response": io.BytesIO(b'{"response":"policy text","blocked":true}')}
    _, client_patch = configure_clients(monkeypatch, runtime_response=blocked)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 422
    assert "policy text" not in result["body"]


def test_failure_logs_error_type_and_code_but_not_message_or_prompt(monkeypatch, caplog):
    error = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "secret details"}}, "InvokeAgentRuntime")
    _, client_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch, caplog.at_level("INFO"):
        handler.lambda_handler(api_event('{"prompt":"secret prompt"}', f"agent_session={SESSION}"), None)

    assert "ClientError" in caplog.text
    assert "AccessDeniedException" in caplog.text
    assert "secret details" not in caplog.text
    assert "secret prompt" not in caplog.text
    assert SESSION not in caplog.text


def test_missing_configuration_is_logged_as_error_type(monkeypatch, caplog):
    monkeypatch.delenv("AGENT_RUNTIME_ARN_PARAM", raising=False)
    _, client_patch = configure_clients(monkeypatch)
    monkeypatch.delenv("AGENT_RUNTIME_ARN_PARAM", raising=False)
    with client_patch, caplog.at_level("INFO"):
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 502
    assert "KeyError" in caplog.text


def test_logs_do_not_contain_session_id_or_prompt(monkeypatch, caplog):
    _, client_patch = configure_clients(monkeypatch)
    with client_patch, caplog.at_level("INFO"):
        handler.lambda_handler(api_event('{"prompt":"secret prompt"}', f"agent_session={SESSION}"), None)

    assert SESSION not in caplog.text
    assert "secret prompt" not in caplog.text
