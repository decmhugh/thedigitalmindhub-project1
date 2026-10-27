import json
import re
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from src.auth_start import handler

PHONE = "+353851234567"


def configure(monkeypatch, put_error=None, sms_error=None):
    monkeypatch.setenv("AUTH_TABLE", "auth-table")
    monkeypatch.setenv("AUTH_PEPPER_PARAM", "/test/pepper")
    monkeypatch.setenv("SMS_SENDER_ID", "SERV")
    ssm = Mock()
    ssm.get_parameter.return_value = {"Parameter": {"Value": "pepper-value"}}
    ddb = Mock()
    if put_error:
        ddb.put_item.side_effect = put_error
    sms = Mock()
    if sms_error:
        sms.send_text_message.side_effect = sms_error
    clients = {"ssm": ssm, "dynamodb": ddb, "pinpoint-sms-voice-v2": sms}
    factory = Mock(side_effect=lambda service, **kwargs: clients[service])
    return ddb, sms, patch.object(handler.boto3, "client", factory)


def event(body):
    return {"body": json.dumps(body)}


def test_valid_number_stores_hashed_code_and_sends_sms(monkeypatch):
    ddb, sms, client_patch = configure(monkeypatch)
    with client_patch:
        result = handler.lambda_handler(event({"phone": PHONE}), None)

    assert result["statusCode"] == 200
    assert json.loads(result["body"]) == {"status": "code_sent"}
    call = sms.send_text_message.call_args.kwargs
    assert call["DestinationPhoneNumber"] == PHONE
    assert call["OriginationIdentity"] == "SERV"
    assert call["MessageType"] == "TRANSACTIONAL"
    code = re.search(r"\b(\d{6})\b", call["MessageBody"]).group(1)
    item = ddb.put_item.call_args.kwargs["Item"]
    assert item["pk"]["S"].startswith("CODE#")
    assert PHONE not in json.dumps(item) and code not in json.dumps(item)
    assert int(item["expires_at"]["N"]) - int(item["sent_at"]["N"]) == 300
    assert item["attempts"]["N"] == "0"


@pytest.mark.parametrize("phone", ["0851234567", "+0123456789", "+35385", "abc", "", None, 123, "+353851234567; drop"])
def test_invalid_number_returns_400_and_sends_nothing(monkeypatch, phone):
    ddb, sms, client_patch = configure(monkeypatch)
    with client_patch as factory:
        result = handler.lambda_handler(event({"phone": phone}), None)

    assert result["statusCode"] == 400
    sms.send_text_message.assert_not_called()
    factory.assert_not_called()


def test_invalid_json_returns_400(monkeypatch):
    _, sms, client_patch = configure(monkeypatch)
    with client_patch:
        result = handler.lambda_handler({"body": "not json"}, None)

    assert result["statusCode"] == 400
    sms.send_text_message.assert_not_called()


def test_resend_cooldown_returns_429_without_sms(monkeypatch):
    error = ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "PutItem")
    _, sms, client_patch = configure(monkeypatch, put_error=error)
    with client_patch:
        result = handler.lambda_handler(event({"phone": PHONE}), None)

    assert result["statusCode"] == 429
    sms.send_text_message.assert_not_called()


def test_sms_failure_returns_generic_502(monkeypatch):
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "secret details"}}, "SendTextMessage")
    _, _, client_patch = configure(monkeypatch, sms_error=error)
    with client_patch:
        result = handler.lambda_handler(event({"phone": PHONE}), None)

    assert result["statusCode"] == 502
    assert "secret details" not in result["body"]


def test_logs_contain_no_phone_or_code(monkeypatch, caplog):
    _, sms, client_patch = configure(monkeypatch)
    with client_patch, caplog.at_level("INFO"):
        handler.lambda_handler(event({"phone": PHONE}), None)

    code = re.search(r"\b(\d{6})\b", sms.send_text_message.call_args.kwargs["MessageBody"]).group(1)
    assert PHONE not in caplog.text and PHONE[1:] not in caplog.text
    assert code not in caplog.text
