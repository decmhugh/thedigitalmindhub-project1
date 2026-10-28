import base64
import hashlib
import io
import json
import time
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from src.agent_invoke import handler

RUNTIME_ARN = "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/test"
SESSION = "a" * 36
AUTH_TOKEN = "B" * 43
AUTH_COOKIE = f"auth_session={AUTH_TOKEN}"
PHONE_HASH = "p" * 64


def runtime_session(agent_session):
    return hashlib.sha256(f"{PHONE_HASH}:{agent_session}".encode()).hexdigest()


def api_event(body, cookie=None, authed=True):
    event = {"body": body, "requestContext": {}}
    cookies = ([AUTH_COOKIE] if authed else []) + ([cookie] if cookie else [])
    if cookies:
        event["cookies"] = cookies
    return event


def configure_clients(monkeypatch, runtime_response=None, runtime_error=None, session_item="default"):
    monkeypatch.setenv("AGENT_RUNTIME_ARN_PARAM", "/test/agent/runtime-arn")
    monkeypatch.setenv("AUTH_TABLE", "auth-table")
    ddb = Mock()
    if session_item == "default":
        session_item = {"phone_hash": {"S": PHONE_HASH}, "expires_at": {"N": str(int(time.time()) + 100)}}
    ddb.get_item.return_value = {"Item": session_item} if session_item else {}
    ssm = Mock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": RUNTIME_ARN}}
    runtime = Mock()
    if runtime_error:
        runtime.invoke_agent_runtime.side_effect = runtime_error
    else:
        runtime.invoke_agent_runtime.return_value = runtime_response or {
            "response": io.BytesIO(b'{"response":"Hello from agent"}'),
        }
    factory = Mock(side_effect=lambda service, **kwargs: {"ssm": ssm, "bedrock-agentcore": runtime, "dynamodb": ddb}[service])
    runtime.ddb = ddb
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
    assert call["runtimeSessionId"] == runtime_session(session_id)
    assert json.loads(call["payload"]) == {"prompt": "hello"}


def test_existing_cookie_session_is_reused_without_new_cookie(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', f"agent_session={SESSION}"), None)

    assert result["statusCode"] == 200
    assert "cookies" not in result
    assert runtime.invoke_agent_runtime.call_args.kwargs["runtimeSessionId"] == runtime_session(SESSION)


def test_new_session_flag_rotates_cookie(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(
            api_event('{"prompt":"hello","new_session":true}', f"agent_session={SESSION}"), None
        )

    new_id = session_cookie(result).split(";")[0].split("=", 1)[1]
    assert new_id != SESSION
    assert runtime.invoke_agent_runtime.call_args.kwargs["runtimeSessionId"] == runtime_session(new_id)


def test_malformed_cookie_is_replaced(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', "agent_session=short"), None)

    new_id = session_cookie(result).split(";")[0].split("=", 1)[1]
    assert new_id != "short" and len(new_id) >= 33


@pytest.mark.parametrize("body", ["not json", "{}", '{"prompt":"  "}', "null", '{"prompt":"hi","new_session":"yes"}'])
def test_invalid_body_returns_400_without_invoking_runtime(monkeypatch, body):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event(body), None)

    assert result["statusCode"] == 400
    runtime.invoke_agent_runtime.assert_not_called()


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


def test_missing_auth_cookie_returns_401_without_any_aws_call(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch as client_factory:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', authed=False), None)

    assert result["statusCode"] == 401
    assert "cookies" not in result
    client_factory.assert_not_called()


@pytest.mark.parametrize("session_item", [None, {"phone_hash": {"S": "x"}, "expires_at": {"N": "1"}}])
def test_unknown_or_expired_session_returns_401_without_invoking_runtime(monkeypatch, session_item):
    runtime, client_patch = configure_clients(monkeypatch, session_item=session_item)
    with client_patch:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 401
    runtime.invoke_agent_runtime.assert_not_called()


def test_runtime_session_is_bound_to_user(monkeypatch):
    other = {"phone_hash": {"S": "q" * 64}, "expires_at": {"N": str(int(time.time()) + 100)}}
    runtime, client_patch = configure_clients(monkeypatch, session_item=other)
    with client_patch:
        handler.lambda_handler(api_event('{"prompt":"hello"}', f"agent_session={SESSION}"), None)

    assert runtime.invoke_agent_runtime.call_args.kwargs["runtimeSessionId"] != runtime_session(SESSION)
    key = {"pk": {"S": "SESSION#" + hashlib.sha256(AUTH_TOKEN.encode()).hexdigest()}}
    assert runtime.ddb.get_item.call_args.kwargs["Key"] == key


PNG_B64 = base64.b64encode(b"\x89PNG fake image bytes").decode()


def image_body(**overrides):
    body = {"prompt": "What is it?", "image": {"mediaType": "image/png", "data": PNG_B64}}
    body.update(overrides)
    return json.dumps(body)


def test_image_is_forwarded_to_runtime(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event(image_body()), None)

    assert result["statusCode"] == 200
    payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload == {"prompt": "What is it?", "image": {"mediaType": "image/png", "data": PNG_B64}}


@pytest.mark.parametrize("prompt", ["", "  ", None])
def test_image_without_prompt_uses_default_prompt(monkeypatch, prompt):
    runtime, client_patch = configure_clients(monkeypatch)
    body = json.loads(image_body())
    if prompt is None:
        del body["prompt"]
    else:
        body["prompt"] = prompt
    with client_patch:
        result = handler.lambda_handler(api_event(json.dumps(body)), None)

    assert result["statusCode"] == 200
    payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload["prompt"] == "Describe this image."


@pytest.mark.parametrize(
    "image",
    [
        "x",
        {},
        {"mediaType": "image/png"},
        {"data": PNG_B64},
        {"mediaType": "image/bmp", "data": PNG_B64},
        {"mediaType": "image/png", "data": ""},
        {"mediaType": "image/png", "data": "not base64!!"},
        {"mediaType": "image/png", "data": base64.b64encode(b"x" * 3_750_001).decode()},
    ],
)
def test_invalid_image_returns_400_without_invoking_runtime(monkeypatch, image):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event(json.dumps({"prompt": "hi", "image": image})), None)

    assert result["statusCode"] == 400
    runtime.invoke_agent_runtime.assert_not_called()


def test_unauthenticated_image_request_returns_401(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event(image_body(), authed=False), None)

    assert result["statusCode"] == 401
    runtime.invoke_agent_runtime.assert_not_called()


def test_image_request_logs_do_not_contain_image_or_prompt(monkeypatch, caplog):
    runtime, client_patch = configure_clients(monkeypatch)
    with caplog.at_level("INFO"), client_patch:
        handler.lambda_handler(api_event(image_body(prompt="secret question")), None)

    assert PNG_B64 not in caplog.text
    assert "secret question" not in caplog.text
