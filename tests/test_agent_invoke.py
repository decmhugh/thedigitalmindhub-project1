import hashlib
import json
import time
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from src.agent_invoke import handler

SESSION = "a" * 36
AUTH_TOKEN = "B" * 43
AUTH_COOKIE = f"auth_session={AUTH_TOKEN}"
PHONE_HASH = "p" * 64
OWNER_PREFIX = "u/" + hashlib.sha256(PHONE_HASH.encode()).hexdigest()[:32] + "/"
KEY = OWNER_PREFIX + "a" * 32


def api_event(body, cookie=None, authed=True):
    event = {"body": body, "requestContext": {}}
    cookies = ([AUTH_COOKIE] if authed else []) + ([cookie] if cookie else [])
    if cookies:
        event["cookies"] = cookies
    return event


def configure(monkeypatch, session_item="default", lock_busy=False, lambda_error=None):
    monkeypatch.setenv("AUTH_TABLE", "auth-table")
    monkeypatch.setenv("JOBS_TABLE", "jobs-table")
    monkeypatch.setenv("WORKER_FUNCTION_NAME", "worker-fn")
    ddb = Mock()
    if session_item == "default":
        session_item = {"phone_hash": {"S": PHONE_HASH}, "expires_at": {"N": str(int(time.time()) + 100)}}
    ddb.get_item.return_value = {"Item": session_item} if session_item else {}
    if lock_busy:
        ddb.put_item.side_effect = ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
    lam = Mock()
    if lambda_error:
        lam.invoke.side_effect = lambda_error
    factory = Mock(side_effect=lambda service, **kwargs: {"dynamodb": ddb, "lambda": lam}[service])
    ddb.lam = lam
    return ddb, patch.object(handler.boto3, "client", factory)


def worker_payload(ddb):
    return json.loads(ddb.lam.invoke.call_args.kwargs["Payload"])


def test_valid_request_returns_202_with_job_id_and_starts_worker_async(monkeypatch):
    ddb, patched = configure(monkeypatch)
    with patched:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 202
    job_id = json.loads(result["body"])["job_id"]
    assert len(job_id) == 32
    call = ddb.lam.invoke.call_args.kwargs
    assert call["FunctionName"] == "worker-fn" and call["InvocationType"] == "Event"
    payload = worker_payload(ddb)
    assert payload["job_id"] == job_id and payload["user"] == PHONE_HASH and payload["prompt"] == "hello"
    assert payload["lock_pk"].startswith("LOCK#")
    job_item = [c.kwargs["Item"] for c in ddb.put_item.call_args_list if c.kwargs["Item"]["pk"]["S"].startswith("JOB#")][0]
    assert job_item["owner"] == {"S": PHONE_HASH} and job_item["status"] == {"S": "pending"}


def test_first_request_sets_browser_session_cookie_without_exposing_id(monkeypatch):
    ddb, patched = configure(monkeypatch)
    with patched:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    cookie = result["cookies"][0]
    assert cookie.startswith("agent_session=")
    for attribute in ("Path=/agent", "Secure", "HttpOnly", "SameSite=Strict"):
        assert attribute in cookie
    session_id = cookie.split(";")[0].split("=", 1)[1]
    assert session_id not in result["body"] and worker_payload(ddb)["session_id"] == session_id


def test_existing_cookie_session_is_reused_without_new_cookie(monkeypatch):
    ddb, patched = configure(monkeypatch)
    with patched:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', f"agent_session={SESSION}"), None)

    assert "cookies" not in result and worker_payload(ddb)["session_id"] == SESSION


def test_new_session_flag_rotates_cookie(monkeypatch):
    ddb, patched = configure(monkeypatch)
    with patched:
        result = handler.lambda_handler(api_event('{"prompt":"hi","new_session":true}', f"agent_session={SESSION}"), None)

    assert worker_payload(ddb)["session_id"] != SESSION and result["cookies"]


