from __future__ import annotations

from dataclasses import dataclass

from open_voice_runtime.app.dependencies import RuntimeDependencies
from open_voice_runtime.app.bootstrap import bootstrap_runtime


@dataclass(slots=True)
class RuntimeServer:
    dependencies: RuntimeDependencies

    def health(self) -> dict[str, str]:
        return {"status": "ok"}


def create_server() -> RuntimeServer:
    return RuntimeServer(dependencies=bootstrap_runtime())
