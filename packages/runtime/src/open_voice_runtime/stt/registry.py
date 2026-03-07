from __future__ import annotations

from open_voice_runtime.core.registry import EngineRegistry
from open_voice_runtime.stt.engine import BaseSttEngine


class SttEngineRegistry(EngineRegistry[BaseSttEngine]):
    pass
