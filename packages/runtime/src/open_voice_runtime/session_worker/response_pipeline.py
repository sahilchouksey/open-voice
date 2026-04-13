from __future__ import annotations

from collections.abc import Awaitable, Callable
import asyncio
from time import monotonic

from open_voice_runtime.app.config import RuntimeConfig
from open_voice_runtime.conversation.events import (
    ConversationEvent,
    ErrorEvent,
    LlmCompletedEvent,
    LlmPhaseEvent,
    LlmReasoningDeltaEvent,
    LlmResponseDeltaEvent,
    LlmSummaryEvent,
    LlmToolUpdateEvent,
    LlmUsageEvent,
    RouteSelectedEvent,
)
from open_voice_runtime.core.errors import ErrorCode, OpenVoiceError
from open_voice_runtime.llm.contracts import (
    LlmEvent,
    LlmEventKind,
    LlmMessage,
    LlmRequest,
    LlmRole,
)
from open_voice_runtime.llm.service import LlmService
from open_voice_runtime.router.contracts import RouteDecision, RouteRequest, RouteTarget
from open_voice_runtime.router.policy import select_route_target
from open_voice_runtime.router.service import RouterService
from open_voice_runtime.session.models import SessionState


ConversationEventEmitter = Callable[[ConversationEvent], Awaitable[None]]


class ResponsePipeline:
    def __init__(
        self,
        *,
        config: RuntimeConfig,
        router_service: RouterService | None,
        llm_service: LlmService | None,
    ) -> None:
        self._config = config
        self._router_service = router_service
        self._llm_service = llm_service

    async def route_text(
        self,
        state: SessionState,
        *,
        turn_id: str | None,
        text: str,
    ) -> tuple[list[ConversationEvent], RouteDecision | None]:
        targets = _route_targets(state, self._config)
        target = self._fallback_target(targets)
        if self._router_service is None or not self._router_service.is_available(
            state.engine_selection.router
        ):
            return self._fallback_events(
                state.session_id, turn_id, target
            ), self._fallback_decision(target)

        request = RouteRequest(
            session_id=state.session_id,
            turn_id=turn_id or "",
            user_text=text,
            available_targets=targets,
            metadata=dict(state.metadata),
        )
        try:
            decision = await asyncio.wait_for(
                self._router_service.route(request, engine_id=state.engine_selection.router),
                timeout=1.5,
            )
        except Exception:
            return self._fallback_events(
                state.session_id, turn_id, target
            ), self._fallback_decision(target)

        event = RouteSelectedEvent(
            state.session_id,
            decision.router_id,
            turn_id=turn_id,
            route_name=decision.route_name,
            llm_engine_id=decision.llm_engine_id,
            provider=decision.provider,
            model=decision.model,
            profile_id=decision.profile_id,
            reason=decision.reason,
            confidence=decision.confidence,
        )
        return [event], decision

    async def stream_llm(
        self,
        state: SessionState,
        *,
        turn_id: str | None,
        user_text: str,
        decision: RouteDecision | None,
        generation_id: str | None,
        emit: ConversationEventEmitter | None = None,
    ) -> tuple[list[ConversationEvent], str | None, float | None]:
        if decision is None or self._llm_service is None:
            return [], None, None
        engine_id = decision.llm_engine_id or state.engine_selection.llm
        if not self._llm_service.is_available(engine_id):
            return [], None, None

        config = _effective_runtime_config(state, self._config)
        request = LlmRequest(
            session_id=state.session_id,
            turn_id=turn_id or "",
            messages=[LlmMessage(role=LlmRole.USER, content=user_text)],
            provider=decision.provider,
            model=decision.model,
            system_prompt=config.llm.system_prompt,
            tools=config.llm.tools,
            metadata={
                "additional_instructions": config.llm.additional_instructions,
                "opencode_mode": config.llm.opencode_mode,
                "opencode_force_system_override": config.llm.opencode_force_system_override,
                "route_name": decision.route_name,
                "profile_id": decision.profile_id,
            },
        )

        llm_raw: list[LlmEvent] = []
        first_delta_at: float | None = None
        try:
            stream = self._llm_service.stream(request, engine_id=engine_id)
            async for item in stream:
                llm_raw.append(item)
                if first_delta_at is None and item.text:
                    first_delta_at = monotonic()
                if emit is not None:
                    events = _conversation_events_from_llm(state.session_id, turn_id, [item])
                    _set_generation_for_events(events, generation_id)
                    await _emit_conversation_events(emit, events)
        except Exception as error:  # pragma: no cover - defensive provider surface
            payload = ErrorEvent(
                state.session_id,
                error
                if isinstance(error, OpenVoiceError)
                else OpenVoiceError(
                    code=ErrorCode.PROVIDER_ERROR,
                    message=str(error),
                    retryable=True,
                    details={"stage": "llm.stream"},
                ),
                turn_id=turn_id,
            )
            if emit is not None:
                payload.generation_id = generation_id
                await emit(payload)
                return [], None, first_delta_at
            return [payload], None, first_delta_at

        if emit is not None:
            return [], _assistant_text(llm_raw), first_delta_at
        events = _conversation_events_from_llm(state.session_id, turn_id, llm_raw)
        _set_generation_for_events(events, generation_id)
        return events, _assistant_text(llm_raw), first_delta_at

    @staticmethod
    def _fallback_target(targets: tuple[RouteTarget, ...]) -> RouteTarget | None:
        if not targets:
            return None
        return select_route_target("moderate_route", targets) or targets[0]

    @staticmethod
    def _fallback_decision(target: RouteTarget | None) -> RouteDecision | None:
        if target is None:
            return None
        return RouteDecision(
            router_id="fallback",
            route_name=target.profile_id or "moderate_route",
            llm_engine_id=target.llm_engine_id,
            provider=target.provider,
            model=target.model,
            profile_id=target.profile_id,
            reason="Configured fallback route target.",
        )

    @classmethod
    def _fallback_events(
        cls,
        session_id: str,
        turn_id: str | None,
        target: RouteTarget | None,
    ) -> list[ConversationEvent]:
        decision = cls._fallback_decision(target)
        if decision is None:
            return []
        return [
            RouteSelectedEvent(
                session_id,
                decision.router_id,
                turn_id=turn_id,
                route_name=decision.route_name,
                llm_engine_id=decision.llm_engine_id,
                provider=decision.provider,
                model=decision.model,
                profile_id=decision.profile_id,
                reason=decision.reason,
                confidence=decision.confidence,
            )
        ]


