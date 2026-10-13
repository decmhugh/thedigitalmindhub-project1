import io
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from src.agent_invoke import handler


RUNTIME_ARN = "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/test"


def api_event(body, claims=None):
    return {
        "body": body,
        "requestContext": {
            "authorizer": {"jwt": {"claims": claims or {}}},
        },
    }


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
            "contentType": "application/json",
        }
    client_factory = Mock(side_effect=lambda service, **kwargs: {"ssm": ssm, "bedrock-agentcore": runtime}[service])
    return ssm, runtime, patch.object(handler.boto3, "client", client_factory)


def test_valid_request_invokes_runtime_and_generates_session(monkeypatch):
    ssm, runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', {"sub": "user-123"}), None)

    assert result["statusCode"] == 200
    response = json.loads(result["body"])
    assert response["response"] == "Hello from agent"
    assert len(response["session_id"]) >= 33
    ssm.get_parameter.assert_called_once_with(Name="/test/agent/runtime-arn", WithDecryption=False)
    call = runtime.invoke_agent_runtime.call_args.kwargs
    assert call["agentRuntimeArn"] == RUNTIME_ARN
    assert call["runtimeSessionId"] == response["session_id"]
    assert json.loads(call["payload"]) == {"prompt": "hello", "user_id": "user-123"}


def test_supplied_session_id_is_reused(monkeypatch):
    _, runtime, client_patch = configure_clients(monkeypatch)
    session_id = "s" * 36
    with client_patch:
        result = handler.lambda_handler(
            api_event('{"prompt":"hello","session_id":"' + session_id + '"}', {"sub": "user-123"}),
            None,
        )

    assert json.loads(result["body"])["session_id"] == session_id
    assert runtime.invoke_agent_runtime.call_args.kwargs["runtimeSessionId"] == session_id


@pytest.mark.parametrize("body", ["not json", "{}", '{"prompt":"  "}', "null"])
def test_invalid_body_returns_400_without_aws_calls(monkeypatch, body):
    _, _, client_patch = configure_clients(monkeypatch)
    with client_patch as client_factory:
        result = handler.lambda_handler(api_event(body, {"sub": "user-123"}), None)

    assert result["statusCode"] == 400
    client_factory.assert_not_called()


def test_missing_jwt_subject_returns_401_without_aws_calls(monkeypatch):
    _, _, client_patch = configure_clients(monkeypatch)
    with client_patch as client_factory:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 401
    client_factory.assert_not_called()


def test_short_session_id_returns_400_without_aws_calls(monkeypatch):
    _, _, client_patch = configure_clients(monkeypatch)
    with client_patch as client_factory:
        result = handler.lambda_handler(
            api_event('{"prompt":"hello","session_id":"too-short"}', {"sub": "user-123"}),
            None,
        )

    assert result["statusCode"] == 400
    client_factory.assert_not_called()


def test_agentcore_timeout_returns_504_without_internal_details(monkeypatch):
    error = ReadTimeoutError(endpoint_url="https://runtime.example")
    _, _, client_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', {"sub": "user-123"}), None)

    assert result["statusCode"] == 504
    assert "runtime.example" not in result["body"]


def test_agentcore_failure_returns_502_without_internal_details(monkeypatch):
    error = ClientError({"Error": {"Code": "InternalServerException", "Message": "secret details"}}, "InvokeAgentRuntime")
    _, _, client_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', {"sub": "user-123"}), None)

    assert result["statusCode"] == 502
    assert "secret details" not in result["body"]


def test_guardrail_block_returns_422_without_internal_details(monkeypatch):
    error = ClientError({"Error": {"Code": "GuardrailIntervened", "Message": "blocked content"}}, "InvokeAgentRuntime")
    _, _, client_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', {"sub": "user-123"}), None)

    assert result["statusCode"] == 422
    assert "blocked content" not in result["body"]
