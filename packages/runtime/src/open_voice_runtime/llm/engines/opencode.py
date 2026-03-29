from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from open_voice_runtime.core.errors import ErrorCode, OpenVoiceError
from open_voice_runtime.integrations.opencode import OpencodeClient, OpencodeModelRef
from open_voice_runtime.llm.contracts import (
    LlmCapabilities,
    LlmEvent,
    LlmEventKind,
    LlmOutputLane,
    LlmPhase,
    LlmRequest,
    LlmResponse,
    LlmSessionConfig,
    LlmToolDefinition,
    LlmToolKind,
    TokenUsage,
)
from open_voice_runtime.llm.engine import BaseLlmEngine
from open_voice_runtime.llm.prompting import build_open_voice_system_prompt


def default_opencode_tools() -> tuple[LlmToolDefinition, ...]:
    return (
        LlmToolDefinition(
            name="websearch",
            description="Search the web for current information and relevant sources.",
            kind=LlmToolKind.FUNCTION,
        ),
        LlmToolDefinition(
            name="webfetch",
            description="Fetch and read content from a specific web URL.",
            kind=LlmToolKind.FUNCTION,
        ),
    )


@dataclass(slots=True)
class _State:
    kinds: dict[str, str] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    message_roles: dict[str, str] = field(default_factory=dict)
    part_messages: dict[str, str] = field(default_factory=dict)


