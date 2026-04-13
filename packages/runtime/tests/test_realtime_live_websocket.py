from __future__ import annotations

import asyncio
import base64
import threading
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.testclient import TestClient

from open_voice_runtime.app.config import RuntimeConfig
from open_voice_runtime.audio.types import AudioChunk
from open_voice_runtime.audio.types import AudioEncoding
from open_voice_runtime.audio.types import AudioFormat
from open_voice_runtime.llm.contracts import LlmCapabilities
from open_voice_runtime.llm.contracts import LlmEvent
from open_voice_runtime.llm.contracts import LlmEventKind
from open_voice_runtime.llm.contracts import LlmOutputLane
from open_voice_runtime.llm.contracts import LlmPhase
from open_voice_runtime.llm.contracts import LlmRequest
from open_voice_runtime.llm.contracts import LlmResponse
from open_voice_runtime.llm.engine import BaseLlmEngine
from open_voice_runtime.llm.registry import LlmEngineRegistry
from open_voice_runtime.llm.service import LlmService
from open_voice_runtime.router.contracts import RouteDecision
from open_voice_runtime.router.contracts import RouteRequest
from open_voice_runtime.router.contracts import RouterCapabilities
from open_voice_runtime.router.engine import BaseRouterEngine
from open_voice_runtime.router.policy import select_route_target
from open_voice_runtime.router.registry import RouterEngineRegistry
from open_voice_runtime.router.service import RouterService
from open_voice_runtime.session.manager import InMemorySessionManager
from open_voice_runtime.stt.contracts import SttCapabilities
from open_voice_runtime.stt.contracts import SttConfig
from open_voice_runtime.stt.contracts import SttEvent
from open_voice_runtime.stt.contracts import SttEventKind
from open_voice_runtime.stt.contracts import SttFileRequest
from open_voice_runtime.stt.contracts import SttFileResult
from open_voice_runtime.stt.engine import BaseSttEngine
from open_voice_runtime.stt.engine import BaseSttStream
from open_voice_runtime.stt.registry import SttEngineRegistry
from open_voice_runtime.stt.service import SttService
from open_voice_runtime.transport.websocket.fastapi import install_realtime_route
from open_voice_runtime.transport.websocket.handler import RealtimeConnectionHandler
from open_voice_runtime.transport.websocket.session import RealtimeConversationSession
from open_voice_runtime.tts.contracts import TtsCapabilities
from open_voice_runtime.tts.contracts import TtsEvent
from open_voice_runtime.tts.contracts import TtsEventKind
from open_voice_runtime.tts.contracts import TtsRequest
from open_voice_runtime.tts.contracts import TtsResult
from open_voice_runtime.tts.engine import BaseTtsEngine
from open_voice_runtime.tts.registry import TtsEngineRegistry
from open_voice_runtime.tts.service import TtsService
from open_voice_runtime.vad.contracts import VadCapabilities
from open_voice_runtime.vad.contracts import VadConfig
from open_voice_runtime.vad.contracts import VadEvent
from open_voice_runtime.vad.contracts import VadEventKind
from open_voice_runtime.vad.contracts import VadResult
from open_voice_runtime.vad.engine import BaseVadEngine
from open_voice_runtime.vad.engine import BaseVadStream
from open_voice_runtime.vad.registry import VadEngineRegistry
from open_voice_runtime.vad.service import VadService


class LiveFakeSttStream(BaseSttStream):
    def __init__(self, engine: LiveFakeSttEngine) -> None:
        self._engine = engine
        self._queue: list[SttEvent] = []

    async def push_audio(self, chunk: AudioChunk) -> None:
        return None

    async def flush(self) -> None:
        self._engine.turn_index += 1
        self._queue.append(
            SttEvent(
                kind=SttEventKind.FINAL,
                text=f"turn-{self._engine.turn_index}",
                sequence=self._engine.turn_index,
            )
        )

    async def close(self) -> None:
        return None

    async def drain(self, wait_seconds: float = 0.0) -> list[SttEvent]:
        if self._queue:
            out = list(self._queue)
            self._queue.clear()
            return out
        if wait_seconds > 0:
            await asyncio.sleep(min(wait_seconds, 0.01))
        return []

    async def events(self) -> AsyncIterator[SttEvent]:
        while False:
            yield SttEvent(kind=SttEventKind.PARTIAL, text="", sequence=0)


class LiveFakeSttEngine(BaseSttEngine):
    id = "moonshine"
    label = "Live Fake STT"
    capabilities = SttCapabilities(streaming=True)

    def __init__(self) -> None:
        self.turn_index = 0

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_stream(self, config: SttConfig) -> BaseSttStream:
        return LiveFakeSttStream(self)

    async def transcribe_file(self, request: SttFileRequest) -> SttFileResult:
        self.turn_index += 1
        return SttFileResult(text=f"turn-{self.turn_index}")


