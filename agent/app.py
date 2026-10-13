import logging
import hashlib
import json
import logging
import os

from bedrock_agentcore import BedrockAgentCoreApp
from langchain_aws import ChatBedrockConverse
from langgraph_checkpoint_aws import AgentCoreMemorySaver

from agent.graph import build_agent, handle_request
from agent.tools import (
    browser_fetcher,
    code_interpreter_factory,
    load_gateway_tools,
    make_browser_tool,
    make_code_tool,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "eu.amazon.nova-pro-v1:0"

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("agent")
app = BedrockAgentCoreApp()
_agent = None


def _region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "eu-west-1"


def _model(region: str) -> ChatBedrockConverse:
    guardrail = None
    if os.environ.get("GUARDRAIL_ID"):
        guardrail = {
            "guardrailIdentifier": os.environ["GUARDRAIL_ID"],
            "guardrailVersion": os.environ.get("GUARDRAIL_VERSION", "DRAFT"),
            "trace": "disabled",
        }
    return ChatBedrockConverse(
        model=os.environ.get("MODEL_ID", DEFAULT_MODEL_ID),
        region_name=region,
        guardrail_config=guardrail,
    )


def get_agent():
    global _agent
    if _agent is None:
        region = _region()
        tools = [make_code_tool(code_interpreter_factory(region)), make_browser_tool(browser_fetcher(region))]
        if os.environ.get("GATEWAY_URL"):
            try:
                tools += load_gateway_tools(os.environ["GATEWAY_URL"], region)
            except Exception:
                logger.exception("Gateway tools unavailable; continuing without them")
        checkpointer = None
        if os.environ.get("MEMORY_ID"):
            checkpointer = AgentCoreMemorySaver(os.environ["MEMORY_ID"], region_name=region)
        _agent = build_agent(_model(region), tools, checkpointer)
    return _agent


@app.entrypoint
async def invoke(payload, context):
    session_id = context.session_id or "default"
    correlation = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    logger.info(json.dumps({"event": "invoke_started", "session": correlation}))
    try:
        result = await handle_request(get_agent(), payload, session_id)
    except Exception as error:
        logger.error(json.dumps({
            "event": "invoke_failed",
            "session": correlation,
            "error_type": type(error).__name__,
        }))
        raise
    logger.info(json.dumps({"event": "invoke_succeeded", "session": correlation}))
    return result


if __name__ == "__main__":
    app.run()
