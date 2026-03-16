from __future__ import annotations

from open_voice_runtime.router.contracts import RouteDecision, RouteRequest
from open_voice_runtime.router.registry import RouterEngineRegistry


class RouterService:
    def __init__(self, registry: RouterEngineRegistry) -> None:
        self._registry = registry

    def is_available(self, engine_id: str | None = None) -> bool:
        if engine_id is None:
            return self._registry.has_default()
        return self._registry.has(engine_id)

    async def route(self, request: RouteRequest, *, engine_id: str | None = None) -> RouteDecision:
        engine = self._registry.resolve(engine_id)
        return await engine.route(request)
