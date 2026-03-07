from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from open_voice_runtime.llm.contracts import LlmCapabilities, LlmEvent, LlmRequest, LlmResponse


class BaseLlmEngine(ABC):
    kind = "llm"
    id: str
    label: str
    capabilities: LlmCapabilities

    @abstractmethod
    async def load(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def complete(self, request: LlmRequest) -> LlmResponse:
        raise NotImplementedError

    @abstractmethod
    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmEvent]:
        raise NotImplementedError
