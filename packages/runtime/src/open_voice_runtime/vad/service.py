from __future__ import annotations

from open_voice_runtime.core.errors import EngineRegistryError
from open_voice_runtime.vad.contracts import VadConfig
from open_voice_runtime.vad.engine import BaseVadStream
from open_voice_runtime.vad.registry import VadEngineRegistry


class VadService:
    def __init__(self, registry: VadEngineRegistry) -> None:
        self._registry = registry

    def is_available(self, engine_id: str | None = None) -> bool:
        try:
            engine = self._registry.resolve(engine_id)
        except EngineRegistryError:
            return False
        return getattr(engine, "available", True)

    async def create_stream(
        self,
        config: VadConfig,
        *,
        engine_id: str | None = None,
    ) -> BaseVadStream:
        engine = self._registry.resolve(engine_id)
        await engine.load()
        return await engine.create_stream(config)
