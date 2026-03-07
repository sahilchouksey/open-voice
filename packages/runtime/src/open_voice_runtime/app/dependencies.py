from __future__ import annotations

from dataclasses import dataclass

from open_voice_runtime.llm.registry import LlmEngineRegistry
from open_voice_runtime.llm.service import LlmService
from open_voice_runtime.router.registry import RouterEngineRegistry
from open_voice_runtime.router.service import RouterService
from open_voice_runtime.session.manager import InMemorySessionManager, SessionManager
from open_voice_runtime.stt.registry import SttEngineRegistry
from open_voice_runtime.stt.service import SttService
from open_voice_runtime.tts.registry import TtsEngineRegistry
from open_voice_runtime.tts.service import TtsService


@dataclass(slots=True)
class RuntimeDependencies:
    stt_registry: SttEngineRegistry
    router_registry: RouterEngineRegistry
    llm_registry: LlmEngineRegistry
    tts_registry: TtsEngineRegistry
    stt_service: SttService
    router_service: RouterService
    llm_service: LlmService
    tts_service: TtsService
    session_manager: SessionManager


def build_runtime_dependencies() -> RuntimeDependencies:
    stt_registry = SttEngineRegistry()
    router_registry = RouterEngineRegistry()
    llm_registry = LlmEngineRegistry()
    tts_registry = TtsEngineRegistry()

    return RuntimeDependencies(
        stt_registry=stt_registry,
        router_registry=router_registry,
        llm_registry=llm_registry,
        tts_registry=tts_registry,
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
        session_manager=InMemorySessionManager(),
    )