class OpencodeLlmEngine(BaseLlmEngine):
    id = "opencode"
    label = "OpenCode"
    capabilities = LlmCapabilities(
        streaming=True,
        tool_calls=True,
        provider_managed_sessions=True,
    )

    def __init__(self, client: OpencodeClient | None = None) -> None:
        self._client = client or OpencodeClient()
        self._sessions: dict[str, str] = {}
        self.available = True
        self.status = "ready"

    async def load(self) -> None:
        try:
            await self._client.ensure_running()
        except RuntimeError as exc:
            raise OpenVoiceError(
                code=ErrorCode.PROVIDER_ERROR,
                message=str(exc),
                retryable=True,
                details={"engine_id": self.id},
            ) from exc

    async def close(self) -> None:
        await self._client.close()

    async def complete(self, request: LlmRequest) -> LlmResponse:
        text = ""
        usage: TokenUsage | None = None
        finish_reason: str | None = None
        provider: str | None = None
        model: str | None = None
        cost: float | None = None

        stream = self.stream(request)
        async for event in stream:
            if event.kind is LlmEventKind.USAGE:
                usage = event.usage
                cost = event.cost
            if event.kind is LlmEventKind.SUMMARY:
                usage = event.usage or usage
                provider = event.provider or provider
                model = event.model or model
                cost = event.cost if event.cost is not None else cost
            if event.kind is LlmEventKind.COMPLETED:
                text = event.text
                finish_reason = event.finish_reason
                provider = event.provider or provider
                model = event.model or model
                usage = event.usage or usage
                cost = event.cost if event.cost is not None else cost

        return LlmResponse(
            text=text,
            finish_reason=finish_reason,
            provider=provider,
            model=model,
            usage=usage,
            cost=cost,
        )

    def stream(self, request: LlmRequest) -> AsyncIterator[LlmEvent]:
        return self._stream(request)

    async def _stream(self, request: LlmRequest) -> AsyncIterator[LlmEvent]:
        await self.load()
        model = _model(request)
        tools = _tools(request.tools)
        session_id = await self._session(request.session_id, tools)
        queue: asyncio.Queue[LlmEvent | Exception | None] = asyncio.Queue()
        ready = asyncio.Event()
        stop = asyncio.Event()
        state = _State()
        listener = asyncio.create_task(self._listen(session_id, tools, state, queue, ready, stop))
        started = False

        try:
            yield LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING)
            await ready.wait()
            await self._raise_if_listener_failed(queue, listener)

            await self._client.prompt_async(
                session_id,
                model=model,
                system=_prompt(request, tools),
                user_text=_user_text(request),
                mode=_mode_from_request(request),
            )

            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                event = item
                if event.kind is LlmEventKind.RESPONSE_DELTA and not started:
                    started = True
                    yield LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.GENERATING)
                yield event

            summary = await self._client.latest_assistant_message(session_id)
            info = _message_info(summary)
            usage = _usage(info.get("tokens"))
            provider = _as_str(info.get("providerID")) or model.provider_id
            model_id = _as_str(info.get("modelID")) or model.model_id
            cost = _as_float(info.get("cost"))
            finish = _as_str(info.get("finish"))
            text = _message_text(summary)

            yield LlmEvent(
                kind=LlmEventKind.SUMMARY,
                provider=provider,
                model=model_id,
                usage=usage,
                cost=cost,
                metadata={
                    "opencode_system_stack": _system_stack(summary),
                },
            )
            yield LlmEvent(
                kind=LlmEventKind.COMPLETED,
                text=text,
                finish_reason=finish,
                provider=provider,
                model=model_id,
                usage=usage,
                cost=cost,
            )
            yield LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.DONE)
        except RuntimeError as exc:
            raise OpenVoiceError(
                code=ErrorCode.PROVIDER_ERROR,
                message=str(exc),
                retryable=True,
                details={"engine_id": self.id},
            ) from exc
        finally:
            stop.set()
            listener.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await listener

    async def _session(self, runtime_session_id: str, tools: tuple[LlmToolDefinition, ...]) -> str:
        existing = self._sessions.get(runtime_session_id)
        if existing is not None:
            return existing
        session_id = await self._client.create_session(permission=_permissions(tools))
        self._sessions[runtime_session_id] = session_id
        return session_id

    async def _listen(
        self,
        session_id: str,
        tools: tuple[LlmToolDefinition, ...],
        state: _State,
        queue: asyncio.Queue[LlmEvent | Exception | None],
        ready: asyncio.Event,
        stop: asyncio.Event,
    ) -> None:
        kinds = {tool.name: tool.kind for tool in tools}
        try:
            async for payload in self._client.iter_events(stop, ready):
                event = _payload(payload)
                kind = _as_str(event.get("type"))
                if kind in {"server.connected", "server.heartbeat"}:
                    continue
                if kind == "message.updated":
                    info = _props(event).get("info")
                    if isinstance(info, dict):
                        message_id = _as_str(info.get("id"))
                        role = _as_str(info.get("role"))
                        if message_id and role:
                            state.message_roles[message_id] = role
                if kind == "session.error" and _session_from_event(event) == session_id:
                    props = _props(event)
                    message = _as_str(props.get("message")) or "OpenCode session error."
                    raise OpenVoiceError(
                        code=ErrorCode.PROVIDER_ERROR,
                        message=message,
                        retryable=True,
                        details={"engine_id": self.id, "session_id": session_id},
                    )
                if kind == "session.idle" and _session_from_event(event) == session_id:
                    return
                if kind == "session.status" and _session_from_event(event) == session_id:
                    status = event.get("properties", {}).get("status")
                    if isinstance(status, dict) and _as_str(status.get("type")) == "idle":
                        return
                if kind == "session.status" and _session_from_event(event) == session_id:
                    status = event.get("properties", {}).get("status")
                    if isinstance(status, dict) and _as_str(status.get("type")) == "retry":
                        message = (
                            _as_str(status.get("message"))
                            or "OpenCode session entered retry state."
                        )
                        raise OpenVoiceError(
                            code=ErrorCode.PROVIDER_ERROR,
                            message=message,
                            retryable=True,
                            details={"engine_id": self.id, "session_id": session_id},
                        )
                if not _matches_session(event, session_id):
                    continue
                for item in _events(event, state, kinds):
                    await queue.put(item)
        except Exception as exc:
            ready.set()
            await queue.put(exc)
        finally:
            ready.set()
            await queue.put(None)

    async def _raise_if_listener_failed(
        self,
        queue: asyncio.Queue[LlmEvent | Exception | None],
        listener: asyncio.Task[None],
    ) -> None:
        if not listener.done():
            return
        item = await queue.get()
        if isinstance(item, Exception):
            raise item


