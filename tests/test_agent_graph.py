import asyncio
import re
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from agent.graph import build_agent
from agent.graph import handle_request as _handle_request
from agent.graph import resolve_attachments
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


def capturing_agent(reply='ok'):
    agent = Mock()
    agent.sent = []

    async def ainvoke(state, config=None):
        agent.sent.append(resolve_attachments(state['messages'][0][1]))
        return {'messages': [AIMessage(reply)]}

    agent.ainvoke = AsyncMock(side_effect=ainvoke)
    return agent


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


def test_image_payload_builds_multimodal_message():
    agent = capturing_agent("a cat")
    payload = {"prompt": "What is this?", "image": {"mediaType": "image/png", "data": "aGVsbG8="}}

    result = asyncio.run(_handle_request(agent, payload, "session-a"))

    assert result == {"response": "a cat"}
    assert agent.sent[0] == [
        {"type": "text", "text": "What is this?"},
        {"type": "image", "base64": "aGVsbG8=", "mime_type": "image/png"},
    ]


@pytest.mark.parametrize(
    "image",
    [{}, "x", {"mediaType": "image/bmp", "data": "aGk="}, {"mediaType": "image/png", "data": ""}],
)
def test_invalid_image_is_rejected_before_calling_agent(image):
    agent = Mock()
    agent.ainvoke = AsyncMock()

    with pytest.raises(ValueError):
        asyncio.run(_handle_request(agent, {"prompt": "hi", "image": image}, "s"))
    agent.ainvoke.assert_not_called()


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.mark.parametrize("media_type", ["application/pdf", "text/plain", "text/csv", DOCX])
def test_document_file_builds_named_file_block(media_type):
    agent = capturing_agent("summary")
    payload = {"prompt": "Summarise", "file": {"mediaType": media_type, "data": "aGVsbG8=", "kind": "document"}}

    asyncio.run(_handle_request(agent, payload, "session-a"))

    text_block, file_block = agent.sent[0]
    assert text_block == {"type": "text", "text": "Summarise"}
    assert file_block["type"] == "file"
    assert file_block["base64"] == "aGVsbG8="
    assert file_block["mime_type"] == media_type
    assert re.fullmatch(r"document-[0-9a-f]{8}", file_block["name"])


@pytest.mark.parametrize("original,expected", [
    ("Quarterly Report.pdf", r"Quarterly Report-[0-9a-f]{8}"),
    ("../../etc/passwd; rm -rf.txt", r"etc-passwd- rm -rf-[0-9a-f]{8}|[A-Za-z0-9 \-]+-[0-9a-f]{8}"),
    ("日本語.pdf", r"document-[0-9a-f]{8}"),
    (None, r"document-[0-9a-f]{8}"),
    ("a" * 200 + ".pdf", r"a{40}-[0-9a-f]{8}"),
])
def test_document_name_keeps_safe_original_name_plus_unique_suffix(original, expected):
    agent = capturing_agent()
    file = {"mediaType": "application/pdf", "data": "aGVsbG8=", "kind": "document"}
    if original is not None:
        file["name"] = original
    asyncio.run(_handle_request(agent, {"prompt": "x", "file": file}, "s"))

    name = agent.sent[0][1]["name"]
    assert re.fullmatch(expected, name)
    assert re.fullmatch(r"[A-Za-z0-9 \-()\[\]]+", name) and "  " not in name


def test_each_document_gets_a_unique_name():
    names = set()
    for _ in range(20):
        agent = capturing_agent("summary")
        payload = {"prompt": "Summarise", "file": {"mediaType": "application/pdf", "data": "aGVsbG8=", "kind": "document"}}
        asyncio.run(_handle_request(agent, payload, "session-a"))
        names.add(agent.sent[0][1]["name"])

    assert len(names) == 20


def test_replayed_history_with_duplicate_document_names_is_renamed_before_model_call():
    agent, model = make_agent([AIMessage("ok")])
    old = {"type": "file", "base64": "aGVsbG8=", "mime_type": "application/pdf", "name": "document"}
    history = [("user", [{"type": "text", "text": "a"}, dict(old)]), ("assistant", "done"),
               ("user", [{"type": "text", "text": "b"}, dict(old)])]

    asyncio.run(agent.ainvoke({"messages": history}, config={"configurable": {"thread_id": "t"}}))

    sent = " ".join(model.calls[0])
    names = re.findall(r"'name': '([^']+)'", sent)
    assert len(names) == 2 and len(set(names)) == 2


def test_attachment_bytes_never_reach_graph_state_but_reach_the_model():
    from langgraph.checkpoint.memory import MemorySaver

    saver = MemorySaver()
    model = ScriptedModel(replies=[AIMessage("a cat")])
    agent = build_agent(model, [], saver)
    payload = {"prompt": "What is this?", "image": {"mediaType": "image/png", "data": "QUJDREVGRw=="}}

    asyncio.run(_handle_request(agent, payload, "s1"))

    assert "QUJDREVGRw==" in " ".join(model.calls[0])
    stored = str(saver.get_tuple({"configurable": {"thread_id": "s1"}}).checkpoint)
    assert "QUJDREVGRw==" not in stored


def test_image_file_builds_image_block():
    agent = capturing_agent("a cat")
    payload = {"prompt": "?", "file": {"mediaType": "image/png", "data": "aGVsbG8=", "kind": "image"}}

    asyncio.run(_handle_request(agent, payload, "session-a"))

    block = agent.sent[0][1]
    assert block == {"type": "image", "base64": "aGVsbG8=", "mime_type": "image/png"}


@pytest.mark.parametrize(
    "file",
    [
        "x",
        {},
        {"mediaType": "image/bmp", "data": "aGk=", "kind": "image"},
        {"mediaType": "application/pdf", "data": "", "kind": "document"},
        {"mediaType": "application/pdf", "data": "aGk=", "kind": "image"},
        {"mediaType": "image/png", "data": "aGk=", "kind": "document"},
    ],
)
def test_invalid_file_is_rejected_before_calling_agent(file):
    agent = Mock()
    agent.ainvoke = AsyncMock()

    with pytest.raises(ValueError):
        asyncio.run(_handle_request(agent, {"prompt": "hi", "file": file}, "s"))
    agent.ainvoke.assert_not_called()


def test_converse_formats_document_block_for_langchain_aws():
    from langchain_aws.chat_models.bedrock_converse import _messages_to_bedrock
    from langchain_core.messages import HumanMessage

    message = HumanMessage(
        [
            {"type": "text", "text": "Summarise"},
            {"type": "file", "base64": "aGVsbG8=", "mime_type": "application/pdf", "name": "document"},
        ]
    )

    converted, _ = _messages_to_bedrock([message])

    document = converted[0]["content"][1]["document"]
    assert document["format"] == "pdf"
    assert document["name"] == "document"
    assert document["source"]["bytes"] == b"hello"


def test_request_config_carries_session_and_actor_for_memory():
    agent = Mock()
    agent.ainvoke = AsyncMock(return_value={"messages": [AIMessage("ok")]})

    asyncio.run(_handle_request(agent, {"prompt": "hi"}, "session-a"))

    config = agent.ainvoke.call_args.kwargs["config"]["configurable"]
    assert config == {"thread_id": "session-a", "actor_id": "anonymous"}
