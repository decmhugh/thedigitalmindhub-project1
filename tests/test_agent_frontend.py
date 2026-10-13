import io
import json
from pathlib import Path
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from src.agent_frontend import handler


def test_returns_configured_html_object(monkeypatch):
    monkeypatch.setenv("AGENT_HTML_BUCKET", "demo-agent-ui")
    monkeypatch.setenv("AGENT_HTML_KEY", "agent.html")
    s3 = Mock()
    s3.get_object.return_value = {"Body": io.BytesIO(b"<html>demo</html>")}

    with patch.object(handler.boto3, "client", return_value=s3):
        result = handler.lambda_handler({}, None)

    assert result["statusCode"] == 200
    assert result["headers"]["content-type"] == "text/html; charset=utf-8"
    assert result["headers"]["cache-control"] == "no-store"
    assert result["body"] == "<html>demo</html>"
    s3.get_object.assert_called_once_with(Bucket="demo-agent-ui", Key="agent.html")


def test_s3_failure_returns_generic_gateway_error(monkeypatch):
    monkeypatch.setenv("AGENT_HTML_BUCKET", "demo-agent-ui")
    monkeypatch.setenv("AGENT_HTML_KEY", "agent.html")
    s3 = Mock()
    s3.get_object.side_effect = ClientError(
        {"Error": {"Code": "NoSuchKey", "Message": "private bucket details"}},
        "GetObject",
    )

    with patch.object(handler.boto3, "client", return_value=s3):
        result = handler.lambda_handler({}, None)

    assert result["statusCode"] == 502
    assert result["headers"]["content-type"] == "application/json"
    assert json.loads(result["body"]) == {"error": "Unable to load demo page"}
    assert "private bucket details" not in result["body"]