def _model(request: LlmRequest) -> OpencodeModelRef:
    provider = request.provider
    model = request.model
    if provider and model:
        return OpencodeModelRef(provider_id=provider, model_id=model)
    raise OpenVoiceError(
        code=ErrorCode.PROVIDER_ERROR,
        message="OpenCode LLM requests require both provider and model.",
        retryable=False,
        details={"engine_id": "opencode"},
    )


def _tools(value: tuple[LlmToolDefinition, ...]) -> tuple[LlmToolDefinition, ...]:
    merged = {tool.name: tool for tool in default_opencode_tools()}
    for tool in value:
        merged[tool.name] = tool
    return tuple(merged.values())


def _permissions(tools: tuple[LlmToolDefinition, ...]) -> list[dict[str, str]]:
    # Enforce an allowlist at session level so only explicitly configured tools
    # can execute. This prevents fallback to unrelated external tools.
    permissions: list[dict[str, str]] = [
        {
            "permission": "*",
            "pattern": "*",
            "action": "deny",
        }
    ]
    for tool in tools:
        permissions.append(
            {
                "permission": tool.name,
                "pattern": "*",
                "action": "allow",
            }
        )
    return permissions


def _prompt(request: LlmRequest, tools: tuple[LlmToolDefinition, ...]) -> str:
    instructions = request.metadata.get("additional_instructions")
    opencode_force_system_override = request.metadata.get("opencode_force_system_override") is True
    if opencode_force_system_override and isinstance(request.system_prompt, str):
        return request.system_prompt

    config = LlmSessionConfig(
        system_prompt=request.system_prompt,
        additional_instructions=instructions if isinstance(instructions, str) else None,
        tools=tools,
    )
    prompt = build_open_voice_system_prompt(config)
    allowed = ", ".join(tool.name for tool in tools) if tools else "none"
    hard_tool_guard = (
        "\n\nTool Execution Constraints:\n"
        f"- You may call only these tools: {allowed}.\n"
        "- Do not request or reference any other tool names.\n"
        "- If a requested capability is unavailable, answer without tool calls instead of switching tools.\n"
        "- Never output raw JSON or pseudo tool-call text in user-facing responses."
    )
    return prompt + hard_tool_guard


def _mode_from_request(request: LlmRequest) -> str | None:
    value = request.metadata.get("opencode_mode")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _system_stack(message: dict[str, Any] | None) -> list[str]:
    if not isinstance(message, dict):
        return []
    info = _message_info(message)
    raw = info.get("system")
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        if isinstance(item, str):
            cleaned = item.strip()
            if cleaned:
                result.append(cleaned)
    return result


def _user_text(request: LlmRequest) -> str:
    for message in reversed(request.messages):
        if message.content.strip():
            return message.content
    raise OpenVoiceError(
        code=ErrorCode.PROVIDER_ERROR,
        message="OpenCode LLM requests require at least one non-empty message.",
        retryable=False,
        details={"engine_id": "opencode"},
    )


def _payload(value: dict[str, Any]) -> dict[str, Any]:
    inner = value.get("payload")
    if isinstance(inner, dict):
        return inner
    return value


def _props(value: dict[str, Any]) -> dict[str, Any]:
    props = value.get("properties")
    if isinstance(props, dict):
        return props
    return {}


def _matches_session(value: dict[str, Any], session_id: str) -> bool:
    kind = _as_str(value.get("type"))
    props = _props(value)
    if kind == "message.part.updated":
        part = props.get("part")
        return isinstance(part, dict) and part.get("sessionID") == session_id
    if kind == "message.part.delta":
        return props.get("sessionID") == session_id
    if kind == "message.updated":
        info = props.get("info")
        return isinstance(info, dict) and info.get("sessionID") == session_id
    return _session_from_event(value) == session_id


