import base64
import hashlib
import io
import json
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from src.agent_invoke_worker import handler

RUNTIME_ARN = "arn:aws:bedrock-agentcore:eu-west-1:123456789012:runtime/test"
SESSION = "a" * 36
PHONE_HASH = "p" * 64
PNG_BYTES = b"\x89PNG fake image bytes"
PNG_B64 = base64.b64encode(PNG_BYTES).decode()
OWNER_PREFIX = "u/" + hashlib.sha256(PHONE_HASH.encode()).hexdigest()[:32] + "/"
KEY = OWNER_PREFIX + "a" * 32


def job(**overrides):
    value = {"job_id": "j" * 32, "user": PHONE_HASH, "session_id": SESSION, "prompt": "hello", "lock_pk": "LOCK#x"}
    value.update(overrides)
    return value


def configure(monkeypatch, runtime_response=None, runtime_error=None):
    monkeypatch.setenv("AGENT_RUNTIME_ARN_PARAM", "/test/agent/runtime-arn")
    monkeypatch.setenv("JOBS_TABLE", "jobs-table")
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    s3 = Mock()
    s3.head_object.return_value = {"ContentType": "image/png", "ContentLength": len(PNG_BYTES)}
    s3.get_object.return_value = {"Body": io.BytesIO(PNG_BYTES)}
    ssm = Mock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": RUNTIME_ARN}}
    runtime = Mock()
    if runtime_error:
        runtime.invoke_agent_runtime.side_effect = runtime_error
    else:
        runtime.invoke_agent_runtime.return_value = runtime_response or {"response": io.BytesIO(b'{"response":"Hello from agent"}')}
    ddb = Mock()
    factory = Mock(side_effect=lambda service, **kwargs: {"ssm": ssm, "bedrock-agentcore": runtime, "dynamodb": ddb, "s3": s3}[service])
    runtime.s3, runtime.ddb = s3, ddb
    return runtime, patch.object(handler.boto3, "client", factory)


def runtime_session(session_id):
    return hashlib.sha256(f"{PHONE_HASH}:{session_id}".encode()).hexdigest()


def test_success_returns_200_and_binds_runtime_session_to_user(monkeypatch):
    runtime, patched = configure(monkeypatch)
    with patched:
        status, body = handler.process(job())

    assert (status, body) == (200, {"response": "Hello from agent"})
    call = runtime.invoke_agent_runtime.call_args.kwargs
    assert call["agentRuntimeArn"] == RUNTIME_ARN and call["runtimeSessionId"] == runtime_session(SESSION)
    assert json.loads(call["payload"]) == {"prompt": "hello"}


def test_file_is_read_from_s3_and_forwarded_with_name(monkeypatch):
    runtime, patched = configure(monkeypatch)
    with patched:
        status, _ = handler.process(job(file={"key": KEY, "name": "Report.png"}))

    assert status == 200
    runtime.s3.get_object.assert_called_once_with(Bucket="test-uploads", Key=KEY)
    payload = json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload["file"] == {"mediaType": "image/png", "data": PNG_B64, "kind": "image", "name": "Report.png"}


def test_documents_are_forwarded_as_document_kind(monkeypatch):
    runtime, patched = configure(monkeypatch)
    runtime.s3.head_object.return_value = {"ContentType": "application/pdf", "ContentLength": 10}
    with patched:
        handler.process(job(file={"key": KEY}))

    assert json.loads(runtime.invoke_agent_runtime.call_args.kwargs["payload"])["file"]["kind"] == "document"


@pytest.mark.parametrize("file", [{"key": "u/" + "0" * 32 + "/" + "a" * 32}, {"key": "bad"}, {}])
def test_foreign_or_invalid_key_is_rejected_without_s3_or_runtime(monkeypatch, file):
    runtime, patched = configure(monkeypatch)
    with patched:
        status, _ = handler.process(job(file=file))

    assert status == 400
    runtime.s3.head_object.assert_not_called()
    runtime.invoke_agent_runtime.assert_not_called()


def test_missing_object_and_unsupported_type_return_400(monkeypatch):
    runtime, patched = configure(monkeypatch)
    runtime.s3.head_object.side_effect = ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
    with patched:
        assert handler.process(job(file={"key": KEY}))[0] == 400
    runtime, patched = configure(monkeypatch)
    runtime.s3.head_object.return_value = {"ContentType": "image/bmp", "ContentLength": 10}
    with patched:
        assert handler.process(job(file={"key": KEY}))[0] == 400
    runtime.invoke_agent_runtime.assert_not_called()


@pytest.mark.parametrize(
    "content_type,size",
    [("image/png", 3_750_001), ("application/pdf", 4_500_001), ("text/plain", 25 * 1024 * 1024)],
)
def test_file_over_model_limit_returns_413_without_reading_or_invoking(monkeypatch, content_type, size):
    runtime, patched = configure(monkeypatch)
    runtime.s3.head_object.return_value = {"ContentType": content_type, "ContentLength": size}
    with patched:
        status, body = handler.process(job(file={"key": KEY}))

    assert (status, body) == (413, {"error": "File exceeds the model size limit"})
    runtime.s3.get_object.assert_not_called()
    runtime.invoke_agent_runtime.assert_not_called()


def test_runtime_size_error_maps_to_413(monkeypatch):
    error = ClientError({"Error": {"Code": "ValidationException", "Message": "Input is too long"}}, "InvokeAgentRuntime")
    _, patched = configure(monkeypatch, runtime_error=error)
    with patched:
        assert handler.process(job())[0] == 413


