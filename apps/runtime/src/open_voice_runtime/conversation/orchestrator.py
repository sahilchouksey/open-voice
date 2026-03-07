from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from open_voice_runtime.audio.types import AudioChunk
from open_voice_runtime.conversation.events import ConversationEvent
from open_voice_runtime.session.models import SessionCreateRequest, SessionState


@dataclass(slots=True)
class ConversationStartRequest:
    session: SessionCreateRequest = field(default_factory=SessionCreateRequest)


class ConversationOrchestrator(ABC):
    @abstractmethod
    async def start_session(self, request: ConversationStartRequest) -> SessionState:
        raise NotImplementedError

    @abstractmethod
    async def handle_audio_chunk(self, session_id: str, chunk: AudioChunk) -> None:
        raise NotImplementedError

    @abstractmethod
    async def interrupt(self, session_id: str) -> None:
        raise NotImplementedError

    @abstractmethod
    async def events(self, session_id: str) -> AsyncIterator[ConversationEvent]:
        raise NotImplementedError

    @abstractmethod
    async def close_session(self, session_id: str) -> None:
        raise NotImplementedError
