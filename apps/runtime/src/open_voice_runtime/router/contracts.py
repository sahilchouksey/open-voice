from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RouteLatencyTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RouteCostTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class RouterCapabilities:
    explanations: bool = True
    profile_selection: bool = True


@dataclass(frozen=True, slots=True)
class RouteTarget:
    llm_engine_id: str
    provider: str | None = None
    model: str | None = None
    profile_id: str | None = None


@dataclass(slots=True)
class RouteRequest:
    session_id: str
    turn_id: str
    user_text: str
    available_targets: tuple[RouteTarget, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RouteDecision:
    router_id: str
    llm_engine_id: str | None = None
    provider: str | None = None
    model: str | None = None
    profile_id: str | None = None
    reason: str | None = None
    confidence: float | None = None
    latency_tier: RouteLatencyTier | None = None
    cost_tier: RouteCostTier | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
