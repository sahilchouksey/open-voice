from __future__ import annotations

from abc import ABC, abstractmethod

from open_voice_runtime.router.contracts import RouteDecision, RouteRequest, RouterCapabilities


class BaseRouterEngine(ABC):
    kind = "router"
    id: str
    label: str
    capabilities: RouterCapabilities

    @abstractmethod
    async def load(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def route(self, request: RouteRequest) -> RouteDecision:
        raise NotImplementedError
