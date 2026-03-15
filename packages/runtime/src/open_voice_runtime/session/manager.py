from __future__ import annotations

from abc import ABC, abstractmethod

from open_voice_runtime.core.errors import ErrorCode, OpenVoiceError
from open_voice_runtime.session.models import (
    SessionCreateRequest,
    SessionState,
    SessionStatus,
    SessionTransition,
)
from open_voice_runtime.session.state_machine import transition_session


class SessionManager(ABC):
    @abstractmethod
    async def create(self, request: SessionCreateRequest) -> SessionState:
        raise NotImplementedError

    @abstractmethod
    async def get(self, session_id: str) -> SessionState:
        raise NotImplementedError

    @abstractmethod
    async def update(self, session_id: str, event: SessionTransition) -> SessionState:
        raise NotImplementedError

    async def persist(self, state: SessionState) -> None:
        """Persist current session state (optional, no-op for in-memory managers)."""

    @abstractmethod
    async def close(self, session_id: str) -> None:
        raise NotImplementedError


class InMemorySessionManager(SessionManager):
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    async def create(self, request: SessionCreateRequest) -> SessionState:
        session = SessionState.create(request)
        self._sessions[session.session_id] = session
        return session

    async def get(self, session_id: str) -> SessionState:
        try:
            return self._sessions[session_id]
        except KeyError as exc:
            raise OpenVoiceError(
                code=ErrorCode.SESSION_NOT_FOUND,
                message=f"Session '{session_id}' was not found.",
                retryable=False,
                details={"session_id": session_id},
            ) from exc

    async def update(self, session_id: str, event: SessionTransition) -> SessionState:
        session = await self.get(session_id)
        return transition_session(session, event)

    async def close(self, session_id: str) -> None:
        session = await self.get(session_id)
        if session.status.value != "closed":
            transition_session(session, SessionTransition(to_status=SessionStatus.CLOSED))
