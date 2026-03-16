from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from open_voice_runtime.core.errors import EngineRegistryError


class EngineLike(Protocol):
    id: str
    kind: str
    label: str
    capabilities: Any


T = TypeVar("T", bound=EngineLike)


@dataclass(frozen=True, slots=True)
class EngineDescriptor:
    id: str
    kind: str
    label: str
    default: bool
    capabilities: Any
    available: bool = True
    status: str = "ready"


class EngineRegistry(Generic[T]):
    def __init__(self, *, default_engine_id: str | None = None) -> None:
        self._engines: dict[str, T] = {}
        self._default_engine_id = default_engine_id

    def register(self, engine: T, *, default: bool = False) -> None:
        self._engines[engine.id] = engine
        if default or self._default_engine_id is None:
            self._default_engine_id = engine.id

    def get(self, engine_id: str) -> T:
        try:
            return self._engines[engine_id]
        except KeyError as exc:
            raise EngineRegistryError(
                f"Engine '{engine_id}' is not registered.",
                details={"engine_id": engine_id, "known_ids": sorted(self._engines)},
            ) from exc

    def has(self, engine_id: str) -> bool:
        return engine_id in self._engines

    def has_default(self) -> bool:
        return self._default_engine_id is not None and self._default_engine_id in self._engines

    def resolve(self, engine_id: str | None) -> T:
        if engine_id is None:
            return self.get_default()
        return self.get(engine_id)

    def get_default(self) -> T:
        if self._default_engine_id is None:
            raise EngineRegistryError("No default engine has been configured.")
        return self.get(self._default_engine_id)

    def list(self) -> list[EngineDescriptor]:
        return [
            EngineDescriptor(
                id=engine.id,
                kind=engine.kind,
                label=engine.label,
                default=engine.id == self._default_engine_id,
                capabilities=engine.capabilities,
                available=getattr(engine, "available", True),
                status=getattr(engine, "status", "ready"),
            )
            for engine in self._engines.values()
        ]
