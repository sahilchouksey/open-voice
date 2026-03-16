from __future__ import annotations

from open_voice_runtime.core.registry import EngineRegistry
from open_voice_runtime.vad.engine import BaseVadEngine


class VadEngineRegistry(EngineRegistry[BaseVadEngine]):
    pass
