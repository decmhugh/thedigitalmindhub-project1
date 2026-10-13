import asyncio
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from botocore.credentials import Credentials

from agent import tools


def test_sigv4_auth_signs_requests_for_agentcore():
    session = Mock()
    session.get_credentials.return_value = Credentials("AKIATEST", "secret", "token-123")
    auth = tools.IamSigV4Auth("eu-west-1", session=session)
    request = httpx.Request("POST", "https://gw.gateway.bedrock-agentcore.eu-west-1.amazonaws.com/mcp", json={"a": 1})

    signed = next(auth.auth_flow(request))

    assert signed.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIATEST/")
    assert "/eu-west-1/bedrock-agentcore/aws4_request" in signed.headers["authorization"]
    assert signed.headers["x-amz-security-token"] == "token-123"
    assert "x-amz-date" in signed.headers


def test_gateway_tools_are_loaded_with_signed_connection():
    client = Mock()
    client.get_tools = AsyncMock(return_value=["hello-tool"])
    with patch.object(tools, "MultiServerMCPClient", return_value=client) as client_class:
        loaded = tools.load_gateway_tools("https://gw.example/mcp", "eu-west-1")

    assert loaded == ["hello-tool"]
    connection = client_class.call_args.args[0]["gateway"]
    assert connection["transport"] == "streamable_http"
    assert connection["url"] == "https://gw.example/mcp"
    assert isinstance(connection["auth"], tools.IamSigV4Auth)


def test_gateway_tools_load_inside_a_running_event_loop():
    client = Mock()
    client.get_tools = AsyncMock(return_value=["hello-tool"])

    async def load():
        with patch.object(tools, "MultiServerMCPClient", return_value=client):
            return tools.load_gateway_tools("https://gw.example/mcp", "eu-west-1")

    assert asyncio.run(load()) == ["hello-tool"]


def test_browser_tool_returns_page_text():
    fetch = Mock(return_value="Example Domain")
    tool = tools.make_browser_tool(fetch)

    assert tool.invoke({"url": "https://example.com/page"}) == "Example Domain"
    fetch.assert_called_once_with("https://example.com/page")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "javascript:alert(1)", "ftp://host/x", "example.com", ""])
def test_browser_tool_rejects_non_http_urls(url):
    fetch = Mock()
    tool = tools.make_browser_tool(fetch)

    assert "http" in tool.invoke({"url": url}).lower()
    fetch.assert_not_called()


def test_browser_tool_truncates_long_pages():
    tool = tools.make_browser_tool(Mock(return_value="x" * (tools.MAX_OUTPUT_CHARS + 500)))

    assert len(tool.invoke({"url": "https://example.com"})) == tools.MAX_OUTPUT_CHARS


def test_browser_tool_failure_returns_generic_message():
    tool = tools.make_browser_tool(Mock(side_effect=RuntimeError("secret internal detail")))

    output = tool.invoke({"url": "https://example.com"})

    assert "secret internal detail" not in output
    assert "failed" in output.lower()
