from __future__ import annotations

from collections.abc import AsyncIterator

from open_voice_runtime.llm.contracts import LlmEvent, LlmRequest, LlmResponse
from open_voice_runtime.llm.registry import LlmEngineRegistry


class LlmService:
    def __init__(self, registry: LlmEngineRegistry) -> None:
        self._registry = registry

    def is_available(self, engine_id: str | None = None) -> bool:
        if engine_id is None:
            return self._registry.has_default()
        return self._registry.has(engine_id)

    async def complete(self, request: LlmRequest, *, engine_id: str | None = None) -> LlmResponse:
        engine = self._registry.resolve(engine_id)
        return await engine.complete(request)

    def stream(
        self, request: LlmRequest, *, engine_id: str | None = None
    ) -> AsyncIterator[LlmEvent]:
        engine = self._registry.resolve(engine_id)
        return engine.stream(request)