async def _emit_conversation_events(
    emit: ConversationEventEmitter,
    events: list[ConversationEvent],
) -> None:
    for event in events:
        await emit(event)


def _set_generation_for_events(events: list[ConversationEvent], generation_id: str | None) -> None:
    if generation_id is None:
        return
    for event in events:
        event.generation_id = generation_id


def _assistant_text(llm_events: list[LlmEvent]) -> str | None:
    for item in reversed(llm_events):
        if item.kind is LlmEventKind.COMPLETED:
            text = item.text.strip()
            return text or None
    return None


def _conversation_events_from_llm(
    session_id: str,
    turn_id: str | None,
    llm_events: list[LlmEvent],
) -> list[ConversationEvent]:
    events: list[ConversationEvent] = []
    for item in llm_events:
        if item.kind is LlmEventKind.PHASE:
            events.append(
                LlmPhaseEvent(session_id, item.phase.value if item.phase else "", turn_id=turn_id)
            )
        elif item.kind is LlmEventKind.REASONING_DELTA:
            events.append(
                LlmReasoningDeltaEvent(
                    session_id,
                    item.text,
                    turn_id=turn_id,
                    part_id=item.part_id,
                )
            )
        elif item.kind is LlmEventKind.RESPONSE_DELTA:
            events.append(
                LlmResponseDeltaEvent(
                    session_id,
                    item.text,
                    turn_id=turn_id,
                    lane=item.lane.value if item.lane else None,
                    part_id=item.part_id,
                )
            )
        elif item.kind is LlmEventKind.TOOL_UPDATE:
            status = item.metadata.get("status") if isinstance(item.metadata, dict) else None
            is_mcp = (
                item.metadata.get("is_mcp") is True if isinstance(item.metadata, dict) else False
            )
            events.append(
                LlmToolUpdateEvent(
                    session_id,
                    tool_name=item.tool_name or "unknown",
                    turn_id=turn_id,
                    call_id=item.call_id,
                    status=status if isinstance(status, str) else None,
                    tool_input=item.tool_input,
                    tool_metadata=item.tool_metadata,
                    tool_output=item.tool_output,
                    tool_error=item.tool_error,
                    is_mcp=is_mcp,
                )
            )
        elif item.kind is LlmEventKind.USAGE:
            events.append(
                LlmUsageEvent(
                    session_id,
                    turn_id=turn_id,
                    usage=item.usage,
                    cost=item.cost,
                )
            )
        elif item.kind is LlmEventKind.SUMMARY:
            events.append(
                LlmSummaryEvent(
                    session_id,
                    turn_id=turn_id,
                    provider=item.provider,
                    model=item.model,
                    usage=item.usage,
                    cost=item.cost,
                    metadata=item.metadata if isinstance(item.metadata, dict) else None,
                )
            )
        elif item.kind is LlmEventKind.COMPLETED:
            events.append(
                LlmCompletedEvent(
                    session_id,
                    text=item.text,
                    finish_reason=item.finish_reason,
                    provider=item.provider,
                    model=item.model,
                    turn_id=turn_id,
                )
            )
    return events


def _route_targets(state: SessionState, config: RuntimeConfig) -> tuple[RouteTarget, ...]:
    runtime_config = _effective_runtime_config(state, config)
    return runtime_config.effective_route_targets(state.engine_selection.llm)


def _effective_runtime_config(state: SessionState, config: RuntimeConfig) -> RuntimeConfig:
    runtime_config = state.metadata.get("runtime_config")
    if not isinstance(runtime_config, dict):
        return config
    try:
        return RuntimeConfig.from_mapping(runtime_config, fallback=config)
    except TypeError:
        return config