def _session_from_event(value: dict[str, Any]) -> str | None:
    props = _props(value)
    session_id = props.get("sessionID")
    if isinstance(session_id, str):
        return session_id
    part = props.get("part")
    if isinstance(part, dict):
        return _as_str(part.get("sessionID"))
    info = props.get("info")
    if isinstance(info, dict):
        return _as_str(info.get("sessionID"))
    return None


def _events(
    value: dict[str, Any],
    state: _State,
    tools: dict[str, LlmToolKind],
) -> list[LlmEvent]:
    kind = _as_str(value.get("type"))
    props = _props(value)
    if kind == "message.part.updated":
        part = props.get("part")
        if not isinstance(part, dict):
            return []
        part_id = _as_str(part.get("id"))
        message_id = _as_str(part.get("messageID"))
        if part_id and message_id:
            state.part_messages[part_id] = message_id
        if message_id is None and part_id is not None:
            message_id = state.part_messages.get(part_id)
        if message_id is not None and state.message_roles.get(message_id) == "user":
            return []
        part_kind = _as_str(part.get("type"))
        if part_id and part_kind:
            state.kinds[part_id] = part_kind
        if part_kind == "reasoning":
            return _text_update(part_id, part, state, reasoning=True)
        if part_kind == "text":
            return _text_update(part_id, part, state, reasoning=False)
        if part_kind in {"tool", "tool-call", "tool_call"}:
            return [_tool_update(part, tools)]
        if part_kind == "step-start":
            tool_event = _tool_update_if_present(part, tools, fallback_status="running")
            return [tool_event] if tool_event is not None else []
        if part_kind == "step-finish":
            events: list[LlmEvent] = []
            tool_event = _tool_update_if_present(part, tools, fallback_status="completed")
            if tool_event is not None:
                events.append(tool_event)
            events.append(_usage_update(part))
            return events
        return []
    if kind == "message.part.delta":
        part_id = _as_str(props.get("partID"))
        message_id = _as_str(props.get("messageID"))
        if message_id is None and part_id is not None:
            message_id = state.part_messages.get(part_id)
        if message_id is not None and state.message_roles.get(message_id) == "user":
            return []
        if part_id and message_id:
            state.part_messages.setdefault(part_id, message_id)
        field = _as_str(props.get("field"))
        delta = _as_str(props.get("delta"))
        if not part_id or field != "text" or not delta:
            return []
        previous = state.texts.get(part_id, "")
        # Some providers can replay the full accumulated part text as a new
        # delta. Suppress only probable full-text replays (long chunks) to
        # avoid dropping legitimate short repeated tokens.
        if _is_probable_replayed_full_delta(previous, delta):
            return []
        state.texts[part_id] = previous + delta
        if state.kinds.get(part_id) == "reasoning":
            return [
                LlmEvent(
                    kind=LlmEventKind.REASONING_DELTA,
                    part_id=part_id,
                    text=delta,
                )
            ]
        return [
            LlmEvent(
                kind=LlmEventKind.RESPONSE_DELTA,
                part_id=part_id,
                text=delta,
                lane=LlmOutputLane.SPEECH,
            )
        ]
    return []


def _text_update(
    part_id: str | None,
    part: dict[str, Any],
    state: _State,
    *,
    reasoning: bool,
) -> list[LlmEvent]:
    if part_id is None:
        return []
    text = _as_str(part.get("text")) or ""
    delta = _delta(state.texts.get(part_id, ""), text)
    state.texts[part_id] = text
    if not delta:
        return []
    if reasoning:
        return [
            LlmEvent(
                kind=LlmEventKind.REASONING_DELTA,
                part_id=part_id,
                text=delta,
            )
        ]
    return [
        LlmEvent(
            kind=LlmEventKind.RESPONSE_DELTA,
            part_id=part_id,
            text=delta,
            lane=LlmOutputLane.SPEECH,
        )
    ]


