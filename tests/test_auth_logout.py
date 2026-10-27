import hashlib
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from src.auth_logout import handler

TOKEN = "A" * 43


def configure(monkeypatch, error=None):
    monkeypatch.setenv("AUTH_TABLE", "auth-table")
    ddb = Mock()
    if error:
        ddb.delete_item.side_effect = error
    return ddb, patch.object(handler.boto3, "client", Mock(return_value=ddb))


def test_logout_deletes_session_and_clears_cookie(monkeypatch):
    ddb, client_patch = configure(monkeypatch)
    with client_patch:
        result = handler.lambda_handler({"cookies": [f"auth_session={TOKEN}"]}, None)

    assert result["statusCode"] == 204
    assert result["body"] == ""
    assert ddb.delete_item.call_args.kwargs["Key"] == {"pk": {"S": "SESSION#" + hashlib.sha256(TOKEN.encode()).hexdigest()}}
    cookie = result["cookies"][0]
    assert cookie.startswith("auth_session=;")
    assert "Max-Age=0" in cookie and "HttpOnly" in cookie and "Secure" in cookie


def test_logout_without_cookie_is_idempotent(monkeypatch):
    ddb, client_patch = configure(monkeypatch)
    with client_patch:
        result = handler.lambda_handler({}, None)

    assert result["statusCode"] == 204
    ddb.delete_item.assert_not_called()
    assert "Max-Age=0" in result["cookies"][0]


def test_logout_still_clears_cookie_when_delete_fails(monkeypatch):
    error = ClientError({"Error": {"Code": "InternalServerError", "Message": "x"}}, "DeleteItem")
    _, client_patch = configure(monkeypatch, error=error)
    with client_patch:
        result = handler.lambda_handler({"cookies": [f"auth_session={TOKEN}"]}, None)

    assert result["statusCode"] == 204
    assert "Max-Age=0" in result["cookies"][0]
