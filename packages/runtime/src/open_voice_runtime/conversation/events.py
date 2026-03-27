from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypeAlias

from open_voice_runtime.audio.types import AudioChunk
from open_voice_runtime.core.errors import ErrorCode, OpenVoiceError
from open_voice_runtime.core.ids import new_event_id
from open_voice_runtime.llm.contracts import LlmOutputLane, LlmPhase, TokenUsage
from open_voice_runtime.session.models import SessionStatus
from open_voice_runtime.vad.contracts import VadEventKind


def event_timestamp() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(slots=True)
class BaseConversationEvent:
    type: str
    session_id: str
    turn_id: str | None = None
    generation_id: str | None = None
    event_id: str = field(default_factory=new_event_id)
    timestamp: datetime = field(default_factory=event_timestamp)


@dataclass(slots=True)
class SessionCreatedEvent(BaseConversationEvent):
    status: str = "created"

    def __init__(self, session_id: str, turn_id: str | None = None) -> None:
        BaseConversationEvent.__init__(
            self,
            type="session.created",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.status = "created"


@dataclass(slots=True)
class SessionReadyEvent(BaseConversationEvent):
    status: str = "ready"

    def __init__(self, session_id: str, turn_id: str | None = None) -> None:
        BaseConversationEvent.__init__(
            self,
            type="session.ready",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.status = "ready"


@dataclass(slots=True)
class SessionStatusEvent(BaseConversationEvent):
    status: SessionStatus = SessionStatus.READY
    reason: str | None = None

    def __init__(
        self,
        session_id: str,
        status: SessionStatus,
        *,
        turn_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="session.status",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.status = status
        self.reason = reason


@dataclass(slots=True)
class VadStateEvent(BaseConversationEvent):
    kind: VadEventKind = VadEventKind.INFERENCE
    sequence: int = 0
    speaking: bool | None = None
    probability: float | None = None
    timestamp_ms: float | None = None
    speech_duration_ms: float | None = None
    silence_duration_ms: float | None = None

    def __init__(
        self,
        session_id: str,
        *,
        kind: VadEventKind,
        sequence: int,
        turn_id: str | None = None,
        speaking: bool | None = None,
        probability: float | None = None,
        timestamp_ms: float | None = None,
        speech_duration_ms: float | None = None,
        silence_duration_ms: float | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="vad.state",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.kind = kind
        self.sequence = sequence
        self.speaking = speaking
        self.probability = probability
        self.timestamp_ms = timestamp_ms
        self.speech_duration_ms = speech_duration_ms
        self.silence_duration_ms = silence_duration_ms


@dataclass(slots=True)
class SttPartialEvent(BaseConversationEvent):
    text: str = ""
    confidence: float | None = None

    def __init__(
        self,
        session_id: str,
        text: str,
        *,
        turn_id: str | None = None,
        confidence: float | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="stt.partial",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.text = text
        self.confidence = confidence


@dataclass(slots=True)
class SttFinalEvent(BaseConversationEvent):
    text: str = ""
    confidence: float | None = None

    def __init__(
        self,
        session_id: str,
        text: str,
        *,
        turn_id: str | None = None,
        confidence: float | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="stt.final",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.text = text
        self.confidence = confidence


@dataclass(slots=True)
class SttStatusEvent(BaseConversationEvent):
    status: str = "queued"
    waited_ms: int | None = None
    attempt: int | None = None

    def __init__(
        self,
        session_id: str,
        status: str,
        *,
        turn_id: str | None = None,
        waited_ms: int | None = None,
        attempt: int | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="stt.status",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.status = status
        self.waited_ms = waited_ms
        self.attempt = attempt


@dataclass(slots=True)
class RouteSelectedEvent(BaseConversationEvent):
    router_id: str = ""
    route_name: str = ""
    llm_engine_id: str | None = None
    provider: str | None = None
    model: str | None = None
    profile_id: str | None = None
    reason: str | None = None
    confidence: float | None = None

    def __init__(
        self,
        session_id: str,
        router_id: str,
        *,
        turn_id: str | None = None,
        route_name: str,
        llm_engine_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        profile_id: str | None = None,
        reason: str | None = None,
        confidence: float | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="route.selected",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.router_id = router_id
        self.route_name = route_name
        self.llm_engine_id = llm_engine_id
        self.provider = provider
        self.model = model
        self.profile_id = profile_id
        self.reason = reason
        self.confidence = confidence


@dataclass(slots=True)
class LlmPhaseEvent(BaseConversationEvent):
    phase: LlmPhase = LlmPhase.THINKING

    def __init__(self, session_id: str, phase: LlmPhase, *, turn_id: str | None = None) -> None:
        BaseConversationEvent.__init__(
            self,
            type="llm.phase",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.phase = phase


@dataclass(slots=True)
class LlmReasoningDeltaEvent(BaseConversationEvent):
    part_id: str | None = None
    delta: str = ""

    def __init__(
        self,
        session_id: str,
        delta: str,
        *,
        turn_id: str | None = None,
        part_id: str | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="llm.reasoning.delta",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.part_id = part_id
        self.delta = delta


@dataclass(slots=True)
class LlmResponseDeltaEvent(BaseConversationEvent):
    part_id: str | None = None
    delta: str = ""
    lane: LlmOutputLane = LlmOutputLane.DISPLAY

    def __init__(
        self,
        session_id: str,
        delta: str,
        *,
        turn_id: str | None = None,
        part_id: str | None = None,
        lane: LlmOutputLane = LlmOutputLane.DISPLAY,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="llm.response.delta",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.part_id = part_id
        self.delta = delta
        self.lane = lane


@dataclass(slots=True)
class LlmToolUpdateEvent(BaseConversationEvent):
    call_id: str | None = None
    tool_name: str = ""
    status: str | None = None
    tool_input: Any | None = None
    tool_metadata: dict[str, Any] = field(default_factory=dict)
    tool_output: Any | None = None
    tool_error: Any | None = None
    is_mcp: bool = False

    def __init__(
        self,
        session_id: str,
        tool_name: str,
        *,
        turn_id: str | None = None,
        call_id: str | None = None,
        status: str | None = None,
        tool_input: Any | None = None,
        tool_metadata: dict[str, Any] | None = None,
        tool_output: Any | None = None,
        tool_error: Any | None = None,
        is_mcp: bool = False,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="llm.tool.update",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.call_id = call_id
        self.tool_name = tool_name
        self.status = status
        self.tool_input = tool_input
        self.tool_metadata = dict(tool_metadata or {})
        self.tool_output = tool_output
        self.tool_error = tool_error
        self.is_mcp = is_mcp


@dataclass(slots=True)
class LlmUsageEvent(BaseConversationEvent):
    usage: TokenUsage | None = None
    cost: float | None = None

    def __init__(
        self,
        session_id: str,
        *,
        turn_id: str | None = None,
        usage: TokenUsage | None = None,
        cost: float | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="llm.usage",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.usage = usage
        self.cost = cost


@dataclass(slots=True)
class LlmSummaryEvent(BaseConversationEvent):
    provider: str | None = None
    model: str | None = None
    usage: TokenUsage | None = None
    cost: float | None = None
    metadata: dict[str, Any] | None = None

    def __init__(
        self,
        session_id: str,
        *,
        turn_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        usage: TokenUsage | None = None,
        cost: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="llm.summary",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.provider = provider
        self.model = model
        self.usage = usage
        self.cost = cost
        self.metadata = dict(metadata) if isinstance(metadata, dict) and metadata else None


@dataclass(slots=True)
class LlmCompletedEvent(BaseConversationEvent):
    text: str = ""
    finish_reason: str | None = None
    provider: str | None = None
    model: str | None = None

    def __init__(
        self,
        session_id: str,
        text: str,
        *,
        turn_id: str | None = None,
        finish_reason: str | None = None,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="llm.completed",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.text = text
        self.finish_reason = finish_reason
        self.provider = provider
        self.model = model


@dataclass(slots=True)
class LlmErrorEvent(BaseConversationEvent):
    error: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self,
        session_id: str,
        *,
        code: str,
        message: str,
        retryable: bool,
        turn_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="llm.error",
            session_id=session_id,
            turn_id=turn_id,
        )
        payload: dict[str, Any] = {
            "code": code,
            "message": message,
            "retryable": retryable,
        }
        if details:
            payload["details"] = dict(details)
        self.error = payload


@dataclass(slots=True)
class TtsChunkEvent(BaseConversationEvent):
    chunk: AudioChunk | None = None
    text_segment: str | None = None

    def __init__(
        self,
        session_id: str,
        chunk: AudioChunk,
        *,
        turn_id: str | None = None,
        text_segment: str | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="tts.chunk",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.chunk = chunk
        self.text_segment = text_segment


@dataclass(slots=True)
class TtsCompletedEvent(BaseConversationEvent):
    duration_ms: float | None = None

    def __init__(
        self, session_id: str, *, turn_id: str | None = None, duration_ms: float | None = None
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="tts.completed",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.duration_ms = duration_ms


@dataclass(slots=True)
class ConversationInterruptedEvent(BaseConversationEvent):
    reason: str | None = None

    def __init__(
        self, session_id: str, *, turn_id: str | None = None, reason: str | None = None
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="conversation.interrupted",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.reason = reason


@dataclass(slots=True)
class TurnQueuedEvent(BaseConversationEvent):
    queue_size: int = 0
    source: str | None = None
    policy: str | None = None

    def __init__(
        self,
        session_id: str,
        queue_size: int,
        *,
        turn_id: str | None = None,
        source: str | None = None,
        policy: str | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="turn.queued",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.queue_size = queue_size
        self.source = source
        self.policy = policy


@dataclass(slots=True)
class TurnAcceptedEvent(BaseConversationEvent):
    client_turn_id: str = ""

    def __init__(
        self,
        session_id: str,
        client_turn_id: str,
        *,
        turn_id: str | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="turn.accepted",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.client_turn_id = client_turn_id


@dataclass(slots=True)
class TurnMetricsEvent(BaseConversationEvent):
    queue_delay_ms: float | None = None
    stt_to_route_ms: float | None = None
    route_to_llm_first_delta_ms: float | None = None
    llm_first_delta_to_tts_first_chunk_ms: float | None = None
    stt_to_tts_first_chunk_ms: float | None = None
    turn_to_first_llm_delta_ms: float | None = None
    turn_to_complete_ms: float | None = None
    cancelled: bool = False
    reason: str | None = None

    def __init__(
        self,
        session_id: str,
        *,
        turn_id: str | None = None,
        queue_delay_ms: float | None = None,
        stt_to_route_ms: float | None = None,
        route_to_llm_first_delta_ms: float | None = None,
        llm_first_delta_to_tts_first_chunk_ms: float | None = None,
        stt_to_tts_first_chunk_ms: float | None = None,
        turn_to_first_llm_delta_ms: float | None = None,
        turn_to_complete_ms: float | None = None,
        cancelled: bool = False,
        reason: str | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="turn.metrics",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.queue_delay_ms = queue_delay_ms
        self.stt_to_route_ms = stt_to_route_ms
        self.route_to_llm_first_delta_ms = route_to_llm_first_delta_ms
        self.llm_first_delta_to_tts_first_chunk_ms = llm_first_delta_to_tts_first_chunk_ms
        self.stt_to_tts_first_chunk_ms = stt_to_tts_first_chunk_ms
        self.turn_to_first_llm_delta_ms = turn_to_first_llm_delta_ms
        self.turn_to_complete_ms = turn_to_complete_ms
        self.cancelled = cancelled
        self.reason = reason


@dataclass(slots=True)
class ErrorEvent(BaseConversationEvent):
    code: ErrorCode = ErrorCode.PROVIDER_ERROR
    message: str = ""
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __init__(
        self, session_id: str, error: OpenVoiceError, *, turn_id: str | None = None
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="error",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.code = error.code
        self.message = error.message
        self.retryable = error.retryable
        self.details = dict(error.details)


@dataclass(slots=True)
class SessionClosedEvent(BaseConversationEvent):
    status: str = "closed"

    def __init__(self, session_id: str, turn_id: str | None = None) -> None:
        BaseConversationEvent.__init__(
            self,
            type="session.closed",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.status = "closed"


ConversationEvent: TypeAlias = (
    SessionCreatedEvent
    | SessionReadyEvent
    | SessionStatusEvent
    | VadStateEvent
    | SttPartialEvent
    | SttFinalEvent
    | SttStatusEvent
    | RouteSelectedEvent
    | LlmPhaseEvent
    | LlmReasoningDeltaEvent
    | LlmResponseDeltaEvent
    | LlmToolUpdateEvent
    | LlmUsageEvent
    | LlmSummaryEvent
    | LlmCompletedEvent
    | LlmErrorEvent
    | TtsChunkEvent
    | TtsCompletedEvent
    | ConversationInterruptedEvent
    | TurnAcceptedEvent
    | TurnQueuedEvent
    | TurnMetricsEvent
    | ErrorEvent
    | SessionClosedEvent
)
