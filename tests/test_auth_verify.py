import hashlib
import hmac
import json
import time
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from src.auth_verify import handler

PHONE = "+353851234567"
PEPPER = "pepper-value"
CODE = "123456"


def digest(key, message):
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


PHONE_HASH = digest(PEPPER, PHONE)
CODE_HASH = digest(PEPPER, f"{PHONE_HASH}:{CODE}")


def conditional_error(operation):
    return ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, operation)


def configure(monkeypatch, item="default", update_error=None, delete_error=None):
    monkeypatch.setenv("AUTH_TABLE", "auth-table")
    monkeypatch.setenv("AUTH_PEPPER_PARAM", "/test/pepper")
    ssm = Mock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": PEPPER}}
    ddb = Mock()
    if item == "default":
        item = {
            "pk": {"S": f"CODE#{PHONE_HASH}"},
            "code_hash": {"S": CODE_HASH},
            "attempts": {"N": "0"},
            "expires_at": {"N": str(int(time.time()) + 200)},
        }
    ddb.get_item.return_value = {"Item": item} if item else {}
    if update_error:
        ddb.update_item.side_effect = update_error
    if delete_error:
        ddb.delete_item.side_effect = delete_error
    factory = Mock(side_effect=lambda service, **kwargs: {"ssm": ssm, "dynamodb": ddb}[service])
    return ddb, patch.object(handler.boto3, "client", factory)


def event(phone=PHONE, code=CODE):
    return {"body": json.dumps({"phone": phone, "code": code})}


def test_correct_code_creates_session_and_cookie(monkeypatch):
    ddb, client_patch = configure(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(event(), None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"authenticated": True}
    cookie = result["cookies"][0]
    assert cookie.startswith("auth_session=")
    for attribute in ("Path=/;", "Secure", "HttpOnly", "SameSite=Strict", "Max-Age=43200"):
        assert attribute in cookie
    token = cookie.split(";")[0].split("=", 1)[1]
    puts = [c.kwargs["Item"] for c in ddb.put_item.call_args_list]
    session = next(i for i in puts if i["pk"]["S"].startswith("SESSION#"))
    assert session["pk"]["S"] == "SESSION#" + hashlib.sha256(token.encode()).hexdigest()
    assert token not in json.dumps(puts)
    assert session["phone_masked"]["S"] == "+353******567"
    assert session["phone_hash"]["S"] == PHONE_HASH
    assert PHONE not in json.dumps(puts)
    assert int(session["expires_at"]["N"]) - time.time() == pytest.approx(43200, abs=5)
    assert any(i["pk"]["S"] == f"USER#{PHONE_HASH}" for i in puts)


def test_code_is_single_use(monkeypatch):
    ddb, client_patch = configure(monkeypatch)
    with client_patch:
        handler.lambda_handler(event(), None)

    assert ddb.delete_item.call_args.kwargs["Key"] == {"pk": {"S": f"CODE#{PHONE_HASH}"}}


def test_concurrent_reuse_of_code_returns_401(monkeypatch):
    _, client_patch = configure(monkeypatch, delete_error=conditional_error("DeleteItem"))
    with client_patch:
        result = handler.lambda_handler(event(), None)

    assert result["statusCode"] == 401
    assert "cookies" not in result


def test_wrong_code_returns_401_without_cookie(monkeypatch):
    ddb, client_patch = configure(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(event(code="000000"), None)

    assert result["statusCode"] == 401
    assert "cookies" not in result
    ddb.update_item.assert_called_once()


def test_unknown_or_missing_code_returns_401(monkeypatch):
    _, client_patch = configure(monkeypatch, item=None)
    with client_patch:
        result = handler.lambda_handler(event(), None)

    assert result["statusCode"] == 401


def test_expired_code_returns_401(monkeypatch):
    item = {
        "pk": {"S": f"CODE#{PHONE_HASH}"},
        "code_hash": {"S": CODE_HASH},
        "attempts": {"N": "0"},
        "expires_at": {"N": str(int(time.time()) - 1)},
    }
    _, client_patch = configure(monkeypatch, item=item)
    with client_patch:
        result = handler.lambda_handler(event(), None)

    assert result["statusCode"] == 401


def test_locked_after_five_attempts_returns_429_even_with_correct_code(monkeypatch):
    _, client_patch = configure(monkeypatch, update_error=conditional_error("UpdateItem"))
    with client_patch:
        result = handler.lambda_handler(event(), None)

    assert result["statusCode"] == 429
    assert "cookies" not in result


@pytest.mark.parametrize(
    "phone,code",
    [("bad", CODE), (PHONE, "12345"), (PHONE, "abcdef"), (PHONE, 123456), (None, None), (PHONE, "1234567")],
)
def test_malformed_input_returns_400_without_aws_calls(monkeypatch, phone, code):
    _, client_patch = configure(monkeypatch)
    with client_patch as factory:
        result = handler.lambda_handler(event(phone, code), None)

    assert result["statusCode"] == 400
    factory.assert_not_called()


def test_logs_contain_no_phone_code_or_token(monkeypatch, caplog):
    _, client_patch = configure(monkeypatch)
    with client_patch, caplog.at_level("INFO"):
        result = handler.lambda_handler(event(), None)

    token = result["cookies"][0].split(";")[0].split("=", 1)[1]
    for secret in (PHONE, PHONE[1:], CODE, token, PHONE_HASH):
        assert secret not in caplog.text
