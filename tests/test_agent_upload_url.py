import hashlib
import json
import time
from unittest.mock import Mock, patch

import pytest

from src.agent_upload_url import handler

AUTH_COOKIE = "auth_session=" + "B" * 43
PHONE_HASH = "p" * 64
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def api_event(body, authed=True):
    event = {"body": body, "requestContext": {}}
    if authed:
        event["cookies"] = [AUTH_COOKIE]
    return event


def configure(monkeypatch, session_item="default"):
    monkeypatch.setenv("UPLOAD_BUCKET", "test-uploads")
    monkeypatch.setenv("AUTH_TABLE", "auth-table")
    ddb = Mock()
    if session_item == "default":
        session_item = {"phone_hash": {"S": PHONE_HASH}, "expires_at": {"N": str(int(time.time()) + 100)}}
    ddb.get_item.return_value = {"Item": session_item} if session_item else {}
    s3 = Mock()
    s3.generate_presigned_post.return_value = {"url": "https://s3.example/test-uploads", "fields": {"k": "v"}}
    factory = Mock(side_effect=lambda service, **kwargs: {"dynamodb": ddb, "s3": s3}[service])
    return s3, patch.object(handler.boto3, "client", factory)


def body(**overrides):
    value = {"mediaType": "image/png", "size": 1000}
    value.update(overrides)
    return json.dumps(value)


def test_returns_presigned_post_scoped_to_caller(monkeypatch):
    s3, client_patch = configure(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event(body()), None)

    assert result["statusCode"] == 200
    payload = json.loads(result["body"])
    assert payload["url"] == "https://s3.example/test-uploads"
    assert payload["fields"] == {"k": "v"}
    assert payload["expiresIn"] == 300
    owner = hashlib.sha256(PHONE_HASH.encode()).hexdigest()[:32]
    assert payload["key"].startswith(f"u/{owner}/")
    call = s3.generate_presigned_post.call_args.kwargs
    assert call["Bucket"] == "test-uploads"
    assert call["Key"] == payload["key"]
    assert call["ExpiresIn"] == 300
    assert {"Content-Type": "image/png"} in call["Conditions"]
    assert ["content-length-range", 1, 1000] in call["Conditions"]


def test_documents_are_allowed(monkeypatch):
    _, client_patch = configure(monkeypatch)
    with client_patch:
        for media_type in ("application/pdf", "text/plain", "text/csv", DOCX):
            assert handler.lambda_handler(api_event(body(mediaType=media_type)), None)["statusCode"] == 200


def test_upload_up_to_25mb_is_allowed(monkeypatch):
    _, client_patch = configure(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event(body(size=25 * 1024 * 1024)), None)

    assert result["statusCode"] == 200


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "{}",
        body(mediaType="image/bmp"),
        body(mediaType=5),
        body(size=0),
        body(size=25 * 1024 * 1024 + 1),
        body(size="10"),
        body(size=True),
    ],
)
def test_invalid_requests_return_400(monkeypatch, raw):
    s3, client_patch = configure(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(api_event(raw), None)

    assert result["statusCode"] == 400
    s3.generate_presigned_post.assert_not_called()


@pytest.mark.parametrize("authed,item", [(False, "default"), (True, None), (True, {"phone_hash": {"S": "x"}, "expires_at": {"N": "1"}})])
def test_requires_valid_sign_in(monkeypatch, authed, item):
    s3, client_patch = configure(monkeypatch, session_item=item)
    with client_patch:
        result = handler.lambda_handler(api_event(body(), authed=authed), None)

    assert result["statusCode"] == 401
    s3.generate_presigned_post.assert_not_called()


def test_logs_contain_no_identifiers_or_key(monkeypatch, caplog):
    _, client_patch = configure(monkeypatch)
    with caplog.at_level("INFO"), client_patch:
        result = handler.lambda_handler(api_event(body()), None)

    key = json.loads(result["body"])["key"]
    assert key not in caplog.text
    assert PHONE_HASH not in caplog.text
    assert "B" * 43 not in caplog.text
