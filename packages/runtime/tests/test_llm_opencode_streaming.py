from __future__ import annotations

from open_voice_runtime.llm.contracts import LlmEvent, LlmEventKind, LlmOutputLane, LlmToolKind
from open_voice_runtime.transport.websocket.session import _conversation_events_from_llm
from open_voice_runtime.llm.engines.opencode import _State
from open_voice_runtime.llm.engines.opencode import default_opencode_tools
from open_voice_runtime.llm.engines.opencode import _events


def _payload(kind: str, properties: dict) -> dict:
    return {
        "type": kind,
        "properties": properties,
    }


def test_events_deduplicate_replayed_message_part_delta_for_reasoning() -> None:
    state = _State(
        message_roles={"msg-assistant": "assistant"},
        part_messages={"prt-1": "msg-assistant"},
        kinds={"prt-1": "reasoning"},
    )
    tools = {"websearch": LlmToolKind.MCP}

    first = _events(
        _payload(
            "message.part.delta",
            {
                "sessionID": "sess-1",
                "messageID": "msg-assistant",
                "partID": "prt-1",
                "field": "text",
                "delta": "From the search results",
            },
        ),
        state,
        tools,
    )
    replayed = _events(
        _payload(
            "message.part.delta",
            {
                "sessionID": "sess-1",
                "messageID": "msg-assistant",
                "partID": "prt-1",
                "field": "text",
                "delta": "From the search results",
            },
        ),
        state,
        tools,
    )

    assert len(first) == 1
    assert first[0].kind is LlmEventKind.REASONING_DELTA
    assert replayed == []


def test_events_emit_follow_up_delta_after_replay_for_response() -> None:
    state = _State(
        message_roles={"msg-assistant": "assistant"},
        part_messages={"prt-2": "msg-assistant"},
        kinds={"prt-2": "text"},
    )
    tools = {"websearch": LlmToolKind.MCP}

    baseline = _events(
        _payload(
            "message.part.delta",
            {
                "sessionID": "sess-1",
                "messageID": "msg-assistant",
                "partID": "prt-2",
                "field": "text",
                "delta": "From the search results",
            },
        ),
        state,
        tools,
    )
    replayed = _events(
        _payload(
            "message.part.delta",
            {
                "sessionID": "sess-1",
                "messageID": "msg-assistant",
                "partID": "prt-2",
                "field": "text",
                "delta": "From the search results",
            },
        ),
        state,
        tools,
    )
    follow_up = _events(
        _payload(
            "message.part.delta",
            {
                "sessionID": "sess-1",
                "messageID": "msg-assistant",
                "partID": "prt-2",
                "field": "text",
                "delta": ", this is settled theory.",
            },
        ),
        state,
        tools,
    )

    assert len(baseline) == 1
    assert baseline[0].kind is LlmEventKind.RESPONSE_DELTA
    assert replayed == []
    assert len(follow_up) == 1
    assert follow_up[0].kind is LlmEventKind.RESPONSE_DELTA
    assert follow_up[0].text == ", this is settled theory."


def test_events_keeps_short_repeated_tokens_from_delta_stream() -> None:
    state = _State(
        message_roles={"msg-assistant": "assistant"},
        part_messages={"prt-3": "msg-assistant"},
        kinds={"prt-3": "text"},
    )
    tools = {"websearch": LlmToolKind.MCP}

    first = _events(
        _payload(
            "message.part.delta",
            {
                "sessionID": "sess-1",
                "messageID": "msg-assistant",
                "partID": "prt-3",
                "field": "text",
                "delta": "yes",
            },
        ),
        state,
        tools,
    )
    second = _events(
        _payload(
            "message.part.delta",
            {
                "sessionID": "sess-1",
                "messageID": "msg-assistant",
                "partID": "prt-3",
                "field": "text",
                "delta": "yes",
            },
        ),
        state,
        tools,
    )

    assert len(first) == 1
    assert first[0].text == "yes"
    assert len(second) == 1
    assert second[0].text == "yes"


def test_events_ignore_whitespace_only_message_part_updated_regression() -> None:
    """Replays test.txt2 duplicate update shape.

    Provider can send two message.part.updated snapshots for the same part where
    only trailing whitespace/newline differs. We should emit only once.
    """

    state = _State(
        message_roles={"msg-assistant": "assistant"},
    )
    tools = {"websearch": LlmToolKind.MCP}

    first = _events(
        _payload(
            "message.part.updated",
            {
                "part": {
                    "sessionID": "sess-1",
                    "messageID": "msg-assistant",
                    "id": "prt-r1",
                    "type": "reasoning",
                    "text": "The user asked for evidence from the web.\n",
                }
            },
        ),
        state,
        tools,
    )
    second = _events(
        _payload(
            "message.part.updated",
            {
                "part": {
                    "sessionID": "sess-1",
                    "messageID": "msg-assistant",
                    "id": "prt-r1",
                    "type": "reasoning",
                    "text": "The user asked for evidence from the web.",
                }
            },
        ),
        state,
        tools,
    )

    assert len(first) == 1
    assert first[0].kind is LlmEventKind.REASONING_DELTA
    assert second == []


