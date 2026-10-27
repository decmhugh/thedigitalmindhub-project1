import hashlib
import json
import time
from unittest.mock import Mock, patch

from src.auth_session import handler

TOKEN = "A" * 43


def configure(monkeypatch, item):
    monkeypatch.setenv("AUTH_TABLE", "auth-table")
    ddb = Mock()
    ddb.get_item.return_value = {"Item": item} if item else {}
    return ddb, patch.object(handler.boto3, "client", Mock(return_value=ddb))


def session_item(expires_in=100):
    return {
        "pk": {"S": "SESSION#x"},
        "phone_hash": {"S": "hash"},
        "phone_masked": {"S": "+353******567"},
        "expires_at": {"N": str(int(time.time()) + expires_in)},
    }


def test_valid_session_returns_masked_number(monkeypatch):
    ddb, client_patch = configure(monkeypatch, session_item())
    with client_patch:
        result = handler.lambda_handler({"cookies": [f"auth_session={TOKEN}"]}, None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"authenticated": True, "phone_masked": "+353******567"}
    key = ddb.get_item.call_args.kwargs["Key"]
    assert key == {"pk": {"S": "SESSION#" + hashlib.sha256(TOKEN.encode()).hexdigest()}}


def test_missing_cookie_returns_401_without_aws_call(monkeypatch):
    ddb, client_patch = configure(monkeypatch, None)
    with client_patch:
        result = handler.lambda_handler({}, None)

    assert result["statusCode"] == 401
    assert json.loads(result["body"]) == {"authenticated": False}
    ddb.get_item.assert_not_called()


def test_unknown_session_returns_401(monkeypatch):
    _, client_patch = configure(monkeypatch, None)
    with client_patch:
        result = handler.lambda_handler({"cookies": [f"auth_session={TOKEN}"]}, None)

    assert result["statusCode"] == 401


def test_expired_session_returns_401(monkeypatch):
    _, client_patch = configure(monkeypatch, session_item(expires_in=-1))
    with client_patch:
        result = handler.lambda_handler({"cookies": [f"auth_session={TOKEN}"]}, None)

    assert result["statusCode"] == 401


def test_malformed_cookie_returns_401_without_aws_call(monkeypatch):
    ddb, client_patch = configure(monkeypatch, session_item())
    with client_patch:
        result = handler.lambda_handler({"cookies": ["auth_session=short"]}, None)

    assert result["statusCode"] == 401
    ddb.get_item.assert_not_called()