def test_timeout_maps_to_504_without_internal_details(monkeypatch):
    _, patched = configure(monkeypatch, runtime_error=ReadTimeoutError(endpoint_url="https://runtime.example"))
    with patched:
        status, body = handler.process(job())

    assert status == 504 and "runtime.example" not in json.dumps(body)


def test_failure_maps_to_502_and_guardrail_to_422_without_details(monkeypatch):
    error = ClientError({"Error": {"Code": "InternalServerException", "Message": "secret details"}}, "InvokeAgentRuntime")
    _, patched = configure(monkeypatch, runtime_error=error)
    with patched:
        status, body = handler.process(job())
    assert status == 502 and "secret details" not in json.dumps(body)

    error = ClientError({"Error": {"Code": "GuardrailIntervened", "Message": "blocked content"}}, "InvokeAgentRuntime")
    _, patched = configure(monkeypatch, runtime_error=error)
    with patched:
        status, body = handler.process(job())
    assert status == 422 and "blocked content" not in json.dumps(body)


def test_runtime_reported_block_returns_422_without_text(monkeypatch):
    blocked = {"response": io.BytesIO(b'{"response":"policy text","blocked":true}')}
    _, patched = configure(monkeypatch, runtime_response=blocked)
    with patched:
        status, body = handler.process(job())

    assert status == 422 and "policy text" not in json.dumps(body)


def test_runtime_error_log_has_http_status_request_id_and_hint_but_no_secrets(monkeypatch, caplog):
    error = ClientError(
        {"Error": {"Code": "RuntimeClientError", "Message": "secret details"}, "ResponseMetadata": {"HTTPStatusCode": 424, "RequestId": "req-123"}},
        "InvokeAgentRuntime",
    )
    _, patched = configure(monkeypatch, runtime_error=error)
    with patched, caplog.at_level("INFO"):
        handler.process(job(prompt="secret prompt"))

    for expected in ('"http_status": 424', '"request_id": "req-123"', "/aws/bedrock-agentcore/runtimes/"):
        assert expected in caplog.text
    for secret in ("secret details", "secret prompt", SESSION, PHONE_HASH):
        assert secret not in caplog.text


def test_missing_configuration_is_logged_as_error_type(monkeypatch, caplog):
    _, patched = configure(monkeypatch)
    monkeypatch.delenv("AGENT_RUNTIME_ARN_PARAM", raising=False)
    with patched, caplog.at_level("INFO"):
        status, _ = handler.process(job())

    assert status == 502 and "KeyError" in caplog.text


def updates(ddb):
    return [c.kwargs for c in ddb.update_item.call_args_list]


def test_handler_marks_done_stores_response_and_clears_lock(monkeypatch):
    runtime, patched = configure(monkeypatch)
    with patched:
        handler.lambda_handler(job(), None)

    last = updates(runtime.ddb)[-1]
    assert last["Key"] == {"pk": {"S": "JOB#" + "j" * 32}}
    assert last["ExpressionAttributeValues"][":status"] == {"S": "done"}
    assert last["ExpressionAttributeValues"][":response"] == {"S": "Hello from agent"}
    assert int(last["ExpressionAttributeValues"][":expires"]["N"]) > 0
    assert updates(runtime.ddb)[0]["ExpressionAttributeValues"][":running"] == {"S": "running"}
    assert runtime.ddb.delete_item.call_args.kwargs["Key"] == {"pk": {"S": "LOCK#x"}}


def test_result_update_aliases_reserved_words(monkeypatch):
    runtime, patched = configure(monkeypatch)
    with patched:
        handler.lambda_handler(job(), None)

    last = updates(runtime.ddb)[-1]
    assert last["ExpressionAttributeNames"]["#r"] == "response"
    assert " response " not in last["UpdateExpression"]
    assert last["UpdateExpression"].count("#") >= 5


def test_handler_marks_blocked_done_without_text_and_failures_failed(monkeypatch):
    blocked = {"response": io.BytesIO(b'{"response":"policy text","blocked":true}')}
    runtime, patched = configure(monkeypatch, runtime_response=blocked)
    with patched:
        handler.lambda_handler(job(), None)
    values = updates(runtime.ddb)[-1]["ExpressionAttributeValues"]
    assert values[":status"] == {"S": "done"} and values[":blocked"] == {"BOOL": True} and ":response" not in values

    error = ClientError({"Error": {"Code": "InternalServerException", "Message": "x"}}, "InvokeAgentRuntime")
    runtime, patched = configure(monkeypatch, runtime_error=error)
    with patched:
        handler.lambda_handler(job(), None)
    values = updates(runtime.ddb)[-1]["ExpressionAttributeValues"]
    assert values[":status"] == {"S": "failed"} and values[":error"] == {"S": "Agent request failed"} and values[":http"] == {"N": "502"}
    assert runtime.ddb.delete_item.called


def test_unexpected_crash_still_fails_job_and_clears_lock(monkeypatch):
    runtime, patched = configure(monkeypatch)
    with patched, patch.object(handler, "process", side_effect=RuntimeError("boom")):
        handler.lambda_handler(job(), None)

    assert updates(runtime.ddb)[-1]["ExpressionAttributeValues"][":status"] == {"S": "failed"}
    assert runtime.ddb.delete_item.called
