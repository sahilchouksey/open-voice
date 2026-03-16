from __future__ import annotations

from abc import ABC, abstractmethod

from open_voice_runtime.audio.types import AudioChunk
from open_voice_runtime.vad.contracts import VadCapabilities, VadConfig, VadResult


class BaseVadStream(ABC):
    @abstractmethod
    async def push_audio(self, chunk: AudioChunk) -> VadResult:
        raise NotImplementedError

    @abstractmethod
    async def flush(self) -> VadResult:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class BaseVadEngine(ABC):
    kind = "vad"
    id: str
    label: str
    capabilities: VadCapabilities

    @abstractmethod
    async def load(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def create_stream(self, config: VadConfig) -> BaseVadStream:
        raise NotImplementedError