class LiveFakeVadStream(BaseVadStream):
    def __init__(self) -> None:
        self._sequence = 0
        self._count = 0

    async def push_audio(self, chunk: AudioChunk) -> VadResult:
        self._count += 1
        events: list[VadEvent] = []

        position = self._count % 4
        if position == 1:
            events.append(
                VadEvent(
                    kind=VadEventKind.START_OF_SPEECH,
                    sequence=self._sequence,
                    timestamp_ms=0.0,
                    speaking=True,
                )
            )
            self._sequence += 1

        speaking = position in {1, 2}

        events.append(
            VadEvent(
                kind=VadEventKind.INFERENCE,
                sequence=self._sequence,
                timestamp_ms=0.0,
                speaking=speaking,
                probability=0.95 if speaking else 0.03,
            )
        )

        if position == 3:
            events.append(
                VadEvent(
                    kind=VadEventKind.END_OF_SPEECH,
                    sequence=self._sequence,
                    timestamp_ms=900.0,
                    speaking=False,
                )
            )
            self._sequence += 1

        return VadResult(events=events)

    async def flush(self) -> VadResult:
        return VadResult()

    async def close(self) -> None:
        return None


class LiveFakeVadEngine(BaseVadEngine):
    id = "silero"
    label = "Live Fake VAD"
    capabilities = VadCapabilities(streaming=True, sample_rates_hz=(16000,))

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_stream(self, config: VadConfig) -> BaseVadStream:
        return LiveFakeVadStream()


class LiveFakeRouterEngine(BaseRouterEngine):
    id = "fake-router"
    label = "Live Fake Router"
    capabilities = RouterCapabilities()

    def __init__(self) -> None:
        self.requests: list[RouteRequest] = []

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def route(self, request: RouteRequest) -> RouteDecision:
        self.requests.append(request)
        target = select_route_target("moderate_route", request.available_targets)
        return RouteDecision(
            router_id=self.id,
            route_name="moderate_route",
            llm_engine_id=target.llm_engine_id if target else "opencode",
            provider=target.provider if target else "opencode",
            model=target.model if target else "minimax-m2.5-free",
            profile_id=target.profile_id if target else "moderate_route",
            confidence=0.95,
        )


class LiveFakeLlmEngine(BaseLlmEngine):
    id = "opencode"
    label = "Live Fake LLM"
    capabilities = LlmCapabilities(streaming=True)

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        self.requests: list[LlmRequest] = []
        self._delay_seconds = delay_seconds

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(text="")

    def stream(self, request: LlmRequest) -> AsyncIterator[LlmEvent]:
        self.requests.append(request)

        async def generator() -> AsyncIterator[LlmEvent]:
            yield LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING)
            if self._delay_seconds > 0:
                await asyncio.sleep(self._delay_seconds)
            yield LlmEvent(
                kind=LlmEventKind.RESPONSE_DELTA,
                text=f"reply:{request.messages[-1].content}",
                lane=LlmOutputLane.SPEECH,
                part_id="part-1",
            )
            yield LlmEvent(
                kind=LlmEventKind.COMPLETED,
                text=f"reply:{request.messages[-1].content}",
                finish_reason="stop",
            )

        return generator()


class LiveFakeTtsEngine(BaseTtsEngine):
    id = "fake-tts"
    label = "Live Fake TTS"
    capabilities = TtsCapabilities(streaming=True)

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def synthesize(self, request: TtsRequest) -> TtsResult:
        return TtsResult(audio=b"\x00\x00", audio_format=request.audio_format, duration_ms=40.0)

    async def stream(self, request: TtsRequest) -> AsyncIterator[TtsEvent]:
        async def generator() -> AsyncIterator[TtsEvent]:
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


class _RealtimeServer:
    def __init__(self, session: RealtimeConversationSession) -> None:
        self._handler = RealtimeConnectionHandler(session)

    def realtime(self) -> RealtimeConnectionHandler:
        return self._handler


def _build_test_app(
    *, llm_delay_seconds: float = 0.0
) -> tuple[FastAPI, LiveFakeRouterEngine, LiveFakeLlmEngine]:
    session_manager = InMemorySessionManager()

    stt_registry = SttEngineRegistry()
    stt_registry.register(LiveFakeSttEngine(), default=True)
    vad_registry = VadEngineRegistry()
    vad_registry.register(LiveFakeVadEngine(), default=True)
    router_registry = RouterEngineRegistry()
    router_engine = LiveFakeRouterEngine()
    router_registry.register(router_engine, default=True)
    llm_registry = LlmEngineRegistry()
    llm_engine = LiveFakeLlmEngine(delay_seconds=llm_delay_seconds)
    llm_registry.register(llm_engine, default=True)
    tts_registry = TtsEngineRegistry()
    tts_registry.register(LiveFakeTtsEngine(), default=True)

    realtime = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    app = FastAPI()
    install_realtime_route(app, _RealtimeServer(realtime))
    return app, router_engine, llm_engine


