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
PNG_BYTES = b"\x89PNG fake image bytes"
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
OWNER_PREFIX = "u/" + hashlib.sha256(PHONE_HASH.encode()).hexdigest()[:32] + "/"
KEY = OWNER_PREFIX + "a" * 32


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
    s3 = Mock()
    s3.head_object.return_value = {"ContentType": "image/png", "ContentLength": len(PNG_BYTES)}
    s3.get_object.return_value = {"Body": io.BytesIO(PNG_BYTES)}
    ssm = Mock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": RUNTIME_ARN}}
    runtime = Mock()
    if runtime_error:
        runtime.invoke_agent_runtime.side_effect = runtime_error
    else:
        runtime.invoke_agent_runtime.return_value = runtime_response or {
            "response": io.BytesIO(b'{"response":"Hello from agent"}'),
        }
    factory = Mock(
        side_effect=lambda service, **kwargs: {
            "ssm": ssm,
            "bedrock-agentcore": runtime,
            "dynamodb": ddb,
            "s3": s3,
        }[service]
    )
    runtime.ddb = ddb
    runtime.s3 = s3
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


def test_runtime_error_log_has_http_status_request_id_and_hint(monkeypatch, caplog):
    error = ClientError(
        {
            "Error": {"Code": "RuntimeClientError", "Message": "secret details"},
            "ResponseMetadata": {"HTTPStatusCode": 424, "RequestId": "req-123"},
        },
        "InvokeAgentRuntime",
    )
    _, client_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch, caplog.at_level("INFO"):
        handler.lambda_handler(api_event('{"prompt":"secret prompt"}', f"agent_session={SESSION}"), None)

    assert '"http_status": 424' in caplog.text
    assert '"request_id": "req-123"' in caplog.text
    assert "/aws/bedrock-agentcore/runtimes/" in caplog.text
    assert "secret details" not in caplog.text
    assert "secret prompt" not in caplog.text


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


def file_body(**overrides):
    body = {"prompt": "What is it?", "file": {"key": KEY}}
    body.update(overrides)
    return json.dumps(body)


def test_file_is_read_from_s3_and_forwarded_to_runtime(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event(file_body()), None)

    assert result["statusCode"] == 200
    runtime.s3.head_object.assert_called_once_with(Bucket="test-uploads", Key=KEY)
    runtime.s3.get_object.assert_called_once_with(Bucket="test-uploads", Key=KEY)
    payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload == {
        "prompt": "What is it?",
        "file": {"mediaType": "image/png", "data": PNG_B64, "kind": "image"},
    }


def test_documents_are_forwarded_as_document_kind(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    runtime, client_patch = configure_clients(monkeypatch)
    runtime.s3.head_object.return_value = {"ContentType": "application/pdf", "ContentLength": len(PNG_BYTES)}
    with client_patch:
        handler.lambda_handler(api_event(file_body()), None)

    payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload["file"]["kind"] == "document"
    assert payload["file"]["mediaType"] == "application/pdf"


def test_original_filename_is_forwarded_truncated_when_provided(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    runtime, client_patch = configure_clients(monkeypatch)
    body = json.dumps({"prompt": "x", "file": {"key": KEY, "name": "Report " + "a" * 300 + ".pdf"}})
    with client_patch:
        handler.lambda_handler(api_event(body), None)

    payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload["file"]["name"].startswith("Report ") and len(payload["file"]["name"]) == 100


@pytest.mark.parametrize("prompt", ["", "  ", None])
def test_file_without_prompt_uses_default_prompt(monkeypatch, prompt):
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    runtime, client_patch = configure_clients(monkeypatch)
    body = json.loads(file_body())
    if prompt is None:
        del body["prompt"]
    else:
        body["prompt"] = prompt
    with client_patch:
        result = handler.lambda_handler(api_event(json.dumps(body)), None)

    assert result["statusCode"] == 200
    payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload["prompt"] == "Describe this file."


@pytest.mark.parametrize(
    "file",
    [
        "x",
        {},
        {"key": 5},
        {"key": ""},
        {"key": "u/" + "0" * 32 + "/" + "a" * 32},
        {"key": OWNER_PREFIX + "../other"},
        {"key": "other/" + "a" * 32},
        {"key": OWNER_PREFIX},
    ],
)
def test_invalid_or_foreign_key_returns_400_without_touching_s3_or_runtime(monkeypatch, file):
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event(json.dumps({"prompt": "hi", "file": file})), None)

    assert result["statusCode"] == 400
    runtime.s3.head_object.assert_not_called()
    runtime.s3.get_object.assert_not_called()
    runtime.invoke_agent_runtime.assert_not_called()


def test_missing_object_returns_400(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    runtime, client_patch = configure_clients(monkeypatch)
    runtime.s3.head_object.side_effect = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
    with client_patch:
        result = handler.lambda_handler(api_event(file_body()), None)

    assert result["statusCode"] == 400
    runtime.invoke_agent_runtime.assert_not_called()


def test_unsupported_object_type_returns_400(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    runtime, client_patch = configure_clients(monkeypatch)
    runtime.s3.head_object.return_value = {"ContentType": "image/bmp", "ContentLength": 10}
    with client_patch:
        result = handler.lambda_handler(api_event(file_body()), None)

    assert result["statusCode"] == 400
    runtime.s3.get_object.assert_not_called()
    runtime.invoke_agent_runtime.assert_not_called()


@pytest.mark.parametrize(
    "content_type,size",
    [("image/png", 3_750_001), ("application/pdf", 4_500_001), ("text/plain", 25 * 1024 * 1024)],
)
def test_file_over_model_limit_returns_413_without_reading_or_invoking(monkeypatch, content_type, size):
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    runtime, client_patch = configure_clients(monkeypatch)
    runtime.s3.head_object.return_value = {"ContentType": content_type, "ContentLength": size}
    with client_patch:
        result = handler.lambda_handler(api_event(file_body()), None)

    assert result["statusCode"] == 413
    assert json.loads(result["body"]) == {"error": "File exceeds the model size limit"}
    runtime.s3.get_object.assert_not_called()
    runtime.invoke_agent_runtime.assert_not_called()


def test_runtime_size_error_is_mapped_to_413(monkeypatch):
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    error = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "Input is too long for requested model"}},
        "InvokeAgentRuntime",
    )
    runtime, client_patch = configure_clients(monkeypatch, runtime_error=error)
    with client_patch:
        result = handler.lambda_handler(api_event(file_body()), None)

    assert result["statusCode"] == 413
    assert "too long" not in result["body"]


def test_base64_image_field_is_no_longer_accepted(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    body = json.dumps({"prompt": "hi", "image": {"mediaType": "image/png", "data": PNG_B64}})
    with client_patch:
        result = handler.lambda_handler(api_event(body), None)

    assert result["statusCode"] == 200
    assert json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"]) == {"prompt": "hi"}


def test_unauthenticated_file_request_returns_401(monkeypatch):
    runtime, client_patch = configure_clients(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event(file_body(), authed=False), None)

    assert result["statusCode"] == 401
    runtime.s3.head_object.assert_not_called()
    runtime.invoke_agent_runtime.assert_not_called()


def test_file_request_logs_do_not_contain_file_key_or_prompt(monkeypatch, caplog):
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    runtime, client_patch = configure_clients(monkeypatch)
    with caplog.at_level("INFO"), client_patch:
        handler.lambda_handler(api_event(file_body(prompt="secret question")), None)

    assert PNG_B64 not in caplog.text
    assert KEY not in caplog.text
    assert "secret question" not in caplog.text
