from __future__ import annotations

from open_voice_runtime.stt.contracts import SttConfig, SttFileRequest, SttFileResult
from open_voice_runtime.stt.engine import BaseSttStream
from open_voice_runtime.stt.registry import SttEngineRegistry


class SttService:
    def __init__(self, registry: SttEngineRegistry) -> None:
        self._registry = registry

    def is_available(self, engine_id: str | None = None) -> bool:
        if engine_id is None:
            return self._registry.has_default()
        return self._registry.has(engine_id)

    async def create_stream(
        self, config: SttConfig, *, engine_id: str | None = None
    ) -> BaseSttStream:
        engine = self._registry.resolve(engine_id)
        return await engine.create_stream(config)

    async def transcribe_file(
        self,
        request: SttFileRequest,
        *,
        engine_id: str | None = None,
    ) -> SttFileResult:
        engine = self._registry.resolve(engine_id)
        return await engine.transcribe_file(request)
