from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from open_voice_runtime.audio.types import AudioChunk
from open_voice_runtime.stt.contracts import (
    SttCapabilities,
    SttConfig,
    SttEvent,
    SttFileRequest,
    SttFileResult,
)


class BaseSttStream(ABC):
    @abstractmethod
    async def push_audio(self, chunk: AudioChunk) -> None:
        raise NotImplementedError

    @abstractmethod
    async def flush(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def events(self) -> AsyncIterator[SttEvent]:
        raise NotImplementedError


class BaseSttEngine(ABC):
    kind = "stt"
    id: str
    label: str
    capabilities: SttCapabilities

    @abstractmethod
    async def load(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def create_stream(self, config: SttConfig) -> BaseSttStream:
        raise NotImplementedError

    @abstractmethod
    async def transcribe_file(self, request: SttFileRequest) -> SttFileResult:
        raise NotImplementedError