def test_events_message_part_updated_keeps_real_follow_up_content() -> None:
    state = _State(
        message_roles={"msg-assistant": "assistant"},
    )
    tools = {"websearch": LlmToolKind.MCP}

    baseline = _events(
        _payload(
            "message.part.updated",
            {
                "part": {
                    "sessionID": "sess-1",
                    "messageID": "msg-assistant",
                    "id": "prt-t1",
                    "type": "text",
                    "text": "From the search results",
                }
            },
        ),
        state,
        tools,
    )
    follow_up = _events(
        _payload(
            "message.part.updated",
            {
                "part": {
                    "sessionID": "sess-1",
                    "messageID": "msg-assistant",
                    "id": "prt-t1",
                    "type": "text",
                    "text": "From the search results, this is settled theory.",
                }
            },
        ),
        state,
        tools,
    )

    assert len(baseline) == 1
    assert baseline[0].kind is LlmEventKind.RESPONSE_DELTA
    assert len(follow_up) == 1
    assert follow_up[0].kind is LlmEventKind.RESPONSE_DELTA
    assert follow_up[0].text == ", this is settled theory."


def test_events_parse_tool_call_alias_and_emit_tool_update() -> None:
    state = _State(message_roles={"msg-assistant": "assistant"})
    tools = {"websearch": LlmToolKind.MCP}

    updates = _events(
        _payload(
            "message.part.updated",
            {
                "part": {
                    "sessionID": "sess-1",
                    "messageID": "msg-assistant",
                    "id": "prt-tool-1",
                    "type": "tool_call",
                    "tool": "websearch",
                    "callID": "call_123",
                    "state": {
                        "input": {"query": "Sahil Chouksey"},
                        "status": "running",
                    },
                }
            },
        ),
        state,
        tools,
    )

    assert len(updates) == 1
    event = updates[0]
    assert event.kind is LlmEventKind.TOOL_UPDATE
    assert event.tool_name == "websearch"
    assert event.call_id == "call_123"
    assert event.tool_input == {"query": "Sahil Chouksey"}
    assert event.metadata.get("status") == "running"
    assert event.metadata.get("is_mcp") is True


def test_events_emit_tool_updates_for_step_start_and_step_finish() -> None:
    state = _State(message_roles={"msg-assistant": "assistant"})
    tools = {"websearch": LlmToolKind.MCP}

    start_events = _events(
        _payload(
            "message.part.updated",
            {
                "part": {
                    "sessionID": "sess-1",
                    "messageID": "msg-assistant",
                    "id": "step-1",
                    "type": "step-start",
                    "toolName": "websearch",
                    "callID": "call_step",
                    "state": {
                        "input": {"query": "WLR"},
                    },
                }
            },
        ),
        state,
        tools,
    )
    finish_events = _events(
        _payload(
            "message.part.updated",
            {
                "part": {
                    "sessionID": "sess-1",
                    "messageID": "msg-assistant",
                    "id": "step-1",
                    "type": "step-finish",
                    "toolName": "websearch",
                    "callID": "call_step",
                    "state": {
                        "output": {"results": 2},
                    },
                    "tokens": {
                        "input": 10,
                        "output": 20,
                        "reasoning": 0,
                        "total": 30,
                    },
                }
            },
        ),
        state,
        tools,
    )

    assert len(start_events) == 1
    start = start_events[0]
    assert start.kind is LlmEventKind.TOOL_UPDATE
    assert start.tool_name == "websearch"
    assert start.metadata.get("status") == "running"

    assert len(finish_events) == 2
    assert finish_events[0].kind is LlmEventKind.TOOL_UPDATE
    assert finish_events[0].tool_name == "websearch"
    assert finish_events[0].metadata.get("status") == "completed"
    assert finish_events[1].kind is LlmEventKind.USAGE


def test_events_do_not_emit_tool_update_for_nameless_step_events() -> None:
    state = _State(message_roles={"msg-assistant": "assistant"})
    tools = {"websearch": LlmToolKind.FUNCTION}

    start_events = _events(
        _payload(
            "message.part.updated",
            {
                "part": {
                    "sessionID": "sess-1",
                    "messageID": "msg-assistant",
                    "id": "step-1",
                    "type": "step-start",
                }
            },
        ),
        state,
        tools,
    )
    finish_events = _events(
        _payload(
            "message.part.updated",
            {
                "part": {
                    "sessionID": "sess-1",
                    "messageID": "msg-assistant",
                    "id": "step-1",
                    "type": "step-finish",
                }
            },
        ),
        state,
        tools,
    )

    assert start_events == []
    assert len(finish_events) == 1
    assert finish_events[0].kind is LlmEventKind.USAGE


def test_summary_event_carries_opencode_system_stack_metadata() -> None:
    llm_events = [
        LlmEvent(
            kind=LlmEventKind.SUMMARY,
            provider="github-copilot",
            model="gpt-5.3-codex",
            metadata={"opencode_system_stack": ["build mode prompt", "runtime prompt"]},
        ),
        LlmEvent(
            kind=LlmEventKind.RESPONSE_DELTA,
            text="hello",
            lane=LlmOutputLane.SPEECH,
            part_id="part-1",
        ),
    ]

    events = _conversation_events_from_llm("sess-1", "turn-1", llm_events)
    summary = next(event for event in events if event.type == "llm.summary")
    assert summary.metadata == {"opencode_system_stack": ["build mode prompt", "runtime prompt"]}


def test_default_opencode_tool_uses_builtin_websearch_function_kind() -> None:
    tools = default_opencode_tools()

    assert len(tools) == 2
    assert tools[0].name == "websearch"
    assert tools[0].kind is LlmToolKind.FUNCTION
    assert tools[1].name == "webfetch"
    assert tools[1].kind is LlmToolKind.FUNCTION
