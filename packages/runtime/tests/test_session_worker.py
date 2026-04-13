from __future__ import annotations

import asyncio

from open_voice_runtime.app.config import RuntimeConfig
from open_voice_runtime.audio.types import AudioChunk, AudioEncoding, AudioFormat
from open_voice_runtime.llm.contracts import (
    LlmCapabilities,
    LlmEvent,
    LlmEventKind,
    LlmOutputLane,
    LlmPhase,
    LlmRequest,
    LlmResponse,
)
from open_voice_runtime.llm.engine import BaseLlmEngine
from open_voice_runtime.llm.registry import LlmEngineRegistry
from open_voice_runtime.llm.service import LlmService
from open_voice_runtime.router.contracts import RouteDecision, RouteRequest, RouterCapabilities
from open_voice_runtime.router.engine import BaseRouterEngine
from open_voice_runtime.router.registry import RouterEngineRegistry
from open_voice_runtime.router.service import RouterService
from open_voice_runtime.session.manager import InMemorySessionManager
from open_voice_runtime.session_worker.host import WorkerHost
from open_voice_runtime.stt.contracts import (
    SttCapabilities,
    SttConfig,
    SttFileRequest,
    SttFileResult,
)
from open_voice_runtime.stt.engine import BaseSttEngine, BaseSttStream
from open_voice_runtime.stt.registry import SttEngineRegistry
from open_voice_runtime.stt.service import SttService
from open_voice_runtime.tts.contracts import (
    TtsCapabilities,
    TtsEvent,
    TtsEventKind,
    TtsRequest,
    TtsResult,
)
from open_voice_runtime.tts.engine import BaseTtsEngine
from open_voice_runtime.tts.registry import TtsEngineRegistry
from open_voice_runtime.tts.service import TtsService
from open_voice_runtime.vad.contracts import (
    VadCapabilities,
    VadConfig,
    VadEvent,
    VadEventKind,
    VadResult,
)
from open_voice_runtime.vad.engine import BaseVadEngine, BaseVadStream
from open_voice_runtime.vad.registry import VadEngineRegistry
from open_voice_runtime.vad.service import VadService


class WorkerFakeSttStream(BaseSttStream):
    async def push_audio(self, chunk: AudioChunk) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def drain(self, wait_seconds: float = 0.0):
        return []

    async def events(self):
        if False:
            yield None


class WorkerFakeSttEngine(BaseSttEngine):
    id = "fake-stt"
    label = "Fake STT"
    capabilities = SttCapabilities(streaming=False, batch=True, partial_results=False)

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_stream(self, config: SttConfig) -> BaseSttStream:
        return WorkerFakeSttStream()

    async def transcribe_file(self, request: SttFileRequest) -> SttFileResult:
        return SttFileResult(text="worker transcript", confidence=0.9)


class WorkerFakeVadStream(BaseVadStream):
    def __init__(self) -> None:
        self._sequence = 0

    async def push_audio(self, chunk: AudioChunk) -> VadResult:
        events = [
            VadEvent(
                kind=VadEventKind.START_OF_SPEECH,
                sequence=self._sequence,
                timestamp_ms=0.0,
                speaking=True,
            ),
            VadEvent(
                kind=VadEventKind.END_OF_SPEECH,
                sequence=self._sequence + 1,
                timestamp_ms=500.0,
                speaking=False,
            ),
        ]
        self._sequence += 2
        return VadResult(events=events)

    async def flush(self) -> VadResult:
        return VadResult()

    async def close(self) -> None:
        return None


class WorkerFakeVadEngine(BaseVadEngine):
    id = "fake-vad"
    label = "Fake VAD"
    capabilities = VadCapabilities(streaming=True, sample_rates_hz=(16000,))

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_stream(self, config: VadConfig) -> BaseVadStream:
        return WorkerFakeVadStream()


class WorkerFakeRouterEngine(BaseRouterEngine):
    id = "fake-router"
    label = "Fake Router"
    capabilities = RouterCapabilities()

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def route(self, request: RouteRequest) -> RouteDecision:
        return RouteDecision(
            router_id=self.id,
            route_name="moderate_route",
            llm_engine_id="fake-llm",
            provider="test",
            model="test-model",
            profile_id="moderate_route",
            confidence=0.9,
        )


class WorkerFakeLlmEngine(BaseLlmEngine):
    id = "fake-llm"
    label = "Fake LLM"
    capabilities = LlmCapabilities(streaming=True)

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(text="hello from llm")

    def stream(self, request: LlmRequest):
        async def generator():
            yield LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING)
            yield LlmEvent(
                kind=LlmEventKind.RESPONSE_DELTA,
                text="hello from llm",
                lane=LlmOutputLane.SPEECH,
                part_id="part-1",
            )
            yield LlmEvent(
                kind=LlmEventKind.COMPLETED,
                text="hello from llm",
                finish_reason="stop",
            )

        return generator()


class WorkerFakeTtsEngine(BaseTtsEngine):
    id = "fake-tts"
    label = "Fake TTS"
    capabilities = TtsCapabilities(streaming=True)

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def synthesize(self, request: TtsRequest) -> TtsResult:
        return TtsResult(audio=b"\x00\x00", audio_format=request.audio_format, duration_ms=40.0)

    async def stream(self, request: TtsRequest):
        async def generator():
            yield TtsEvent(
                kind=TtsEventKind.AUDIO_CHUNK,
                audio_chunk=AudioChunk(
                    data=b"\x00\x00",
                    format=AudioFormat(
                        sample_rate_hz=24000,
                        channels=1,
                        encoding=AudioEncoding.PCM_S16LE,
                    ),
                    sequence=0,
                    duration_ms=40.0,
                ),
                text_segment=request.text,
            )
            yield TtsEvent(kind=TtsEventKind.COMPLETED, duration_ms=40.0)

        return generator()


def test_worker_host_emits_final_only_stt_flow() -> None:
    asyncio.run(_test_worker_host_emits_final_only_stt_flow())


async def _test_worker_host_emits_final_only_stt_flow() -> None:
    session_manager = InMemorySessionManager()

    stt_registry = SttEngineRegistry()
    stt_registry.register(WorkerFakeSttEngine(), default=True)
    vad_registry = VadEngineRegistry()
    vad_registry.register(WorkerFakeVadEngine(), default=True)
    router_registry = RouterEngineRegistry()
    router_registry.register(WorkerFakeRouterEngine(), default=True)
    llm_registry = LlmEngineRegistry()
    llm_registry.register(WorkerFakeLlmEngine(), default=True)
    tts_registry = TtsEngineRegistry()
    tts_registry.register(WorkerFakeTtsEngine(), default=True)

    host = WorkerHost(
        session_manager,
        RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await host.apply({"type": "session.start"})
    session_id = start_events[-1]["session_id"]

    payload = {
        "type": "audio.append",
        "session_id": session_id,
        "chunk": {
            "chunk_id": f"{session_id}:0",
            "sequence": 0,
            "encoding": "pcm_s16le",
            "sample_rate_hz": 16000,
            "channels": 1,
            "duration_ms": 100.0,
            "transport": "inline-base64",
            "data_base64": "AAAA",
        },
    }

    events = await host.apply(payload)
    types = [item["type"] for item in events]
    assert "stt.partial" not in types
    assert "stt.final" in types
    assert any(item["type"] == "stt.status" and item["status"] == "completed" for item in events)
    assert "route.selected" in types
    assert "llm.completed" in types
    assert "tts.completed" in types