def _audio_append(session_id: str, sequence: int) -> dict[str, object]:
    payload = base64.b64encode(b"\x00\x00" * 4096).decode("ascii")
    return {
        "type": "audio.append",
        "session_id": session_id,
        "chunk": {
            "chunk_id": f"{session_id}:{sequence}",
            "sequence": sequence,
            "encoding": "pcm_s16le",
            "sample_rate_hz": 16000,
            "channels": 1,
            "duration_ms": 170.0,
            "transport": "inline-base64",
            "data_base64": payload,
        },
    }


def _receive_until(ws, predicate, *, max_messages: int = 80):
    events = []
    for _ in range(max_messages):
        event = _receive_json_with_timeout(ws)
        events.append(event)
        if predicate(event):
            return events
    raise AssertionError("Condition not reached within received message budget")


def _receive_json_with_timeout(ws, *, timeout_seconds: float = 2.0):
    holder: dict[str, object] = {}

    def run_receive() -> None:
        try:
            holder["value"] = ws.receive_json()
        except Exception as exc:  # pragma: no cover - exercised in integration runtime
            holder["error"] = exc

    thread = threading.Thread(target=run_receive, daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise AssertionError("Timed out waiting for websocket message")
    if "error" in holder:
        raise holder["error"]  # type: ignore[misc]
    return holder["value"]


def test_live_websocket_consecutive_turns() -> None:
    app, router_engine, llm_engine = _build_test_app()

    with TestClient(app) as client:
        with client.websocket_connect("/v1/realtime/conversation") as ws:
            ws.send_json(
                {
                    "type": "session.start",
                    "config": {
                        "turn_detection": {
                            "mode": "hybrid",
                            "transcript_timeout_ms": 0,
                            "min_silence_duration_ms": 0,
                        },
                        "turn_queue": {"policy": "send_now"},
                    },
                }
            )

            start_events = _receive_until(
                ws, lambda e: e["type"] == "session.ready", max_messages=10
            )
            session_id = start_events[-1]["session_id"]

            for i in range(3):
                ws.send_json(_audio_append(session_id, i))

            first_turn = _receive_until(ws, lambda e: e["type"] == "tts.completed", max_messages=80)
            assert any(item["type"] == "stt.final" for item in first_turn)
            assert any(item["type"] == "route.selected" for item in first_turn)
            assert any(item["type"] == "llm.completed" for item in first_turn)

            for i in range(3, 6):
                ws.send_json(_audio_append(session_id, i))

    assert len(router_engine.requests) >= 1
    assert len(llm_engine.requests) >= 1


def test_live_websocket_barge_in_interrupt() -> None:
    app, _router_engine, llm_engine = _build_test_app(llm_delay_seconds=0.3)

    with TestClient(app) as client:
        with client.websocket_connect("/v1/realtime/conversation") as ws:
            ws.send_json(
                {
                    "type": "session.start",
                    "config": {
                        "turn_detection": {
                            "mode": "hybrid",
                            "transcript_timeout_ms": 0,
                            "min_silence_duration_ms": 0,
                        },
                        "turn_queue": {"policy": "send_now"},
                    },
                }
            )

            start_events = _receive_until(
                ws, lambda e: e["type"] == "session.ready", max_messages=10
            )
            session_id = start_events[-1]["session_id"]

            for i in range(3):
                ws.send_json(_audio_append(session_id, i))

            _receive_until(
                ws,
                lambda e: e["type"] == "session.status" and e.get("status") == "thinking",
                max_messages=60,
            )

            # Make sure the first generation actually started before barge-in.
            _receive_until(
                ws,
                lambda e: e["type"] == "llm.phase" and e.get("phase") == "thinking",
                max_messages=60,
            )

            for i in range(3, 6):
                ws.send_json(_audio_append(session_id, i))

            # Follow-up chunk helps trigger turn commit after barge-in.
            ws.send_json(_audio_append(session_id, 6))

            followup = _receive_until(ws, lambda e: e["type"] == "tts.completed", max_messages=120)
            assert any(item["type"] == "conversation.interrupted" for item in followup)
            assert any(item["type"] == "stt.final" for item in followup)

    assert len(llm_engine.requests) >= 2
