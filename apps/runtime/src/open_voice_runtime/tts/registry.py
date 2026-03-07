from __future__ import annotations

from open_voice_runtime.core.registry import EngineRegistry
from open_voice_runtime.tts.engine import BaseTtsEngine


class TtsEngineRegistry(EngineRegistry[BaseTtsEngine]):
    pass
