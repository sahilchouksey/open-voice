from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from open_voice_runtime.core.ids import new_session_id, new_turn_id


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class SessionStatus(str, Enum):
    CREATED = "created"
    LOADING = "loading"
    READY = "ready"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class EngineSelection:
    stt: str | None = None
    router: str | None = None
    llm: str | None = None
    tts: str | None = None


@dataclass(slots=True)
class SessionTurn:
    turn_id: str
    user_text: str | None = None
    assistant_text: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None


@dataclass(slots=True)
class SessionCreateRequest:
    engine_selection: EngineSelection = field(default_factory=EngineSelection)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionTransition:
    to_status: SessionStatus
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SessionState:
    session_id: str
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    engine_selection: EngineSelection
    active_turn_id: str | None = None
    turns: list[SessionTurn] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, request: SessionCreateRequest) -> "SessionState":
        now = utc_now()
        return cls(
            session_id=new_session_id(),
            status=SessionStatus.CREATED,
            created_at=now,
            updated_at=now,
            engine_selection=request.engine_selection,
            metadata=dict(request.metadata),
        )

    def begin_turn(self) -> str:
        turn_id = new_turn_id()
        self.active_turn_id = turn_id
        self.turns.append(SessionTurn(turn_id=turn_id))
        self.updated_at = utc_now()
        return turn_id

    def current_turn(self) -> SessionTurn | None:
        if self.active_turn_id is None:
            return None
        for turn in reversed(self.turns):
            if turn.turn_id == self.active_turn_id:
                return turn
        return None

    def complete_turn(
        self,
        *,
        user_text: str | None = None,
        assistant_text: str | None = None,
    ) -> SessionTurn | None:
        turn = self.current_turn()
        if turn is None:
            return None
        if user_text is not None:
            turn.user_text = user_text
        if assistant_text is not None:
            turn.assistant_text = assistant_text
        turn.completed_at = utc_now()
        self.active_turn_id = None
        self.updated_at = utc_now()
        return turn

    def touch(self) -> None:
        self.updated_at = utc_now()

    def with_status(self, status: SessionStatus) -> "SessionState":
        return replace(self, status=status, updated_at=utc_now())
