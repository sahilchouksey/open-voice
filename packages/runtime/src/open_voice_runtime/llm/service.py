from __future__ import annotations

from collections.abc import AsyncIterator

from open_voice_runtime.llm.contracts import LlmEvent, LlmRequest, LlmResponse
from open_voice_runtime.llm.registry import LlmEngineRegistry


class LlmService:
    def __init__(self, registry: LlmEngineRegistry) -> None:
        self._registry = registry

    async def complete(self, request: LlmRequest, *, engine_id: str | None = None) -> LlmResponse:
        engine = self._registry.resolve(engine_id)
        return await engine.complete(request)

    async def stream(
        self, request: LlmRequest, *, engine_id: str | None = None
    ) -> AsyncIterator[LlmEvent]:
        engine = self._registry.resolve(engine_id)
        return await engine.stream(request)
