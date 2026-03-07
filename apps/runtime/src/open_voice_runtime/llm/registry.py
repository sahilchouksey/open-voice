from __future__ import annotations

from open_voice_runtime.core.registry import EngineRegistry
from open_voice_runtime.llm.engine import BaseLlmEngine


class LlmEngineRegistry(EngineRegistry[BaseLlmEngine]):
    pass
