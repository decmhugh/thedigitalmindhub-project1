import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from agent.graph import build_agent
from agent.graph import handle_request as _handle_request
from agent.tools import make_code_tool


def handle_request(agent, payload, session_id):
    return asyncio.run(_handle_request(agent, payload, session_id))


class ScriptedModel(BaseChatModel):
    replies: list = Field(default_factory=list)
    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls.append([str(m.content) for m in messages])
        reply = self.replies[len(self.calls) - 1]
        return ChatResult(generations=[ChatGeneration(message=reply)])


def interpreter_returning(text):
    interpreter = Mock()
    interpreter.execute_code.return_value = {"stream": [{"result": {"content": [{"type": "text", "text": text}]}}]}
    return interpreter


def make_agent(replies, interpreter=None):
    model = ScriptedModel(replies=replies)
    tool = make_code_tool(lambda: interpreter or interpreter_returning(""))
    return build_agent(model, [tool]), model


def test_agent_remembers_earlier_turns_in_same_session():
    agent, model = make_agent([AIMessage("Nice to meet you"), AIMessage("Your name is Sam")])

    handle_request(agent, {"prompt": "My name is Sam"}, "session-a")
    handle_request(agent, {"prompt": "What is my name?"}, "session-a")

    assert any("My name is Sam" in part for part in model.calls[1])


def test_sessions_do_not_share_memory():
    agent, model = make_agent([AIMessage("Hello Sam"), AIMessage("I do not know")])

    handle_request(agent, {"prompt": "My name is Sam"}, "session-a")
    handle_request(agent, {"prompt": "What is my name?"}, "session-b")

    assert not any("My name is Sam" in part for part in model.calls[1])


def test_agent_uses_code_tool_when_model_requests_it():
    interpreter = interpreter_returning("4\n")
    call = AIMessage(content="", tool_calls=[{"name": "run_python", "args": {"code": "print(2+2)"}, "id": "call-1"}])
    agent, _ = make_agent([call, AIMessage("The answer is 4")], interpreter)

    result = handle_request(agent, {"prompt": "What is 2+2?"}, "session-a")

    assert result == {"response": "The answer is 4"}
    interpreter.execute_code.assert_called_once_with("print(2+2)")
    interpreter.stop.assert_called_once()


def test_code_tool_failure_returns_generic_message_without_details():
    interpreter = Mock()
    interpreter.execute_code.side_effect = RuntimeError("secret internal detail")
    tool = make_code_tool(lambda: interpreter)

    output = tool.invoke({"code": "print(1)"})

    assert "secret internal detail" not in output
    assert "failed" in output.lower()
    interpreter.stop.assert_called_once()


def test_content_blocks_are_flattened_to_text():
    agent, _ = make_agent([AIMessage(content=[{"type": "text", "text": "Hello"}, {"type": "text", "text": " there"}])])

    assert handle_request(agent, {"prompt": "hi"}, "session-a") == {"response": "Hello there"}


@pytest.mark.parametrize("payload", [{}, {"prompt": ""}, {"prompt": "  "}, {"prompt": 5}, None, "text"])
def test_invalid_payload_is_rejected_before_calling_model(payload):
    agent, model = make_agent([AIMessage("unused")])

    with pytest.raises(ValueError):
        handle_request(agent, payload, "session-a")

    assert model.calls == []


def test_guardrail_intervention_is_flagged_as_blocked():
    blocked = AIMessage("Blocked by policy", response_metadata={"stopReason": "guardrail_intervened"})
    agent, _ = make_agent([blocked])

    result = handle_request(agent, {"prompt": "bad request"}, "session-a")

    assert result == {"response": "Blocked by policy", "blocked": True}


def test_request_config_carries_session_and_actor_for_memory():
    agent = Mock()
    agent.ainvoke = AsyncMock(return_value={"messages": [AIMessage("ok")]})

    asyncio.run(_handle_request(agent, {"prompt": "hi"}, "session-a"))

    config = agent.ainvoke.call_args.kwargs["config"]["configurable"]
    assert config == {"thread_id": "session-a", "actor_id": "anonymous"}
