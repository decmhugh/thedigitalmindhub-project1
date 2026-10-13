import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from urllib.parse import urlparse

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 4000


def _output_text(result: dict[str, Any]) -> str:
    parts = []
    for event in result.get("stream", []):
        for item in event.get("result", {}).get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
    return "".join(parts)[:MAX_OUTPUT_CHARS] or "(no output)"


def make_code_tool(interpreter_factory: Callable[[], Any]):
    @tool
    def run_python(code: str) -> str:
        """Run Python code in an isolated sandbox and return its output."""
        interpreter = interpreter_factory()
        try:
            return _output_text(interpreter.execute_code(code))
        except Exception:
            logger.exception("Code execution failed")
            return "Code execution failed."
        finally:
            interpreter.stop()

    return run_python


def code_interpreter_factory(region: str) -> Callable[[], Any]:
    from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter

    def factory() -> Any:
        interpreter = CodeInterpreter(region)
        interpreter.start()
        return interpreter

    return factory


def make_browser_tool(fetch_page_text: Callable[[str], str]):
    @tool
    def browse_page(url: str) -> str:
        """Open a public web page in a sandboxed browser and return its visible text."""
        if urlparse(url).scheme not in ("http", "https"):
            return "Only http and https URLs can be browsed."
        try:
            return fetch_page_text(url)[:MAX_OUTPUT_CHARS]
        except Exception:
            logger.exception("Browsing failed")
            return "Browsing failed."

    return browse_page


def browser_fetcher(region: str) -> Callable[[str], str]:
    from bedrock_agentcore.tools.browser_client import BrowserClient
    from playwright.sync_api import sync_playwright

    def fetch(url: str) -> str:
        client = BrowserClient(region)
        client.start()
        try:
            ws_url, headers = client.generate_ws_headers()
            with sync_playwright() as playwright:
                browser = playwright.chromium.connect_over_cdp(ws_url, headers=headers)
                try:
                    page = browser.contexts[0].pages[0] if browser.contexts and browser.contexts[0].pages else browser.new_page()
                    page.goto(url, timeout=30000)
                    return page.inner_text("body")
                finally:
                    browser.close()
        finally:
            client.stop()

    return fetch


class IamSigV4Auth(httpx.Auth):
    requires_request_body = True

    def __init__(self, region: str, service: str = "bedrock-agentcore", session: Any = None):
        self._region = region
        self._service = service
        self._session = session or boto3.Session()

    def auth_flow(self, request: httpx.Request):
        credentials = self._session.get_credentials().get_frozen_credentials()
        aws_request = AWSRequest(method=request.method, url=str(request.url), data=request.content)
        SigV4Auth(credentials, self._service, self._region).add_auth(aws_request)
        for name, value in aws_request.headers.items():
            request.headers[name] = value
        yield request


def load_gateway_tools(gateway_url: str, region: str) -> list:
    client = MultiServerMCPClient(
        {"gateway": {"transport": "streamable_http", "url": gateway_url, "auth": IamSigV4Auth(region)}}
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(client.get_tools())
    # A loop is already running in this thread, so load the tools on a separate thread.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, client.get_tools()).result()
