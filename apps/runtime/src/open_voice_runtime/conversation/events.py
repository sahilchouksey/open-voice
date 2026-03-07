from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TypeAlias

from open_voice_runtime.audio.types import AudioChunk
from open_voice_runtime.core.errors import ErrorCode, OpenVoiceError
from open_voice_runtime.core.ids import new_event_id


def event_timestamp() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass(slots=True)
class BaseConversationEvent:
    type: str
    session_id: str
    turn_id: str | None = None
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
class RouteSelectedEvent(BaseConversationEvent):
    router_id: str = ""
    llm_engine_id: str | None = None
    provider: str | None = None
    model: str | None = None
    reason: str | None = None
    confidence: float | None = None

    def __init__(
        self,
        session_id: str,
        router_id: str,
        *,
        turn_id: str | None = None,
        llm_engine_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
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
        self.llm_engine_id = llm_engine_id
        self.provider = provider
        self.model = model
        self.reason = reason
        self.confidence = confidence


@dataclass(slots=True)
class LlmTokenEvent(BaseConversationEvent):
    token: str = ""
    index: int = 0

    def __init__(
        self, session_id: str, token: str, index: int, *, turn_id: str | None = None
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="llm.token",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.token = token
        self.index = index


@dataclass(slots=True)
class LlmCompletedEvent(BaseConversationEvent):
    text: str = ""
    finish_reason: str | None = None

    def __init__(
        self,
        session_id: str,
        text: str,
        *,
        turn_id: str | None = None,
        finish_reason: str | None = None,
    ) -> None:
        BaseConversationEvent.__init__(
            self,
            type="llm.completed",
            session_id=session_id,
            turn_id=turn_id,
        )
        self.text = text
        self.finish_reason = finish_reason


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
    | SttPartialEvent
    | SttFinalEvent
    | RouteSelectedEvent
    | LlmTokenEvent
    | LlmCompletedEvent
    | TtsChunkEvent
    | TtsCompletedEvent
    | ConversationInterruptedEvent
    | ErrorEvent
    | SessionClosedEvent
)
