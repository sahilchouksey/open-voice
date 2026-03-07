from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from open_voice_runtime.tts.contracts import TtsCapabilities, TtsEvent, TtsRequest, TtsResult


class BaseTtsEngine(ABC):
    kind = "tts"
    id: str
    label: str
    capabilities: TtsCapabilities

    @abstractmethod
    async def load(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def synthesize(self, request: TtsRequest) -> TtsResult:
        raise NotImplementedError

    @abstractmethod
    async def stream(self, request: TtsRequest) -> AsyncIterator[TtsEvent]:
        raise NotImplementedError
