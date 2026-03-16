from __future__ import annotations

from open_voice_runtime.integrations.arch_router import ArchRouterClient, default_arch_routes
from open_voice_runtime.router.contracts import (
    RouteCostTier,
    RouteDecision,
    RouteLatencyTier,
    RouteRequest,
    RouteTarget,
    RouterCapabilities,
)
from open_voice_runtime.router.engine import BaseRouterEngine
from open_voice_runtime.router.policy import select_route_target


class ArchRouterEngine(BaseRouterEngine):
    id = "arch-router"
    label = "Arch Router 1.5B"
    capabilities = RouterCapabilities(explanations=True, profile_selection=True)

    def __init__(self, client: ArchRouterClient | None = None) -> None:
        self._client = client or ArchRouterClient()
        self._routes = default_arch_routes()

    @property
    def available(self) -> bool:
        return self._client.available

    @property
    def status(self) -> str:
        return self._client.status

    async def load(self) -> None:
        await self._client.load()

    async def close(self) -> None:
        return None

    async def route(self, request: RouteRequest) -> RouteDecision:
        result = await self._client.classify(request.user_text, self._routes)
        latency_tier, cost_tier = _route_tiers(result.route_name)
        target = select_route_target(result.route_name, request.available_targets)
        return RouteDecision(
            router_id=self.id,
            route_name=result.route_name,
            llm_engine_id=target.llm_engine_id if target else None,
            provider=target.provider if target else None,
            model=target.model if target else None,
            profile_id=target.profile_id if target else None,
            reason=_build_reason(result.route_name, target),
            confidence=result.confidence,
            latency_tier=latency_tier,
            cost_tier=cost_tier,
            metadata={
                "backend": result.backend,
                "raw_response": result.raw_response,
                "router_error": result.error,
            },
        )


def _route_tiers(route_name: str) -> tuple[RouteLatencyTier, RouteCostTier]:
    key = route_name.lower().strip()
    if key == "trivial_route":
        return RouteLatencyTier.LOW, RouteCostTier.LOW
    if key == "simple_route":
        return RouteLatencyTier.LOW, RouteCostTier.MEDIUM
    if key == "moderate_route":
        return RouteLatencyTier.MEDIUM, RouteCostTier.MEDIUM
    if key == "complex_route":
        return RouteLatencyTier.HIGH, RouteCostTier.MEDIUM
    if key == "expert_route":
        return RouteLatencyTier.HIGH, RouteCostTier.HIGH
    return RouteLatencyTier.MEDIUM, RouteCostTier.MEDIUM


def _build_reason(route_name: str, target: RouteTarget | None) -> str:
    if target is None:
        return f"Selected route '{route_name}' using Arch Router classification."
    return (
        f"Selected route '{route_name}' using Arch Router classification and mapped it to "
        f"{target.provider}:{target.model}."
    )