@pytest.mark.parametrize("body", ["not json", "{}", '{"prompt":"  "}', "null", '{"prompt":"hi","new_session":"yes"}'])
def test_invalid_body_returns_400_without_starting_worker(monkeypatch, body):
    ddb, patched = configure(monkeypatch)
    with patched:
        result = handler.lambda_handler(api_event(body), None)

    assert result["statusCode"] == 400
    ddb.lam.invoke.assert_not_called()
    ddb.put_item.assert_not_called()


@pytest.mark.parametrize(
    "file",
    ["x", {}, {"key": 5}, {"key": ""}, {"key": "u/" + "0" * 32 + "/" + "a" * 32}, {"key": OWNER_PREFIX + "../x"}, {"key": OWNER_PREFIX}],
)
def test_invalid_or_foreign_key_returns_400_without_starting_worker(monkeypatch, file):
    ddb, patched = configure(monkeypatch)
    with patched:
        result = handler.lambda_handler(api_event(json.dumps({"prompt": "hi", "file": file})), None)

    assert result["statusCode"] == 400
    ddb.lam.invoke.assert_not_called()


def test_file_job_carries_key_and_truncated_name_never_bytes(monkeypatch):
    ddb, patched = configure(monkeypatch)
    body = json.dumps({"file": {"key": KEY, "name": "n" * 300}})
    with patched:
        result = handler.lambda_handler(api_event(body), None)

    assert result["statusCode"] == 202
    payload = worker_payload(ddb)
    assert payload["prompt"] == "Describe this file."
    assert payload["file"] == {"key": KEY, "name": "n" * 100}


def test_missing_auth_cookie_returns_401_without_any_aws_call(monkeypatch):
    _, patched = configure(monkeypatch)
    with patched as factory:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', authed=False), None)

    assert result["statusCode"] == 401 and "cookies" not in result
    factory.assert_not_called()


@pytest.mark.parametrize("session_item", [None, {"phone_hash": {"S": "x"}, "expires_at": {"N": "1"}}])
def test_unknown_or_expired_session_returns_401_without_starting_worker(monkeypatch, session_item):
    ddb, patched = configure(monkeypatch, session_item=session_item)
    with patched:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 401
    ddb.lam.invoke.assert_not_called()


def test_second_request_while_job_runs_returns_409(monkeypatch):
    ddb, patched = configure(monkeypatch, lock_busy=True)
    with patched:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}', f"agent_session={SESSION}"), None)

    assert result["statusCode"] == 409
    ddb.lam.invoke.assert_not_called()


def test_lock_is_conditional_on_absent_or_expired(monkeypatch):
    ddb, patched = configure(monkeypatch)
    with patched:
        handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    lock_call = ddb.put_item.call_args_list[0].kwargs
    assert lock_call["Item"]["pk"]["S"].startswith("LOCK#")
    assert "attribute_not_exists(pk)" in lock_call["ConditionExpression"]


def test_worker_start_failure_releases_lock_and_returns_502(monkeypatch):
    error = ClientError({"Error": {"Code": "ServiceException", "Message": "secret"}}, "Invoke")
    ddb, patched = configure(monkeypatch, lambda_error=error)
    with patched:
        result = handler.lambda_handler(api_event('{"prompt":"hello"}'), None)

    assert result["statusCode"] == 502 and "secret" not in result["body"]
    assert ddb.delete_item.call_args.kwargs["Key"]["pk"]["S"].startswith("LOCK#")


def test_logs_do_not_contain_session_prompt_or_filename(monkeypatch, caplog):
    _, patched = configure(monkeypatch)
    body = json.dumps({"prompt": "secret prompt", "file": {"key": KEY, "name": "secret-name.pdf"}})
    with patched, caplog.at_level("INFO"):
        handler.lambda_handler(api_event(body, f"agent_session={SESSION}"), None)

    for secret in (SESSION, "secret prompt", "secret-name", KEY, PHONE_HASH):
        assert secret not in caplog.text
    assert '"status": 202' in caplog.text