def _tool_update(
    part: dict[str, Any],
    tools: dict[str, LlmToolKind],
    *,
    resolved_name: str | None = None,
) -> LlmEvent:
    state = part.get("state")
    data = state if isinstance(state, dict) else {}
    meta: dict[str, Any] = {}
    if isinstance(data.get("metadata"), dict):
        meta.update(data["metadata"])
    if isinstance(part.get("metadata"), dict):
        meta.update(part["metadata"])
    name = resolved_name or _tool_name(part) or "unknown"
    status = _as_str(data.get("status"))
    call_id = _as_str(part.get("callID")) or _as_str(data.get("callID")) or _as_str(part.get("id"))
    return LlmEvent(
        kind=LlmEventKind.TOOL_UPDATE,
        call_id=call_id,
        tool_name=name,
        tool_input=data.get("input") if data.get("input") is not None else part.get("input"),
        tool_metadata=meta,
        tool_output=data.get("output") if data.get("output") is not None else part.get("output"),
        tool_error=data.get("error") if data.get("error") is not None else part.get("error"),
        metadata={
            "status": status,
            "is_mcp": tools.get(name) is LlmToolKind.MCP,
        },
    )


def _tool_name(part: dict[str, Any]) -> str | None:
    state = part.get("state")
    data = state if isinstance(state, dict) else {}

    for candidate in (
        _as_str(part.get("tool")),
        _as_str(part.get("toolName")),
        _as_str(part.get("name")),
        _as_str(data.get("tool")),
        _as_str(data.get("toolName")),
        _as_str(data.get("name")),
    ):
        if candidate:
            return candidate
    return None


def _resolved_tool_name(part: dict[str, Any], tools: dict[str, LlmToolKind]) -> str | None:
    _ = tools
    explicit = _tool_name(part)
    if explicit is not None:
        return explicit

    return None


def _tool_update_if_present(
    part: dict[str, Any],
    tools: dict[str, LlmToolKind],
    *,
    fallback_status: str,
) -> LlmEvent | None:
    name = _resolved_tool_name(part, tools)
    if name is None:
        return None
    event = _tool_update(part, tools, resolved_name=name)
    if event.metadata.get("status") is None:
        event.metadata["status"] = fallback_status
    return event


def _usage_update(part: dict[str, Any]) -> LlmEvent:
    return LlmEvent(
        kind=LlmEventKind.USAGE,
        usage=_usage(part.get("tokens")),
        cost=_as_float(part.get("cost")),
    )


def _message_info(value: dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    info = value.get("info")
    if isinstance(info, dict):
        return info
    return {}


def _message_text(value: dict[str, Any] | None) -> str:
    if value is None:
        return ""
    parts = value.get("parts")
    if not isinstance(parts, list):
        return ""
    text: list[str] = []
    for item in parts:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        if item.get("ignored") is True:
            continue
        chunk = _as_str(item.get("text"))
        if chunk:
            text.append(chunk)
    return "".join(text).strip()


def _usage(value: Any) -> TokenUsage | None:
    if not isinstance(value, dict):
        return None
    cache = value.get("cache")
    cache_data = cache if isinstance(cache, dict) else {}
    return TokenUsage(
        input_tokens=_as_int(value.get("input")),
        output_tokens=_as_int(value.get("output")),
        reasoning_tokens=_as_int(value.get("reasoning")),
        cache_read_tokens=_as_int(cache_data.get("read")),
        cache_write_tokens=_as_int(cache_data.get("write")),
        total_tokens=_as_int(value.get("total")),
    )


def _delta(previous: str, current: str) -> str:
    if _same_text_content(previous, current):
        return ""
    if current == previous:
        return ""
    if current.startswith(previous):
        return current[len(previous) :]
    # OpenCode can emit snapshot updates where text is trimmed (e.g. trailing
    # newline removed) or whitespace-normalized. Those are not new content and
    # should not be emitted again as full deltas.
    if previous.startswith(current):
        return ""
    return current


def _same_text_content(left: str, right: str) -> bool:
    if left == right:
        return True
    if left.strip() == right.strip():
        return True
    return " ".join(left.split()) == " ".join(right.split())


def _is_probable_replayed_full_delta(previous: str, delta: str) -> bool:
    if not previous or not delta:
        return False
    if len(delta) < 16:
        return False
    return _same_text_content(previous, delta)


def _as_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _as_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _as_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None
