from __future__ import annotations

from collections.abc import AsyncIterator

from open_voice_runtime.tts.contracts import TtsEvent, TtsRequest, TtsResult
from open_voice_runtime.tts.registry import TtsEngineRegistry


class TtsService:
    def __init__(self, registry: TtsEngineRegistry) -> None:
        self._registry = registry

    async def synthesize(self, request: TtsRequest, *, engine_id: str | None = None) -> TtsResult:
        engine = self._registry.resolve(engine_id)
        return await engine.synthesize(request)

    async def stream(
        self, request: TtsRequest, *, engine_id: str | None = None
    ) -> AsyncIterator[TtsEvent]:
        engine = self._registry.resolve(engine_id)
        return await engine.stream(request)
