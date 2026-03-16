from __future__ import annotations

from dataclasses import dataclass

from open_voice_runtime.app.catalog import build_engine_catalog
from open_voice_runtime.app.config import RuntimeConfig
from open_voice_runtime.core.registry import EngineDescriptor
from open_voice_runtime.integrations.arch_router import arch_router_backend_available
from open_voice_runtime.integrations.moonshine_voice import moonshine_voice_available
from open_voice_runtime.integrations.opencode import opencode_backend_available
from open_voice_runtime.llm.engines import OpencodeLlmEngine
from open_voice_runtime.llm.registry import LlmEngineRegistry
from open_voice_runtime.llm.service import LlmService
from open_voice_runtime.router.engines import ArchRouterEngine
from open_voice_runtime.router.registry import RouterEngineRegistry
from open_voice_runtime.router.service import RouterService
import os
from open_voice_runtime.observability.trace_sink import TraceSink
from open_voice_runtime.session.manager import InMemorySessionManager, SessionManager
from open_voice_runtime.session.redis import RedisSessionManager
from open_voice_runtime.stt.engines import MoonshineSttEngine
from open_voice_runtime.stt.registry import SttEngineRegistry
from open_voice_runtime.stt.service import SttService
from open_voice_runtime.transport.websocket.handler import RealtimeConnectionHandler
from open_voice_runtime.transport.websocket.session import RealtimeConversationSession
from open_voice_runtime.tts.engines import KokoroTtsEngine
from open_voice_runtime.tts.registry import TtsEngineRegistry
from open_voice_runtime.tts.service import TtsService
from open_voice_runtime.vad.registry import VadEngineRegistry
from open_voice_runtime.vad.service import VadService


@dataclass(slots=True)
class RuntimeDependencies:
    config: RuntimeConfig
    stt_registry: SttEngineRegistry
    vad_registry: VadEngineRegistry
    router_registry: RouterEngineRegistry
    llm_registry: LlmEngineRegistry
    tts_registry: TtsEngineRegistry
    stt_service: SttService
    vad_service: VadService
    router_service: RouterService
    llm_service: LlmService
    tts_service: TtsService
    session_manager: SessionManager
    engine_catalog: dict[str, list[EngineDescriptor]]
    realtime_session: RealtimeConversationSession
    realtime_handler: RealtimeConnectionHandler
    trace_sink: TraceSink


def build_runtime_dependencies(config: RuntimeConfig | None = None) -> RuntimeDependencies:
    if config is None:
        config = RuntimeConfig.from_env()
    stt_registry = SttEngineRegistry()
    vad_registry = VadEngineRegistry()
    router_registry = RouterEngineRegistry()
    llm_registry = LlmEngineRegistry()
    tts_registry = TtsEngineRegistry()
    trace_sink = TraceSink.from_env()

    # Session manager selection: Redis if OPEN_VOICE_REDIS_URL is set, otherwise in-memory
    redis_url = os.getenv("OPEN_VOICE_REDIS_URL")
    if redis_url:
        session_manager: SessionManager = RedisSessionManager(redis_url)
    else:
        session_manager = InMemorySessionManager()
    stt_service = SttService(stt_registry)
    vad_service = VadService(vad_registry)
    router_service = RouterService(router_registry)
    llm_service = LlmService(llm_registry)
    tts_service = TtsService(tts_registry)

    if moonshine_voice_available():
        stt_registry.register(MoonshineSttEngine(), default=True)

    try:
        from open_voice_runtime.vad.engines import SileroVadEngine

        vad_registry.register(SileroVadEngine(), default=True)
    except Exception:
        pass

    if arch_router_backend_available():
        router_registry.register(ArchRouterEngine(), default=True)

    if opencode_backend_available():
        llm_registry.register(OpencodeLlmEngine(), default=True)

    tts_registry.register(KokoroTtsEngine(), default=True)

    engine_catalog = build_engine_catalog(
        stt_entries=stt_registry.list(),
        vad_entries=vad_registry.list(),
        router_entries=router_registry.list(),
        llm_entries=llm_registry.list(),
        tts_entries=tts_registry.list(),
    )

    realtime_session = RealtimeConversationSession(
        session_manager,
        config=config,
        stt_service=stt_service,
        vad_service=vad_service,
        router_service=router_service,
        llm_service=llm_service,
        tts_service=tts_service,
    )

    return RuntimeDependencies(
        config=config,
        stt_registry=stt_registry,
        vad_registry=vad_registry,
        router_registry=router_registry,
        llm_registry=llm_registry,
        tts_registry=tts_registry,
        stt_service=stt_service,
        vad_service=vad_service,
        router_service=router_service,
        llm_service=llm_service,
        tts_service=tts_service,
        session_manager=session_manager,
        engine_catalog=engine_catalog,
        realtime_session=realtime_session,
        realtime_handler=RealtimeConnectionHandler(realtime_session, trace_sink=trace_sink),
        trace_sink=trace_sink,
    )
