from __future__ import annotations

from open_voice_runtime.app.dependencies import RuntimeDependencies, build_runtime_dependencies


def bootstrap_runtime() -> RuntimeDependencies:
    return build_runtime_dependencies()
