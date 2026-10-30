import json
import time
from unittest.mock import Mock, patch

import pytest

from src.agent_job_status import handler

AUTH_TOKEN = "B" * 43
PHONE_HASH = "p" * 64
JOB_ID = "c" * 32


def event(job_id=JOB_ID, authed=True):
    value = {"pathParameters": {"job_id": job_id}}
    if authed:
        value["cookies"] = [f"auth_session={AUTH_TOKEN}"]
    return value


def configure(monkeypatch, job=None, owner=PHONE_HASH, session_item="default"):
    monkeypatch.setenv("AUTH_TABLE", "auth-table")
    monkeypatch.setenv("JOBS_TABLE", "jobs-table")
    ddb = Mock()
    session = {"phone_hash": {"S": PHONE_HASH}, "expires_at": {"N": str(int(time.time()) + 100)}}
    if session_item != "default":
        session = session_item
    item = None
    if job is not None:
        item = {"owner": {"S": owner}, "created_at": {"N": str(int(time.time()))}, "expires_at": {"N": str(int(time.time()) + 100)}}
        item.update(job)

    def get_item(TableName, Key):
        if TableName == "auth-table":
            return {"Item": session} if session else {}
        return {"Item": item} if item else {}

    ddb.get_item.side_effect = get_item
    return ddb, patch.object(handler.boto3, "client", Mock(return_value=ddb))


def call(monkeypatch, **kwargs):
    ddb, patched = configure(monkeypatch, **kwargs)
    with patched:
        result = handler.lambda_handler(event(), None)
    return result["statusCode"], json.loads(result["body"])


def test_unauthenticated_returns_401_without_aws_call(monkeypatch):
    _, patched = configure(monkeypatch)
    with patched as factory:
        result = handler.lambda_handler(event(authed=False), None)

    assert result["statusCode"] == 401
    factory.assert_not_called()


def test_unknown_expired_and_malformed_ids_return_404(monkeypatch):
    assert call(monkeypatch, job=None)[0] == 404
    ddb, patched = configure(monkeypatch, job={"status": {"S": "done"}, "response": {"S": "x"}})
    with patched:
        assert handler.lambda_handler(event("not-a-job-id"), None)["statusCode"] == 404
    assert call(monkeypatch, job={"status": {"S": "done"}, "response": {"S": "x"}, "expires_at": {"N": "1"}})[0] == 404


def test_foreign_job_returns_404_not_403(monkeypatch):
    status, body = call(monkeypatch, job={"status": {"S": "done"}, "response": {"S": "secret"}}, owner="q" * 64)

    assert status == 404 and "secret" not in json.dumps(body)


@pytest.mark.parametrize("state", ["pending", "running"])
def test_in_progress_states(monkeypatch, state):
    assert call(monkeypatch, job={"status": {"S": state}}) == (200, {"status": state})


def test_done_returns_response_and_blocked_has_no_text(monkeypatch):
    assert call(monkeypatch, job={"status": {"S": "done"}, "response": {"S": "hi"}}) == (200, {"status": "done", "response": "hi"})
    status, body = call(monkeypatch, job={"status": {"S": "done"}, "blocked": {"BOOL": True}})
    assert (status, body) == (200, {"status": "done", "blocked": True})


def test_failed_returns_generic_error_and_http_status(monkeypatch):
    job = {"status": {"S": "failed"}, "error": {"S": "Agent request timed out"}, "http_status": {"N": "504"}}
    assert call(monkeypatch, job=job) == (200, {"status": "failed", "error": "Agent request timed out", "http_status": 504})


def test_stale_running_job_is_reported_failed(monkeypatch):
    job = {"status": {"S": "running"}, "created_at": {"N": str(int(time.time()) - 400)}}
    status, body = call(monkeypatch, job=job)

    assert status == 200 and body["status"] == "failed" and body["http_status"] == 502
