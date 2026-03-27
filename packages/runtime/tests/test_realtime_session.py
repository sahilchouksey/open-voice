from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any
from typing import cast

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
    TokenUsage,
)
from open_voice_runtime.llm.engine import BaseLlmEngine
from open_voice_runtime.llm.registry import LlmEngineRegistry
from open_voice_runtime.llm.service import LlmService
from open_voice_runtime.router.contracts import RouteDecision, RouteRequest, RouterCapabilities
from open_voice_runtime.router.engine import BaseRouterEngine
from open_voice_runtime.router.policy import select_route_target
from open_voice_runtime.router.registry import RouterEngineRegistry
from open_voice_runtime.router.service import RouterService
from open_voice_runtime.session.manager import InMemorySessionManager
from open_voice_runtime.session.models import EngineSelection
from open_voice_runtime.stt.contracts import (
    SttCapabilities,
    SttConfig,
    SttEvent,
    SttEventKind,
    SttFileRequest,
    SttFileResult,
)
from open_voice_runtime.stt.engine import BaseSttEngine, BaseSttStream
from open_voice_runtime.stt.registry import SttEngineRegistry
from open_voice_runtime.stt.service import SttService
from open_voice_runtime.transport.http.parser import parse_session_create_request
from open_voice_runtime.transport.websocket.protocol import (
    AudioAppendMessage,
    AudioChunkPayload,
    AudioCommitMessage,
    AudioTransport,
    ConfigUpdateMessage,
    SessionStartMessage,
    UserTurnCommitMessage,
)
from open_voice_runtime.transport.websocket.session import (
    RealtimeConversationSession,
    _llm_first_delta_timeout_seconds,
)
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
from open_voice_runtime.conversation.events import RouteSelectedEvent
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


class FakeSttStream(BaseSttStream):
    def __init__(self, batches: list[list[SttEvent]]) -> None:
        self._batches = list(batches)

    async def push_audio(self, chunk: AudioChunk) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def drain(self, wait_seconds: float = 0.0) -> list[SttEvent]:
        if not self._batches:
            return []
        return self._batches.pop(0)

    async def events(self):
        if False:
            yield SttEvent(kind=SttEventKind.PARTIAL, text="", sequence=0)


class FakeSttEngine(BaseSttEngine):
    id = "fake-stt"
    label = "Fake STT"
    capabilities = SttCapabilities()

    def __init__(self, batches: list[list[SttEvent]]) -> None:
        self._batches = batches

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_stream(self, config: SttConfig) -> BaseSttStream:
        return FakeSttStream(self._batches)

    async def transcribe_file(self, request: SttFileRequest) -> SttFileResult:
        return SttFileResult(text="")


class MultiStreamFakeSttEngine(BaseSttEngine):
    id = "fake-stt"
    label = "Multi Stream Fake STT"
    capabilities = SttCapabilities()

    def __init__(self, streams: list[list[list[SttEvent]]]) -> None:
        self._streams = [[list(batch) for batch in stream] for stream in streams]
        self._stream_index = 0

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_stream(self, config: SttConfig) -> BaseSttStream:
        if self._stream_index < len(self._streams):
            batches = self._streams[self._stream_index]
        else:
            batches = []
        self._stream_index += 1
        return FakeSttStream(batches)

    async def transcribe_file(self, request: SttFileRequest) -> SttFileResult:
        return SttFileResult(text="")


class FlushFinalFakeSttStream(BaseSttStream):
    def __init__(
        self, partial_batches: list[list[SttEvent]], flush_batches: list[list[SttEvent]]
    ) -> None:
        self._partial_batches = list(partial_batches)
        self._flush_batches = list(flush_batches)
        self._flushed = False

    async def push_audio(self, chunk: AudioChunk) -> None:
        return None

    async def flush(self) -> None:
        self._flushed = True

    async def close(self) -> None:
        return None

    async def drain(self, wait_seconds: float = 0.0) -> list[SttEvent]:
        if self._partial_batches:
            return self._partial_batches.pop(0)
        if self._flushed and self._flush_batches:
            return self._flush_batches.pop(0)
        return []

    async def events(self):
        if False:
            yield SttEvent(kind=SttEventKind.PARTIAL, text="", sequence=0)


class FlushFinalFakeSttEngine(BaseSttEngine):
    id = "fake-stt"
    label = "Flush Final Fake STT"
    capabilities = SttCapabilities()

    def __init__(
        self,
        partial_batches: list[list[SttEvent]],
        flush_batches: list[list[SttEvent]],
    ) -> None:
        self._partial_batches = partial_batches
        self._flush_batches = flush_batches

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_stream(self, config: SttConfig) -> BaseSttStream:
        return FlushFinalFakeSttStream(self._partial_batches, self._flush_batches)

    async def transcribe_file(self, request: SttFileRequest) -> SttFileResult:
        return SttFileResult(text="")


class DelayedFinalFakeSttStream(BaseSttStream):
    def __init__(self) -> None:
        self._drain_calls = 0

    async def push_audio(self, chunk: AudioChunk) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def drain(self, wait_seconds: float = 0.0) -> list[SttEvent]:
        self._drain_calls += 1
        if self._drain_calls == 1:
            return [SttEvent(kind=SttEventKind.FINAL, text="hello there", sequence=1)]
        if self._drain_calls == 2:
            return [SttEvent(kind=SttEventKind.FINAL, text="hello there world", sequence=1)]
        return []

    async def events(self):
        if False:
            yield SttEvent(kind=SttEventKind.PARTIAL, text="", sequence=0)


class DelayedFinalFakeSttEngine(BaseSttEngine):
    id = "fake-stt"
    label = "Delayed Final Fake STT"
    capabilities = SttCapabilities()

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_stream(self, config: SttConfig) -> BaseSttStream:
        return DelayedFinalFakeSttStream()

    async def transcribe_file(self, request: SttFileRequest) -> SttFileResult:
        return SttFileResult(text="")


class ChainedRevisionFakeSttStream(BaseSttStream):
    def __init__(self) -> None:
        self._drain_calls = 0

    async def push_audio(self, chunk: AudioChunk) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def drain(self, wait_seconds: float = 0.0) -> list[SttEvent]:
        self._drain_calls += 1
        if self._drain_calls == 1:
            return [SttEvent(kind=SttEventKind.FINAL, text="something about socrates", sequence=1)]
        if self._drain_calls == 2:
            return [
                SttEvent(
                    kind=SttEventKind.FINAL,
                    text="something about socrates not god",
                    sequence=1,
                )
            ]
        if self._drain_calls == 3:
            return [
                SttEvent(
                    kind=SttEventKind.FINAL,
                    text="something about socrates not god i do not",
                    sequence=1,
                )
            ]
        return []

    async def events(self):
        if False:
            yield SttEvent(kind=SttEventKind.PARTIAL, text="", sequence=0)


class ChainedRevisionFakeSttEngine(BaseSttEngine):
    id = "fake-stt"
    label = "Chained Revision Fake STT"
    capabilities = SttCapabilities()

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_stream(self, config: SttConfig) -> BaseSttStream:
        return ChainedRevisionFakeSttStream()

    async def transcribe_file(self, request: SttFileRequest) -> SttFileResult:
        return SttFileResult(text="")


class FakeRouterEngine(BaseRouterEngine):
    id = "fake-router"
    label = "Fake Router"
    capabilities = RouterCapabilities()

    def __init__(self, route_name: str) -> None:
        self._route_name = route_name
        self.requests: list[RouteRequest] = []

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def route(self, request: RouteRequest) -> RouteDecision:
        self.requests.append(request)
        target = select_route_target(self._route_name, request.available_targets)
        return RouteDecision(
            router_id=self.id,
            route_name=self._route_name,
            llm_engine_id=target.llm_engine_id if target else None,
            provider=target.provider if target else None,
            model=target.model if target else None,
            profile_id=target.profile_id if target else None,
            confidence=0.99,
        )


class SlowFakeRouterEngine(FakeRouterEngine):
    def __init__(self, route_name: str, *, delay_seconds: float = 0.4) -> None:
        super().__init__(route_name)
        self._delay_seconds = delay_seconds

    async def route(self, request: RouteRequest) -> RouteDecision:
        await asyncio.sleep(self._delay_seconds)
        return await super().route(request)


class FakeLlmEngine(BaseLlmEngine):
    id = "opencode"
    label = "Fake LLM"
    capabilities = LlmCapabilities(streaming=True, tool_calls=True, provider_managed_sessions=True)

    def __init__(self, events: list[LlmEvent]) -> None:
        self._events = list(events)
        self.requests: list[LlmRequest] = []

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
            for event in self._events:
                yield event

        return generator()


class FakeTtsEngine(BaseTtsEngine):
    id = "fake-tts"
    label = "Fake TTS"
    capabilities = TtsCapabilities(streaming=True)

    def __init__(
        self,
        *,
        chunk_delay_seconds: float = 0.0,
        complete_delay_seconds: float = 0.0,
    ) -> None:
        self.requests: list[TtsRequest] = []
        self._chunk_delay_seconds = chunk_delay_seconds
        self._complete_delay_seconds = complete_delay_seconds

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def synthesize(self, request: TtsRequest) -> TtsResult:
        self.requests.append(request)
        return TtsResult(audio=b"\x00\x00", audio_format=request.audio_format, duration_ms=40.0)

    async def stream(self, request: TtsRequest) -> AsyncIterator[TtsEvent]:
        self.requests.append(request)

        async def generator() -> AsyncIterator[TtsEvent]:
            if self._chunk_delay_seconds > 0:
                await asyncio.sleep(self._chunk_delay_seconds)
            yield TtsEvent(
                kind=TtsEventKind.AUDIO_CHUNK,
                audio_chunk=AudioChunk(
                    data=b"\x00\x00",
                    format=request.audio_format,
                    sequence=0,
                    duration_ms=40.0,
                ),
                text_segment=request.text,
            )
            if self._complete_delay_seconds > 0:
                await asyncio.sleep(self._complete_delay_seconds)
            yield TtsEvent(kind=TtsEventKind.COMPLETED, duration_ms=40.0)

        return generator()


class FakeVadStream(BaseVadStream):
    def __init__(self, batches: list[list[VadEvent]]) -> None:
        self._batches = list(batches)

    async def push_audio(self, chunk: AudioChunk) -> VadResult:
        if not self._batches:
            return VadResult()
        return VadResult(events=self._batches.pop(0))

    async def flush(self) -> VadResult:
        return VadResult()

    async def close(self) -> None:
        return None


class FakeVadEngine(BaseVadEngine):
    id = "silero"
    label = "Fake VAD"
    capabilities = VadCapabilities(streaming=True, sample_rates_hz=(16000,))

    def __init__(self, batches: list[list[VadEvent]]) -> None:
        self._batches = batches

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_stream(self, config: VadConfig) -> BaseVadStream:
        return FakeVadStream(self._batches)


class MinDurationAwareFakeVadStream(BaseVadStream):
    def __init__(self, batches: list[list[VadEvent]], *, min_speech_duration_ms: int) -> None:
        self._batches = list(batches)
        self._min_speech_duration_ms = min_speech_duration_ms

    async def push_audio(self, chunk: AudioChunk) -> VadResult:
        if not self._batches:
            return VadResult()
        events = self._batches.pop(0)
        normalized: list[VadEvent] = []
        for event in events:
            copied = VadEvent(
                kind=event.kind,
                sequence=event.sequence,
                timestamp_ms=event.timestamp_ms,
                probability=event.probability,
                speaking=event.speaking,
                speech_duration_ms=event.speech_duration_ms,
                silence_duration_ms=event.silence_duration_ms,
                chunk=event.chunk,
            )
            if copied.kind is VadEventKind.START_OF_SPEECH:
                copied.speech_duration_ms = float(self._min_speech_duration_ms)
            normalized.append(copied)
        return VadResult(events=normalized)

    async def flush(self) -> VadResult:
        return VadResult()

    async def close(self) -> None:
        return None


class MinDurationAwareFakeVadEngine(BaseVadEngine):
    id = "silero"
    label = "Min Duration Aware Fake VAD"
    capabilities = VadCapabilities(streaming=True, sample_rates_hz=(16000,))

    def __init__(self, batches: list[list[VadEvent]]) -> None:
        self._batches = batches

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_stream(self, config: VadConfig) -> BaseVadStream:
        return MinDurationAwareFakeVadStream(
            self._batches,
            min_speech_duration_ms=config.min_speech_duration_ms,
        )


class DelayedFakeLlmEngine(BaseLlmEngine):
    id = "opencode"
    label = "Delayed Fake LLM"
    capabilities = LlmCapabilities(streaming=True, tool_calls=True, provider_managed_sessions=True)

    def __init__(self, events: list[LlmEvent], *, delay_seconds: float = 0.01) -> None:
        self._events = list(events)
        self._delay_seconds = delay_seconds
        self.requests: list[LlmRequest] = []

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
            for event in self._events:
                await asyncio.sleep(self._delay_seconds)
                yield event

        return generator()


class EchoDelayedFakeLlmEngine(BaseLlmEngine):
    id = "opencode"
    label = "Echo Delayed Fake LLM"
    capabilities = LlmCapabilities(streaming=True, tool_calls=True, provider_managed_sessions=True)

    def __init__(self, *, delay_seconds: float = 0.04) -> None:
        self._delay_seconds = delay_seconds
        self.requests: list[LlmRequest] = []

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(text="")

    def stream(self, request: LlmRequest) -> AsyncIterator[LlmEvent]:
        self.requests.append(request)
        text = request.messages[-1].content if request.messages else ""

        async def generator() -> AsyncIterator[LlmEvent]:
            yield LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING)
            await asyncio.sleep(self._delay_seconds)
            yield LlmEvent(
                kind=LlmEventKind.RESPONSE_DELTA,
                text=f"reply:{text}",
                lane=LlmOutputLane.SPEECH,
                part_id=f"part-{text or 'empty'}",
            )
            await asyncio.sleep(self._delay_seconds)
            yield LlmEvent(
                kind=LlmEventKind.COMPLETED,
                text=f"reply:{text}",
                finish_reason="stop",
            )

        return generator()


class ToolRunningThenDelayFakeLlmEngine(BaseLlmEngine):
    id = "opencode"
    label = "Tool Running Then Delay Fake LLM"
    capabilities = LlmCapabilities(streaming=True, tool_calls=True, provider_managed_sessions=True)

    def __init__(self, *, hold_seconds: float = 0.8) -> None:
        self._hold_seconds = hold_seconds
        self.requests: list[LlmRequest] = []

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(text="")

    def stream(self, request: LlmRequest) -> AsyncIterator[LlmEvent]:
        request_index = len(self.requests)
        self.requests.append(request)
        text = request.messages[-1].content if request.messages else ""

        async def generator() -> AsyncIterator[LlmEvent]:
            if request_index == 0:
                yield LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING)
                yield LlmEvent(
                    kind=LlmEventKind.REASONING_DELTA,
                    text="Searching now.",
                    part_id="reason-1",
                )
                yield LlmEvent(
                    kind=LlmEventKind.TOOL_UPDATE,
                    call_id="call_1",
                    tool_name="websearch",
                    tool_input={},
                    metadata={"status": "pending", "is_mcp": True},
                )
                yield LlmEvent(
                    kind=LlmEventKind.TOOL_UPDATE,
                    call_id="call_1",
                    tool_name="websearch",
                    tool_input={"query": text},
                    metadata={"status": "running", "is_mcp": True},
                )
                await asyncio.sleep(self._hold_seconds)
                yield LlmEvent(
                    kind=LlmEventKind.RESPONSE_DELTA,
                    text=f"first:{text}",
                    lane=LlmOutputLane.SPEECH,
                    part_id="part-first",
                )
                yield LlmEvent(
                    kind=LlmEventKind.COMPLETED,
                    text=f"first:{text}",
                    finish_reason="stop",
                )
                return

            yield LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING)
            await asyncio.sleep(0.01)
            yield LlmEvent(
                kind=LlmEventKind.RESPONSE_DELTA,
                text=f"reply:{text}",
                lane=LlmOutputLane.SPEECH,
                part_id="part-second",
            )
            yield LlmEvent(
                kind=LlmEventKind.COMPLETED,
                text=f"reply:{text}",
                finish_reason="stop",
            )

        return generator()


class SlowStartFakeLlmEngine(BaseLlmEngine):
    id = "opencode"
    label = "Slow Start Fake LLM"
    capabilities = LlmCapabilities(streaming=True, tool_calls=True, provider_managed_sessions=True)

    def __init__(self, *, response_delay_seconds: float = 0.45) -> None:
        self._response_delay_seconds = response_delay_seconds
        self.requests: list[LlmRequest] = []

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
            await asyncio.sleep(self._response_delay_seconds)
            yield LlmEvent(
                kind=LlmEventKind.RESPONSE_DELTA,
                text="Thanks for waiting.",
                lane=LlmOutputLane.SPEECH,
                part_id="slow-part",
            )
            yield LlmEvent(
                kind=LlmEventKind.COMPLETED,
                text="Thanks for waiting.",
                finish_reason="stop",
            )

        return generator()


class NeverRespondingFakeLlmEngine(BaseLlmEngine):
    id = "opencode"
    label = "Never Responding Fake LLM"
    capabilities = LlmCapabilities(streaming=True, tool_calls=True, provider_managed_sessions=True)

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

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
            await asyncio.sleep(60)
            if False:
                yield LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING)

        return generator()


class StaleLateDeltaFakeLlmEngine(BaseLlmEngine):
    id = "opencode"
    label = "Stale Late Delta Fake LLM"
    capabilities = LlmCapabilities(streaming=True, tool_calls=True, provider_managed_sessions=True)

    def __init__(self) -> None:
        self.requests: list[LlmRequest] = []

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(text="")

    def stream(self, request: LlmRequest) -> AsyncIterator[LlmEvent]:
        request_index = len(self.requests)
        self.requests.append(request)
        text = request.messages[-1].content if request.messages else ""

        async def generator() -> AsyncIterator[LlmEvent]:
            yield LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING)
            if request_index == 0:
                await asyncio.sleep(0.18)
                yield LlmEvent(
                    kind=LlmEventKind.RESPONSE_DELTA,
                    text="stale-old",  # should be filtered after send_now preemption
                    lane=LlmOutputLane.SPEECH,
                    part_id="stale-old",
                )
                yield LlmEvent(
                    kind=LlmEventKind.COMPLETED,
                    text="stale-old",
                    finish_reason="stop",
                )
                return

            await asyncio.sleep(0.01)
            yield LlmEvent(
                kind=LlmEventKind.RESPONSE_DELTA,
                text=f"fresh:{text}",
                lane=LlmOutputLane.SPEECH,
                part_id="fresh",
            )
            yield LlmEvent(
                kind=LlmEventKind.COMPLETED,
                text=f"fresh:{text}",
                finish_reason="stop",
            )

        return generator()


def test_commit_routes_on_full_utterance() -> None:
    asyncio.run(_test_commit_routes_on_full_utterance())


async def _test_commit_routes_on_full_utterance() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [SttEvent(kind=SttEventKind.FINAL, text="Ever tried?", sequence=1)],
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="Fail better.", sequence=2)],
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_engine = FakeRouterEngine("trivial_route")
    router_registry.register(router_engine, default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
    )

    events = await session.apply_message(
        SessionStartMessage(config={"turn_detection": {"stabilization_ms": 50}})
    )
    session_id = events[0].session_id

    await session.apply_message(_audio_append_message(session_id, sequence=0))
    await session.apply_message(_audio_append_message(session_id, sequence=1))
    commit_events = await session.apply_message(AudioCommitMessage(session_id=session_id))
    state = await session_manager.get(session_id)

    assert router_engine.requests[0].user_text == "Ever tried? Fail better."
    assert state.turns[0].user_text == "Ever tried? Fail better."
    event_types_list = event_types(commit_events)
    assert event_types_list == [
        "session.status",
        "stt.final",
        "route.selected",
        "session.status",
        "session.status",
    ]
    transcribing_status = next(event for event in commit_events if event.type == "session.status")
    assert transcribing_status.status.value == "transcribing"


def test_audio_commit_with_client_turn_id_emits_turn_accepted() -> None:
    asyncio.run(_test_audio_commit_with_client_turn_id_emits_turn_accepted())


async def _test_audio_commit_with_client_turn_id_emits_turn_accepted() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="ack this commit", sequence=1)],
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("trivial_route"), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
    )

    start_events = await session.apply_message(SessionStartMessage())
    session_id = start_events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))

    commit_events = await session.apply_message(
        AudioCommitMessage(session_id=session_id, client_turn_id="ct-123")
    )

    assert commit_events[0].type == "turn.accepted"
    assert commit_events[0].client_turn_id == "ct-123"
    assert commit_events[0].turn_id is not None
    stt_final = next(event for event in commit_events if event.type == "stt.final")
    assert stt_final.turn_id == commit_events[0].turn_id


def test_audio_commit_emits_stt_status_progress_events() -> None:
    asyncio.run(_test_audio_commit_emits_stt_status_progress_events())


async def _test_audio_commit_emits_stt_status_progress_events() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        DelayedFinalFakeSttEngine(),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("trivial_route"), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(config={"turn_detection": {"stabilization_ms": 20}})
    )
    session_id = start_events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))

    commit_events = await session.apply_message(
        AudioCommitMessage(session_id=session_id, client_turn_id="ct-progress-1")
    )
    statuses = [event.status for event in commit_events if event.type == "stt.status"]

    assert "queued" in statuses
    assert "transcribing" in statuses
    assert "waiting_final" in statuses
    assert "stabilizing" in statuses


def test_stt_final_includes_revision_and_finality() -> None:
    asyncio.run(_test_stt_final_includes_revision_and_finality())


async def _test_stt_final_includes_revision_and_finality() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(DelayedFinalFakeSttEngine(), default=True)
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("trivial_route"), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(config={"turn_detection": {"stabilization_ms": 20}})
    )
    session_id = start_events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))

    commit_events = await session.apply_message(AudioCommitMessage(session_id=session_id))
    final_event = next(event for event in commit_events if event.type == "stt.final")

    assert final_event.revision is not None
    assert final_event.revision >= 1
    assert final_event.finality in {"stable", "revised"}
    assert isinstance(final_event.deferred, bool)


def test_llm_first_delta_timeout_has_more_reasonable_default() -> None:
    asyncio.run(_test_llm_first_delta_timeout_has_more_reasonable_default())


async def _test_llm_first_delta_timeout_has_more_reasonable_default() -> None:
    session_manager = InMemorySessionManager()
    session = RealtimeConversationSession(session_manager, config=RuntimeConfig())
    start_events = await session.apply_message(SessionStartMessage())
    session_id = start_events[0].session_id
    state = await session_manager.get(session_id)

    timeout_seconds = cast(float, _llm_first_delta_timeout_seconds(state))
    assert timeout_seconds >= 30.0


def test_llm_first_delta_timeout_extends_on_non_delta_progress() -> None:
    asyncio.run(_test_llm_first_delta_timeout_extends_on_non_delta_progress())


async def _test_llm_first_delta_timeout_extends_on_non_delta_progress() -> None:
    session_manager = InMemorySessionManager()

    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="search this", sequence=1)],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_registry.register(ToolRunningThenDelayFakeLlmEngine(hold_seconds=0.35), default=True)

    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "llm": {
                    "first_delta_timeout_ms": 200,
                    "total_timeout_ms": 3000,
                }
            }
        )
    )
    session_id = start_events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    await asyncio.sleep(0.9)

    assert any(item.get("type") == "llm.response.delta" for item in emitted)
    assert not any(
        item.get("type") == "llm.error"
        and item.get("error", {}).get("details", {}).get("timeout_kind")
        == "llm_first_delta_timeout"
        for item in emitted
    )


def test_user_turn_commit_with_client_turn_id_emits_turn_accepted() -> None:
    asyncio.run(_test_user_turn_commit_with_client_turn_id_emits_turn_accepted())


async def _test_user_turn_commit_with_client_turn_id_emits_turn_accepted() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="ack via user turn commit", sequence=1)],
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("trivial_route"), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
    )

    start_events = await session.apply_message(SessionStartMessage())
    session_id = start_events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))

    commit_events = await session.apply_message(
        UserTurnCommitMessage(session_id=session_id, client_turn_id="ct-user-456")
    )

    assert commit_events[0].type == "turn.accepted"
    assert commit_events[0].client_turn_id == "ct-user-456"
    assert commit_events[0].turn_id is not None
    stt_final = next(event for event in commit_events if event.type == "stt.final")
    assert stt_final.turn_id == commit_events[0].turn_id


def test_session_runtime_config_overrides_route_targets() -> None:
    asyncio.run(_test_session_runtime_config_overrides_route_targets())


async def _test_session_runtime_config_overrides_route_targets() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="Need deep analysis.", sequence=1)],
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_engine = FakeRouterEngine("complex_route")
    router_registry.register(router_engine, default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
    )

    events = await session.apply_message(
        SessionStartMessage(
            engine_selection=EngineSelection(llm="custom-llm"),
            config={
                "route_targets": [
                    {
                        "provider": "acme-ai",
                        "model": "deep-thinker",
                        "profile_id": "complex_route",
                    }
                ]
            },
        )
    )
    session_id = events[0].session_id

    await session.apply_message(_audio_append_message(session_id, sequence=0))
    commit_events = await session.apply_message(AudioCommitMessage(session_id=session_id))
    route_event = cast(
        RouteSelectedEvent,
        next(event for event in commit_events if event.type == "route.selected"),
    )

    assert router_engine.requests[0].available_targets[0].llm_engine_id == "custom-llm"
    assert route_event.llm_engine_id == "custom-llm"
    assert route_event.provider == "acme-ai"
    assert route_event.model == "deep-thinker"


def test_commit_streams_llm_events_and_stores_assistant_text() -> None:
    asyncio.run(_test_commit_streams_llm_events_and_stores_assistant_text())


async def _test_commit_streams_llm_events_and_stores_assistant_text() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="Plan the system.", sequence=1)],
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)
    llm_registry = LlmEngineRegistry()
    llm_engine = FakeLlmEngine(
        [
            LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING),
            LlmEvent(
                kind=LlmEventKind.RESPONSE_DELTA,
                text="Let me outline the architecture.",
                lane=LlmOutputLane.SPEECH,
                part_id="part-1",
            ),
            LlmEvent(
                kind=LlmEventKind.USAGE,
                usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
                cost=0.12,
            ),
            LlmEvent(
                kind=LlmEventKind.SUMMARY,
                provider="github-copilot",
                model="claude-sonnet-4.6",
                usage=TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30),
                cost=0.12,
            ),
            LlmEvent(
                kind=LlmEventKind.COMPLETED,
                text="Let me outline the architecture.",
                provider="github-copilot",
                model="claude-sonnet-4.6",
                finish_reason="stop",
            ),
            LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.DONE),
        ]
    )
    llm_registry.register(llm_engine, default=True)
    tts_registry = TtsEngineRegistry()
    tts_engine = FakeTtsEngine()
    tts_registry.register(tts_engine, default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig.from_mapping(
            {
                "llm": {
                    "system_prompt": "You are Open Voice for planning.",
                    "additional_instructions": "Keep spoken chunks short.",
                }
            }
        ),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    events = await session.apply_message(
        SessionStartMessage(config={"turn_detection": {"stabilization_ms": 50}})
    )
    session_id = events[0].session_id

    await session.apply_message(_audio_append_message(session_id, sequence=0))
    commit_events = await session.apply_message(AudioCommitMessage(session_id=session_id))
    state = await session_manager.get(session_id)

    assert event_types(commit_events) == [
        "session.status",
        "stt.final",
        "route.selected",
        "session.status",
        "llm.phase",
        "llm.response.delta",
        "llm.usage",
        "llm.summary",
        "llm.completed",
        "llm.phase",
        "session.status",
        "tts.chunk",
        "tts.completed",
        "session.status",
    ]
    assert state.turns[0].assistant_text == "Let me outline the architecture."
    assert llm_engine.requests[0].messages[0].content == "Plan the system."
    assert llm_engine.requests[0].provider == "opencode"
    assert llm_engine.requests[0].model == "gpt-5.3-codex"
    assert llm_engine.requests[0].system_prompt == "You are Open Voice for planning."
    assert llm_engine.requests[0].metadata["additional_instructions"] == "Keep spoken chunks short."
    assert llm_engine.requests[0].metadata["opencode_mode"] is None
    assert llm_engine.requests[0].metadata["opencode_force_system_override"] is False
    assert tts_engine.requests[0].text == "Let me outline the architecture."
    assert tts_engine.requests[0].audio_format == AudioFormat(
        sample_rate_hz=24000,
        channels=1,
        encoding=AudioEncoding.PCM_S16LE,
    )
    summary_event = next(event for event in commit_events if event.type == "llm.summary")
    assert summary_event.metadata is None


def test_parse_session_create_request_captures_runtime_config() -> None:
    request = parse_session_create_request(
        {
            "metadata": {"source": "test"},
            "runtime_config": {
                "default_llm_engine_id": "custom-llm",
                "route_targets": [
                    {
                        "provider": "acme-ai",
                        "model": "deep-thinker",
                        "profile_id": "complex_route",
                    }
                ],
            },
        }
    )

    assert request.metadata["source"] == "test"
    assert request.metadata["runtime_config"]["default_llm_engine_id"] == "custom-llm"


def test_config_update_deep_merges_nested_llm_settings() -> None:
    asyncio.run(_test_config_update_deep_merges_nested_llm_settings())


async def _test_config_update_deep_merges_nested_llm_settings() -> None:
    session_manager = InMemorySessionManager()
    session = RealtimeConversationSession(session_manager)
    events = await session.apply_message(
        SessionStartMessage(
            config={
                "llm": {
                    "system_prompt": "Base prompt.",
                }
            }
        )
    )
    session_id = events[0].session_id

    await session.apply_message(
        ConfigUpdateMessage(
            session_id=session_id,
            config={
                "llm": {
                    "additional_instructions": "Keep spoken output short.",
                }
            },
        )
    )

    state = await session_manager.get(session_id)
    runtime_config = state.metadata["runtime_config"]
    assert runtime_config["llm"]["system_prompt"] == "Base prompt."
    assert runtime_config["llm"]["additional_instructions"] == "Keep spoken output short."


def test_config_update_merges_opencode_override_controls() -> None:
    asyncio.run(_test_config_update_merges_opencode_override_controls())


async def _test_config_update_merges_opencode_override_controls() -> None:
    session_manager = InMemorySessionManager()
    session = RealtimeConversationSession(session_manager)
    events = await session.apply_message(
        SessionStartMessage(
            config={
                "llm": {
                    "opencode_mode": "build",
                    "opencode_force_system_override": True,
                }
            }
        )
    )
    session_id = events[0].session_id

    await session.apply_message(
        ConfigUpdateMessage(
            session_id=session_id,
            config={
                "llm": {
                    "system_prompt": "Runtime override prompt",
                }
            },
        )
    )

    state = await session_manager.get(session_id)
    runtime_config = state.metadata["runtime_config"]
    assert runtime_config["llm"]["opencode_mode"] == "build"
    assert runtime_config["llm"]["opencode_force_system_override"] is True
    assert runtime_config["llm"]["system_prompt"] == "Runtime override prompt"


def test_apply_streams_live_llm_and_tts_events() -> None:
    asyncio.run(_test_apply_streams_live_llm_and_tts_events())


async def _test_apply_streams_live_llm_and_tts_events() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="Say something.", sequence=1)],
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)
    llm_registry = LlmEngineRegistry()
    llm_registry.register(
        DelayedFakeLlmEngine(
            [
                LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING),
                LlmEvent(
                    kind=LlmEventKind.RESPONSE_DELTA,
                    text="Hello there. ",
                    lane=LlmOutputLane.SPEECH,
                    part_id="part-1",
                ),
                LlmEvent(
                    kind=LlmEventKind.RESPONSE_DELTA,
                    text="General Kenobi.",
                    lane=LlmOutputLane.SPEECH,
                    part_id="part-2",
                ),
                LlmEvent(
                    kind=LlmEventKind.COMPLETED,
                    text="Hello there. General Kenobi.",
                    finish_reason="stop",
                ),
            ]
        ),
        default=True,
    )
    tts_registry = TtsEngineRegistry()
    tts_engine = FakeTtsEngine()
    tts_registry.register(tts_engine, default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig.from_mapping({"llm": {"enable_fast_ack": False}}),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(SessionStartMessage())
    session_id = start_events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    result = await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    assert result == []
    assert emitted[:3] == [
        {"type": "stt.final", **{k: v for k, v in emitted[0].items() if k != "type"}},
        {"type": "route.selected", **{k: v for k, v in emitted[1].items() if k != "type"}},
        {"type": "session.status", **{k: v for k, v in emitted[2].items() if k != "type"}},
    ]

    await asyncio.sleep(0.12)

    event_types = [item["type"] for item in emitted]
    assert event_types[:3] == ["stt.final", "route.selected", "session.status"]
    assert "llm.phase" in event_types
    assert event_types.count("llm.response.delta") == 2
    assert event_types.count("tts.chunk") == 2
    assert "llm.completed" in event_types
    assert "generation_id" in emitted[event_types.index("llm.response.delta")]
    assert "generation_id" in emitted[event_types.index("tts.chunk")]
    assert event_types.index("tts.chunk") < event_types.index("llm.completed")
    assert emitted[event_types.index("tts.chunk")]["chunk"]["data_base64"] == base64.b64encode(
        b"\x00\x00"
    ).decode("ascii")
    assert tts_engine.requests[0].text == "Hello there."
    assert tts_engine.requests[1].text == "General Kenobi."


def test_fast_ack_arrives_before_router_timeout_and_slow_llm_response() -> None:
    asyncio.run(_test_fast_ack_arrives_before_router_timeout_and_slow_llm_response())


async def _test_fast_ack_arrives_before_router_timeout_and_slow_llm_response() -> None:
    session_manager = InMemorySessionManager()

    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="I forgive him.", sequence=1)],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(
        SlowFakeRouterEngine("moderate_route", delay_seconds=0.35),
        default=True,
    )

    llm_registry = LlmEngineRegistry()
    llm_registry.register(SlowStartFakeLlmEngine(response_delay_seconds=0.45), default=True)

    tts_registry = TtsEngineRegistry()
    tts_engine = FakeTtsEngine()
    tts_registry.register(tts_engine, default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(config={"router": {"timeout_ms": 50}})
    )
    session_id = start_events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    await asyncio.sleep(0.9)

    first_stt_final = next(item for item in emitted if item["type"] == "stt.final")
    first_route = next(item for item in emitted if item["type"] == "route.selected")
    first_llm_response = next(item for item in emitted if item["type"] == "llm.response.delta")
    first_tts_chunk = next(item for item in emitted if item["type"] == "tts.chunk")

    ack_index = next(
        index
        for index, item in enumerate(emitted)
        if item["type"] == "tts.chunk" and item.get("text_segment") == "Got it."
    )
    route_index = next(
        index for index, item in enumerate(emitted) if item["type"] == "route.selected"
    )
    llm_response_index = next(
        index for index, item in enumerate(emitted) if item["type"] == "llm.response.delta"
    )

    assert "timed out after 50 ms" in (first_route.get("reason") or "")
    assert first_tts_chunk.get("text_segment") == "Got it."

    stt_ts = datetime.fromisoformat(first_stt_final["timestamp"])
    tts_ack_ts = datetime.fromisoformat(first_tts_chunk["timestamp"])
    ack_delay_ms = (tts_ack_ts - stt_ts).total_seconds() * 1000.0

    assert ack_index < llm_response_index
    assert ack_delay_ms <= 600.0
    assert tts_engine.requests[0].text == "Got it."
    assert first_llm_response.get("delta") == "Thanks for waiting."

    assert any(item["type"] == "llm.completed" for item in emitted)
    assert any(item["type"] == "tts.completed" for item in emitted)


def test_fast_ack_can_be_disabled_in_runtime_config() -> None:
    asyncio.run(_test_fast_ack_can_be_disabled_in_runtime_config())


async def _test_fast_ack_can_be_disabled_in_runtime_config() -> None:
    session_manager = InMemorySessionManager()

    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="search voice agents", sequence=1)],
                [],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_registry.register(ToolRunningThenDelayFakeLlmEngine(hold_seconds=0.25), default=True)

    tts_registry = TtsEngineRegistry()
    tts_engine = FakeTtsEngine()
    tts_registry.register(tts_engine, default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig.from_mapping({"llm": {"enable_fast_ack": False}}),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(SessionStartMessage())
    session_id = start_events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    await asyncio.sleep(0.9)

    ack_chunks = [
        item
        for item in emitted
        if item.get("type") == "tts.chunk" and item.get("text_segment") == "Got it."
    ]
    assert not ack_chunks, "Fast ack should be disabled by llm.enable_fast_ack=false"

    assert any(item.get("type") == "llm.tool.update" for item in emitted)
    assert any(item.get("type") == "tts.chunk" for item in emitted)
    assert all(request.text != "Got it." for request in tts_engine.requests)


def test_audio_append_can_auto_commit_with_hybrid_turn_detection() -> None:
    asyncio.run(_test_audio_append_can_auto_commit_with_hybrid_turn_detection())


async def _test_audio_append_can_auto_commit_with_hybrid_turn_detection() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [SttEvent(kind=SttEventKind.FINAL, text="Auto commit me.", sequence=0)],
                [],
            ]
        ),
        default=True,
    )
    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=0, timestamp_ms=0.0)],
                [VadEvent(kind=VadEventKind.END_OF_SPEECH, sequence=1, timestamp_ms=1200.0)],
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)
    llm_registry = LlmEngineRegistry()
    llm_registry.register(
        FakeLlmEngine(
            [
                LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING),
                LlmEvent(
                    kind=LlmEventKind.RESPONSE_DELTA,
                    text="Done automatically.",
                    lane=LlmOutputLane.SPEECH,
                    part_id="part-1",
                ),
                LlmEvent(
                    kind=LlmEventKind.COMPLETED,
                    text="Done automatically.",
                    finish_reason="stop",
                ),
            ]
        ),
        default=True,
    )
    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_detection": {
                    "mode": "hybrid",
                    "transcript_timeout_ms": 0,
                    "min_silence_duration_ms": 0,
                }
            }
        )
    )
    session_id = events[0].session_id

    first_events = await session.apply_message(_audio_append_message(session_id, sequence=0))
    second_events = await session.apply_message(_audio_append_message(session_id, sequence=1))
    state = await session_manager.get(session_id)

    assert event_types(first_events) == ["vad.state", "stt.final"]
    assert event_types(second_events) == [
        "vad.state",
        "route.selected",
        "session.status",
        "llm.phase",
        "llm.response.delta",
        "llm.completed",
        "session.status",
        "tts.chunk",
        "tts.completed",
        "session.status",
    ]
    assert state.turns[0].user_text == "Auto commit me."


def test_audio_append_auto_commits_after_vad_end_with_only_interim_text() -> None:
    asyncio.run(_test_audio_append_auto_commits_after_vad_end_with_only_interim_text())


async def _test_audio_append_auto_commits_after_vad_end_with_only_interim_text() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FlushFinalFakeSttEngine(
            [
                [SttEvent(kind=SttEventKind.PARTIAL, text="What is the capital", sequence=0)],
                [],
                [],
            ],
            [
                [
                    SttEvent(
                        kind=SttEventKind.FINAL, text="What is the capital of France?", sequence=0
                    )
                ]
            ],
        ),
        default=True,
    )
    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=0, timestamp_ms=0.0)],
                [],
                [VadEvent(kind=VadEventKind.END_OF_SPEECH, sequence=1, timestamp_ms=1200.0)],
            ]
        ),
        default=True,
    )
    router_engine = FakeRouterEngine("simple_route")
    router_registry = RouterEngineRegistry()
    router_registry.register(router_engine, default=True)
    llm_registry = LlmEngineRegistry()
    llm_registry.register(
        FakeLlmEngine(
            [
                LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING),
                LlmEvent(
                    kind=LlmEventKind.RESPONSE_DELTA,
                    text="Paris",
                    lane=LlmOutputLane.SPEECH,
                    part_id="part-1",
                ),
                LlmEvent(
                    kind=LlmEventKind.COMPLETED,
                    text="Paris",
                    finish_reason="stop",
                ),
            ]
        ),
        default=True,
    )
    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_detection": {
                    "mode": "hybrid",
                    "transcript_timeout_ms": 10_000,
                    "min_silence_duration_ms": 0,
                }
            }
        )
    )
    session_id = events[0].session_id

    first_events = await session.apply_message(_audio_append_message(session_id, sequence=0))
    second_events = await session.apply_message(_audio_append_message(session_id, sequence=1))
    third_events = await session.apply_message(_audio_append_message(session_id, sequence=2))
    state = await session_manager.get(session_id)

    assert event_types(first_events) == ["vad.state", "stt.partial"]
    assert second_events == []
    assert event_types(third_events) == [
        "vad.state",
        "stt.final",
        "route.selected",
        "session.status",
        "llm.phase",
        "llm.response.delta",
        "llm.completed",
        "session.status",
        "tts.chunk",
        "tts.completed",
        "session.status",
    ]
    assert state.turns[0].user_text == "What is the capital of France?"
    assert router_engine.requests[0].user_text == "What is the capital of France?"


def test_interrupt_cancels_background_response_task() -> None:
    asyncio.run(_test_interrupt_cancels_background_response_task())


async def _test_interrupt_cancels_background_response_task() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="Interrupt me.", sequence=1)],
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)
    llm_registry = LlmEngineRegistry()
    llm_registry.register(
        DelayedFakeLlmEngine(
            [
                LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING),
                LlmEvent(
                    kind=LlmEventKind.RESPONSE_DELTA,
                    text="This should not finish.",
                    lane=LlmOutputLane.SPEECH,
                    part_id="part-1",
                ),
                LlmEvent(
                    kind=LlmEventKind.COMPLETED,
                    text="This should not finish.",
                    finish_reason="stop",
                ),
            ],
            delay_seconds=0.05,
        ),
        default=True,
    )
    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    events = await session.apply_message(SessionStartMessage())
    session_id = events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    await session.apply(
        {
            "type": "conversation.interrupt",
            "session_id": session_id,
            "reason": "client",
        },
        emit=emit,
    )
    await asyncio.sleep(0.12)

    assert any(item["type"] == "conversation.interrupted" for item in emitted)
    interrupted_index = next(
        index for index, item in enumerate(emitted) if item["type"] == "conversation.interrupted"
    )
    assert all(item["type"] != "tts.chunk" for item in emitted[interrupted_index + 1 :])


def test_queue_enqueue_policy_processes_follow_up_turn_after_current_finishes() -> None:
    asyncio.run(_test_queue_enqueue_policy_processes_follow_up_turn_after_current_finishes())


async def _test_queue_enqueue_policy_processes_follow_up_turn_after_current_finishes() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        MultiStreamFakeSttEngine(
            [
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="first", sequence=1)],
                ],
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="second", sequence=2)],
                ],
            ]
        ),
        default=True,
    )
    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=0,
                        timestamp_ms=0.0,
                    )
                ]
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)
    llm_registry = LlmEngineRegistry()
    llm_engine = EchoDelayedFakeLlmEngine(delay_seconds=0.06)
    llm_registry.register(llm_engine, default=True)
    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(complete_delay_seconds=0.25), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_queue": {"policy": "enqueue"},
            }
        )
    )
    session_id = start_events[0].session_id

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply_message(_audio_append_message(session_id, sequence=0))
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    async def _wait_for_status(status: str, timeout_seconds: float = 1.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if any(
                item.get("type") == "session.status" and item.get("status") == status
                for item in emitted
            ):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"Timed out waiting for session status '{status}'")

    # Queue policy applies while SPEAKING (THINKING always interrupts by design).
    await _wait_for_status("speaking")

    await session.apply_message(_audio_append_message(session_id, sequence=1))
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    await asyncio.sleep(0.35)

    queued_events = [item for item in emitted if item.get("type") == "turn.queued"]
    assert queued_events
    assert queued_events[-1]["queue_size"] >= 1

    metric_events = [item for item in emitted if item.get("type") == "turn.metrics"]
    assert metric_events
    assert any((item.get("queue_delay_ms") or 0) >= 0 for item in metric_events)

    async def _wait_for_completed_generations(count: int, timeout_seconds: float = 1.5) -> set[str]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            generation_ids_now = {
                item.get("generation_id")
                for item in emitted
                if item.get("type") == "llm.completed" and item.get("generation_id")
            }
            if len(generation_ids_now) >= count:
                return {gen_id for gen_id in generation_ids_now if isinstance(gen_id, str)}
            await asyncio.sleep(0.02)
        return {
            item.get("generation_id")
            for item in emitted
            if item.get("type") == "llm.completed" and item.get("generation_id")
        }

    generation_ids = await _wait_for_completed_generations(2)
    assert len(generation_ids) >= 2


def test_send_now_policy_interrupts_current_generation_for_new_turn() -> None:
    asyncio.run(_test_send_now_policy_interrupts_current_generation_for_new_turn())


async def _test_send_now_policy_interrupts_current_generation_for_new_turn() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        MultiStreamFakeSttEngine(
            [
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="first", sequence=1)],
                ],
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="second", sequence=2)],
                ],
            ]
        ),
        default=True,
    )
    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=0,
                        timestamp_ms=0.0,
                    )
                ]
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)
    llm_registry = LlmEngineRegistry()
    llm_engine = EchoDelayedFakeLlmEngine(delay_seconds=0.12)
    llm_registry.register(llm_engine, default=True)
    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_queue": {"policy": "send_now"},
            }
        )
    )
    session_id = start_events[0].session_id

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(
        _audio_append_payload(session_id, sequence=0),
        emit=emit,
    )
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    # Wait until generation is active before barge-in.
    async def _wait_for_generation_start(timeout_seconds: float = 1.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if any(
                item.get("type") == "session.status" and item.get("status") == "thinking"
                for item in emitted
            ):
                return
            await asyncio.sleep(0.01)
        raise AssertionError("Timed out waiting for thinking status before interruption")

    await _wait_for_generation_start()

    await session.apply(
        _audio_append_payload(session_id, sequence=1),
        emit=emit,
    )
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    await asyncio.sleep(0.6)

    assert any(
        item.get("type") == "conversation.interrupted" and item.get("reason") == "send_now"
        for item in emitted
    )
    completed_generations = {
        item.get("generation_id")
        for item in emitted
        if item.get("type") == "llm.completed" and item.get("generation_id")
    }
    assert completed_generations
    interrupted_generation = next(
        item.get("generation_id")
        for item in emitted
        if item.get("type") == "conversation.interrupted" and item.get("reason") == "send_now"
    )
    assert completed_generations
    assert any(
        item.get("type") == "route.selected" and item.get("generation_id") != interrupted_generation
        for item in emitted
    )
    assert not any(item.get("type") == "turn.queued" for item in emitted)


def test_send_now_policy_interrupts_immediately_while_speaking() -> None:
    asyncio.run(_test_send_now_policy_interrupts_immediately_while_speaking())


def test_send_now_ignores_short_follow_up_during_cooldown() -> None:
    asyncio.run(_test_send_now_ignores_short_follow_up_during_cooldown())


async def _test_send_now_ignores_short_follow_up_during_cooldown() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        MultiStreamFakeSttEngine(
            [
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="first question", sequence=1)],
                ],
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="second question", sequence=2)],
                ],
                [
                    [SttEvent(kind=SttEventKind.FINAL, text="yeah", sequence=3)],
                    [],
                ],
            ]
        ),
        default=True,
    )

    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=0, timestamp_ms=0.0)],
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=1, timestamp_ms=40.0)],
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=2, timestamp_ms=80.0)],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_registry.register(EchoDelayedFakeLlmEngine(delay_seconds=0.04), default=True)

    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_queue": {"policy": "send_now"},
                "turn_detection": {"mode": "manual"},
                "interruption": {"cooldown_ms": 1000},
            }
        )
    )
    session_id = start_events[0].session_id

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(_audio_append_payload(session_id, sequence=0), emit=emit)
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    async def _wait_for_status(status: str, timeout_seconds: float = 1.2) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if any(
                item.get("type") == "session.status" and item.get("status") == status
                for item in emitted
            ):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"Timed out waiting for session status '{status}'")

    await _wait_for_status("thinking")

    await session.apply(_audio_append_payload(session_id, sequence=1), emit=emit)
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    await _wait_for_status("thinking")

    await session.apply(_audio_append_payload(session_id, sequence=2), emit=emit)
    await asyncio.sleep(0.45)

    interrupt_events = [
        item
        for item in emitted
        if item.get("type") == "conversation.interrupted" and item.get("reason") == "send_now"
    ]
    assert len(interrupt_events) == 1

    interrupted_generation = interrupt_events[0].get("generation_id")
    assert isinstance(interrupted_generation, str)

    assert any(
        item.get("type") == "llm.completed"
        and item.get("generation_id")
        and item.get("generation_id") != interrupted_generation
        for item in emitted
    )


async def _test_send_now_policy_interrupts_immediately_while_speaking() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        MultiStreamFakeSttEngine(
            [
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="first", sequence=1)],
                ],
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="second", sequence=2)],
                ],
            ]
        ),
        default=True,
    )
    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=0,
                        timestamp_ms=0.0,
                    )
                ],
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=1,
                        timestamp_ms=30.0,
                    )
                ],
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=2,
                        timestamp_ms=60.0,
                    )
                ],
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)
    llm_registry = LlmEngineRegistry()
    llm_registry.register(EchoDelayedFakeLlmEngine(delay_seconds=0.04), default=True)
    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(complete_delay_seconds=0.3), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_queue": {"policy": "send_now"},
            }
        )
    )
    session_id = start_events[0].session_id

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(
        _audio_append_payload(session_id, sequence=0),
        emit=emit,
    )
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    async def _wait_for_first_tts_chunk(timeout_seconds: float = 1.5) -> str:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            for item in emitted:
                if item.get("type") == "tts.chunk" and item.get("generation_id"):
                    generation_id = item.get("generation_id")
                    if isinstance(generation_id, str):
                        return generation_id
            await asyncio.sleep(0.01)
        raise AssertionError("Timed out waiting for first tts.chunk event")

    first_generation_id = await _wait_for_first_tts_chunk()

    await session.apply(
        _audio_append_payload(session_id, sequence=1),
        emit=emit,
    )

    async def _wait_for_interrupt(timeout_seconds: float = 1.0) -> int:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            for index, item in enumerate(emitted):
                if (
                    item.get("type") == "conversation.interrupted"
                    and item.get("reason") == "send_now"
                ):
                    return index
            await asyncio.sleep(0.01)
        raise AssertionError("Timed out waiting for send_now interruption")

    interrupted_index = await _wait_for_interrupt()

    assert all(
        not (
            item.get("type") in {"tts.chunk", "llm.response.delta", "llm.reasoning.delta"}
            and item.get("generation_id") == first_generation_id
        )
        for item in emitted[interrupted_index + 1 :]
    )

    await session.apply(
        _audio_append_payload(session_id, sequence=2),
        emit=emit,
    )
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    await asyncio.sleep(0.5)

    assert any(
        item.get("type") == "route.selected" and item.get("generation_id") != first_generation_id
        for item in emitted
    )


def test_send_now_with_min_duration_requires_sustained_vad_before_interrupt() -> None:
    asyncio.run(_test_send_now_with_min_duration_requires_sustained_vad_before_interrupt())


async def _test_send_now_with_min_duration_requires_sustained_vad_before_interrupt() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        MultiStreamFakeSttEngine(
            [
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="first", sequence=1)],
                ],
                [
                    [SttEvent(kind=SttEventKind.PARTIAL, text="h", sequence=2)],
                    [SttEvent(kind=SttEventKind.FINAL, text="second", sequence=3)],
                ],
            ]
        ),
        default=True,
    )

    vad_registry = VadEngineRegistry()
    vad_registry.register(
        MinDurationAwareFakeVadEngine(
            [
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=0,
                        timestamp_ms=0.0,
                    )
                ],
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=1,
                        timestamp_ms=20.0,
                    )
                ],
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=2,
                        timestamp_ms=40.0,
                    )
                ],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_registry.register(EchoDelayedFakeLlmEngine(delay_seconds=0.04), default=True)

    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(complete_delay_seconds=0.25), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_queue": {"policy": "send_now"},
                "interruption": {"min_duration": 0.15, "cooldown_ms": 1000},
                "turn_detection": {"min_speech_duration_ms": 220},
            }
        )
    )
    session_id = start_events[0].session_id

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(_audio_append_payload(session_id, sequence=0), emit=emit)
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    async def _wait_for_first_tts_chunk(timeout_seconds: float = 1.5) -> str:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            for item in emitted:
                if item.get("type") == "tts.chunk" and item.get("generation_id"):
                    generation_id = item.get("generation_id")
                    if isinstance(generation_id, str):
                        return generation_id
            await asyncio.sleep(0.01)
        raise AssertionError("Timed out waiting for first tts.chunk event")

    first_generation_id = await _wait_for_first_tts_chunk()

    await session.apply(_audio_append_payload(session_id, sequence=1), emit=emit)
    await asyncio.sleep(0.08)

    assert not any(
        item.get("type") == "conversation.interrupted" and item.get("reason") == "send_now"
        for item in emitted
    )

    await session.apply(_audio_append_payload(session_id, sequence=2), emit=emit)
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    await asyncio.sleep(0.55)

    interrupts = [
        item
        for item in emitted
        if item.get("type") == "conversation.interrupted" and item.get("reason") == "send_now"
    ]
    assert len(interrupts) == 1
    assert interrupts[0].get("generation_id") == first_generation_id


def test_send_now_interrupt_works_during_post_interrupt_collecting() -> None:
    asyncio.run(_test_send_now_interrupt_works_during_post_interrupt_collecting())


async def _test_send_now_interrupt_works_during_post_interrupt_collecting() -> None:
    session_manager = InMemorySessionManager()

    stt_registry = SttEngineRegistry()
    stt_registry.register(
        MultiStreamFakeSttEngine(
            [
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="first question", sequence=1)],
                ],
                [
                    [SttEvent(kind=SttEventKind.FINAL, text="interrupt one", sequence=2)],
                    [],
                ],
                [
                    [SttEvent(kind=SttEventKind.FINAL, text="interrupt two", sequence=3)],
                    [],
                ],
            ]
        ),
        default=True,
    )

    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=0, timestamp_ms=0.0)],
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=1, timestamp_ms=40.0)],
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=2, timestamp_ms=80.0)],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_registry.register(EchoDelayedFakeLlmEngine(delay_seconds=0.12), default=True)

    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(complete_delay_seconds=0.3), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_queue": {"policy": "send_now"},
                "turn_detection": {"mode": "hybrid", "transcript_timeout_ms": 0},
            }
        )
    )
    session_id = start_events[0].session_id

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(_audio_append_payload(session_id, sequence=0), emit=emit)
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    async def _wait_for_status(status: str, timeout_seconds: float = 1.4) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if any(
                item.get("type") == "session.status" and item.get("status") == status
                for item in emitted
            ):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"Timed out waiting for session status '{status}'")

    await _wait_for_status("thinking")

    await session.apply(_audio_append_payload(session_id, sequence=1), emit=emit)
    await asyncio.sleep(0.05)
    await session.apply(_audio_append_payload(session_id, sequence=2), emit=emit)

    await asyncio.sleep(0.9)

    interrupts = [
        item
        for item in emitted
        if item.get("type") == "conversation.interrupted" and item.get("reason") == "send_now"
    ]
    assert len(interrupts) >= 2

    route_events = [item for item in emitted if item.get("type") == "route.selected"]
    assert len(route_events) >= 2


def test_commit_reports_stt_timeout_with_spoken_feedback() -> None:
    asyncio.run(_test_commit_reports_stt_timeout_with_spoken_feedback())


async def _test_commit_reports_stt_timeout_with_spoken_feedback() -> None:
    session_manager = InMemorySessionManager()

    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [],
                [],
            ]
        ),
        default=True,
    )

    tts_registry = TtsEngineRegistry()
    tts_engine = FakeTtsEngine()
    tts_registry.register(tts_engine, default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(config={"stt": {"final_timeout_ms": 80}})
    )
    session_id = start_events[0].session_id

    await session.apply_message(_audio_append_message(session_id, sequence=0))

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    error_event = next(item for item in emitted if item.get("type") == "error")
    assert error_event.get("details", {}).get("timeout_kind") == "stt_final_timeout"
    assert error_event.get("retryable") is True

    assert not any(item.get("type") == "tts.chunk" for item in emitted)
    assert any(
        item.get("type") == "session.status" and item.get("status") == "listening"
        for item in emitted
    )

    assert not tts_engine.requests


def test_commit_timeout_emits_retry_scheduled_when_enabled() -> None:
    asyncio.run(_test_commit_timeout_emits_retry_scheduled_when_enabled())


async def _test_commit_timeout_emits_retry_scheduled_when_enabled() -> None:
    session_manager = InMemorySessionManager()

    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [],
                [],
            ]
        ),
        default=True,
    )

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "stt": {"final_timeout_ms": 50},
                "retry": {"enabled": True, "after_ms": 25},
            }
        )
    )
    session_id = start_events[0].session_id

    await session.apply_message(_audio_append_message(session_id, sequence=0))

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
            "client_turn_id": "ct-retry-1",
        },
        emit=emit,
    )

    retry_event = next(
        item
        for item in emitted
        if item.get("type") == "stt.status" and item.get("status") == "retry_scheduled"
    )
    assert retry_event.get("attempt") == 2
    assert retry_event.get("waited_ms") == 25

    accepted_events = [
        item
        for item in emitted
        if item.get("type") == "turn.accepted" and item.get("client_turn_id") == "ct-retry-1"
    ]
    assert len(accepted_events) == 2


def test_commit_reports_llm_first_delta_timeout_with_spoken_feedback() -> None:
    asyncio.run(_test_commit_reports_llm_first_delta_timeout_with_spoken_feedback())


async def _test_commit_reports_llm_first_delta_timeout_with_spoken_feedback() -> None:
    session_manager = InMemorySessionManager()

    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="tell me a joke", sequence=1)],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_registry.register(NeverRespondingFakeLlmEngine(), default=True)

    tts_registry = TtsEngineRegistry()
    tts_engine = FakeTtsEngine()
    tts_registry.register(tts_engine, default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "llm": {
                    "first_delta_timeout_ms": 300,
                    "total_timeout_ms": 2000,
                }
            }
        )
    )
    session_id = start_events[0].session_id

    await session.apply_message(_audio_append_message(session_id, sequence=0))

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    await asyncio.sleep(0.8)

    error_event = next(item for item in emitted if item.get("type") == "error")
    timeout_details = error_event.get("details", {})
    assert timeout_details.get("session_id") == session_id
    assert timeout_details.get("turn_id")
    assert error_event.get("retryable") is False

    llm_error_event = next(item for item in emitted if item.get("type") == "llm.error")
    assert llm_error_event.get("error", {}).get("code") == "provider_error"
    assert llm_error_event.get("error", {}).get("retryable") is True
    assert (
        llm_error_event.get("error", {}).get("details", {}).get("timeout_kind")
        == "llm_first_delta_timeout"
    )

    assert any(
        item.get("type") == "tts.chunk"
        and item.get("text_segment") == "I hit an error: llm_first_delta_timeout"
        for item in emitted
    )
    assert any(
        item.get("type") == "session.status" and item.get("status") == "listening"
        for item in emitted
    )

    assert any(
        request.text == "I hit an error: llm_first_delta_timeout" for request in tts_engine.requests
    )


def test_send_now_cuts_over_immediately_on_new_stt_final() -> None:
    asyncio.run(_test_send_now_cuts_over_immediately_on_new_stt_final())


async def _test_send_now_cuts_over_immediately_on_new_stt_final() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        MultiStreamFakeSttEngine(
            [
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="first query", sequence=1)],
                ],
                [
                    [SttEvent(kind=SttEventKind.PARTIAL, text="new", sequence=2)],
                    [SttEvent(kind=SttEventKind.FINAL, text="latest final", sequence=3)],
                    [],
                ],
            ]
        ),
        default=True,
    )

    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=0, timestamp_ms=0.0)],
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=1, timestamp_ms=40.0)],
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=2, timestamp_ms=80.0)],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_engine = ToolRunningThenDelayFakeLlmEngine(hold_seconds=0.8)
    llm_registry.register(llm_engine, default=True)

    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig.from_mapping({"llm": {"enable_fast_ack": False}}),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(config={"turn_queue": {"policy": "send_now"}})
    )
    session_id = start_events[0].session_id

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(_audio_append_payload(session_id, sequence=0), emit=emit)
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    async def _wait_for_tool_running(timeout_seconds: float = 1.2) -> str:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            for item in emitted:
                if item.get("type") == "llm.tool.update" and item.get("status") == "running":
                    generation_id = item.get("generation_id")
                    if isinstance(generation_id, str):
                        return generation_id
            await asyncio.sleep(0.01)
        raise AssertionError("Timed out waiting for tool-running event")

    first_generation_id = await _wait_for_tool_running()

    await session.apply(_audio_append_payload(session_id, sequence=1), emit=emit)
    await session.apply(_audio_append_payload(session_id, sequence=2), emit=emit)

    await asyncio.sleep(1.0)

    latest_final_index = next(
        index
        for index, item in enumerate(emitted)
        if item.get("type") == "stt.final" and item.get("text") == "latest final"
    )

    assert any(
        item.get("type") == "conversation.interrupted"
        and item.get("reason") == "send_now"
        and item.get("generation_id") == first_generation_id
        for item in emitted
    )

    assert all(
        not (
            item.get("generation_id") == first_generation_id
            and item.get("type") in {"llm.response.delta", "llm.reasoning.delta", "tts.chunk"}
        )
        for item in emitted[latest_final_index:]
    )

    assert any(
        item.get("type") == "route.selected"
        and item.get("generation_id")
        and item.get("generation_id") != first_generation_id
        for item in emitted
    )

    assert any(
        request.messages and request.messages[0].content == "latest final"
        for request in llm_engine.requests
    )


def test_commit_emits_tool_update_events() -> None:
    asyncio.run(_test_commit_emits_tool_update_events())


def test_send_now_barge_in_preserves_interrupting_chunk_text() -> None:
    asyncio.run(_test_send_now_barge_in_preserves_interrupting_chunk_text())


async def _test_send_now_barge_in_preserves_interrupting_chunk_text() -> None:
    session_manager = InMemorySessionManager()

    stt_registry = SttEngineRegistry()
    stt_registry.register(
        MultiStreamFakeSttEngine(
            [
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="first question", sequence=1)],
                ],
                [
                    [SttEvent(kind=SttEventKind.FINAL, text="I said silly walk.", sequence=1)],
                    [SttEvent(kind=SttEventKind.FINAL, text="War. WLR.", sequence=2)],
                    [],
                ],
            ]
        ),
        default=True,
    )

    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=0,
                        timestamp_ms=0.0,
                    )
                ]
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_engine = EchoDelayedFakeLlmEngine(delay_seconds=0.04)
    llm_registry.register(llm_engine, default=True)

    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(complete_delay_seconds=0.3), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_queue": {"policy": "send_now"},
                "turn_detection": {"mode": "manual"},
            }
        )
    )
    session_id = start_events[0].session_id

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(
        _audio_append_payload(session_id, sequence=0),
        emit=emit,
    )
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    async def _wait_for_status(status: str, timeout_seconds: float = 1.2) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if any(
                item.get("type") == "session.status" and item.get("status") == status
                for item in emitted
            ):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"Timed out waiting for session status '{status}'")

    await _wait_for_status("speaking")

    await session.apply(
        _audio_append_payload(session_id, sequence=1),
        emit=emit,
    )
    await session.apply(
        _audio_append_payload(session_id, sequence=2),
        emit=emit,
    )

    await asyncio.sleep(0.8)

    assert any(
        item.get("type") == "conversation.interrupted" and item.get("reason") == "send_now"
        for item in emitted
    )
    interrupted_generation = next(
        item.get("generation_id")
        for item in emitted
        if item.get("type") == "conversation.interrupted" and item.get("reason") == "send_now"
    )
    assert any(
        item.get("type") == "route.selected"
        and item.get("generation_id")
        and item.get("generation_id") != interrupted_generation
        for item in emitted
    ), "Replacement generation should route after barge-in interruption"


def test_send_now_tool_running_interrupt_uses_latest_context_without_stall() -> None:
    asyncio.run(_test_send_now_tool_running_interrupt_uses_latest_context_without_stall())


async def _test_send_now_tool_running_interrupt_uses_latest_context_without_stall() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        MultiStreamFakeSttEngine(
            [
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="boxing.", sequence=1)],
                ],
                [
                    [SttEvent(kind=SttEventKind.FINAL, text="See?", sequence=2)],
                    [SttEvent(kind=SttEventKind.FINAL, text="E y", sequence=3)],
                    [],
                ],
            ]
        ),
        default=True,
    )

    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=0,
                        timestamp_ms=0.0,
                    )
                ],
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=1,
                        timestamp_ms=80.0,
                    )
                ],
                [
                    VadEvent(
                        kind=VadEventKind.START_OF_SPEECH,
                        sequence=2,
                        timestamp_ms=160.0,
                    )
                ],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_engine = ToolRunningThenDelayFakeLlmEngine(hold_seconds=0.8)
    llm_registry.register(llm_engine, default=True)

    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_queue": {"policy": "send_now"},
                "turn_detection": {"mode": "hybrid", "transcript_timeout_ms": 0},
            }
        )
    )
    session_id = start[0].session_id

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(_audio_append_payload(session_id, sequence=0), emit=emit)
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    async def _wait_for_tool_running(timeout_seconds: float = 1.2) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if any(
                item.get("type") == "llm.tool.update" and item.get("status") == "running"
                for item in emitted
            ):
                return
            await asyncio.sleep(0.01)
        raise AssertionError("Timed out waiting for llm.tool.update status=running")

    await _wait_for_tool_running()

    await session.apply(_audio_append_payload(session_id, sequence=1), emit=emit)
    await session.apply(_audio_append_payload(session_id, sequence=2), emit=emit)

    await asyncio.sleep(1.0)

    interrupt_events = [
        item
        for item in emitted
        if item.get("type") == "conversation.interrupted" and item.get("reason") == "send_now"
    ]
    assert interrupt_events, "send_now should interrupt active tool-running generation"

    interrupted_generation = interrupt_events[0].get("generation_id")
    assert isinstance(interrupted_generation, str)

    metrics_cancelled = [
        item
        for item in emitted
        if item.get("type") == "turn.metrics"
        and item.get("generation_id") == interrupted_generation
        and item.get("cancelled") is True
    ]
    assert metrics_cancelled, "Cancelled generation should emit cancelled turn.metrics"

    route_after_interrupt = [
        item
        for item in emitted
        if item.get("type") == "route.selected"
        and item.get("generation_id")
        and item.get("generation_id") != interrupted_generation
    ]
    assert route_after_interrupt, "Replacement generation should route after interruption"

    assert any(
        item.get("type") == "session.status"
        and item.get("status") == "thinking"
        and item.get("generation_id") != interrupted_generation
        for item in emitted
    ), "Replacement flow should transition into a new generation without stalling"


def test_send_now_stt_final_blocks_stale_old_generation_deltas() -> None:
    asyncio.run(_test_send_now_stt_final_blocks_stale_old_generation_deltas())


async def _test_send_now_stt_final_blocks_stale_old_generation_deltas() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        MultiStreamFakeSttEngine(
            [
                [
                    [],
                    [SttEvent(kind=SttEventKind.FINAL, text="first query", sequence=1)],
                ],
                [
                    [SttEvent(kind=SttEventKind.FINAL, text="latest final", sequence=2)],
                    [],
                ],
            ]
        ),
        default=True,
    )

    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=0, timestamp_ms=0.0)],
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=1, timestamp_ms=45.0)],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_engine = StaleLateDeltaFakeLlmEngine()
    llm_registry.register(llm_engine, default=True)

    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig.from_mapping({"llm": {"enable_fast_ack": False}}),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_queue": {"policy": "send_now"},
                "turn_detection": {"mode": "manual"},
                "interruption": {"cooldown_ms": 1000},
            }
        )
    )
    session_id = start_events[0].session_id

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(_audio_append_payload(session_id, sequence=0), emit=emit)
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    async def _wait_for_status(status: str, timeout_seconds: float = 1.2) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if any(
                item.get("type") == "session.status" and item.get("status") == status
                for item in emitted
            ):
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"Timed out waiting for session status '{status}'")

    await _wait_for_status("thinking")

    await session.apply(_audio_append_payload(session_id, sequence=1), emit=emit)
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )

    await asyncio.sleep(0.5)

    interrupt_event = next(
        item
        for item in emitted
        if item.get("type") == "conversation.interrupted" and item.get("reason") == "send_now"
    )
    interrupted_generation = interrupt_event.get("generation_id")
    assert isinstance(interrupted_generation, str)

    interruption_index = emitted.index(interrupt_event)
    assert all(
        not (
            item.get("generation_id") == interrupted_generation
            and item.get("type") in {"llm.response.delta", "llm.reasoning.delta", "tts.chunk"}
        )
        for item in emitted[interruption_index + 1 :]
    )

    assert any(
        item.get("type") == "llm.response.delta"
        and item.get("generation_id") != interrupted_generation
        for item in emitted
    )


def test_tool_search_speech_hints_are_batched_not_repetitive() -> None:
    asyncio.run(_test_tool_search_speech_hints_are_batched_not_repetitive())


async def _test_tool_search_speech_hints_are_batched_not_repetitive() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [
                    SttEvent(
                        kind=SttEventKind.FINAL, text="compare philosophers on time", sequence=1
                    )
                ],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_registry.register(
        FakeLlmEngine(
            [
                LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING),
                LlmEvent(
                    kind=LlmEventKind.TOOL_UPDATE,
                    call_id="call_1",
                    tool_name="websearch",
                    metadata={"status": "running", "is_mcp": True},
                    tool_input={"query": "marcus aurelius time"},
                ),
                LlmEvent(
                    kind=LlmEventKind.TOOL_UPDATE,
                    call_id="call_2",
                    tool_name="websearch",
                    metadata={"status": "running", "is_mcp": True},
                    tool_input={"query": "plato time"},
                ),
                LlmEvent(
                    kind=LlmEventKind.TOOL_UPDATE,
                    call_id="call_3",
                    tool_name="websearch",
                    metadata={"status": "running", "is_mcp": True},
                    tool_input={"query": "socrates time"},
                ),
                LlmEvent(
                    kind=LlmEventKind.TOOL_UPDATE,
                    call_id="call_4",
                    tool_name="websearch",
                    metadata={"status": "running", "is_mcp": True},
                    tool_input={"query": "anna time"},
                ),
                LlmEvent(
                    kind=LlmEventKind.TOOL_UPDATE,
                    call_id="call_1",
                    tool_name="websearch",
                    metadata={"status": "completed", "is_mcp": True},
                ),
                LlmEvent(
                    kind=LlmEventKind.TOOL_UPDATE,
                    call_id="call_2",
                    tool_name="websearch",
                    metadata={"status": "completed", "is_mcp": True},
                ),
                LlmEvent(
                    kind=LlmEventKind.TOOL_UPDATE,
                    call_id="call_3",
                    tool_name="websearch",
                    metadata={"status": "completed", "is_mcp": True},
                ),
                LlmEvent(
                    kind=LlmEventKind.TOOL_UPDATE,
                    call_id="call_4",
                    tool_name="websearch",
                    metadata={"status": "completed", "is_mcp": True},
                ),
                LlmEvent(
                    kind=LlmEventKind.RESPONSE_DELTA,
                    text="Here is a concise comparison.",
                    lane=LlmOutputLane.SPEECH,
                    part_id="part-1",
                ),
                LlmEvent(kind=LlmEventKind.COMPLETED, text="Here is a concise comparison."),
            ]
        ),
        default=True,
    )

    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig.from_mapping({"llm": {"enable_fast_ack": False}}),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start = await session.apply_message(SessionStartMessage())
    session_id = start[0].session_id

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(_audio_append_payload(session_id, sequence=0), emit=emit)
    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )
    await asyncio.sleep(0.1)

    speech_segments = [
        item.get("text_segment")
        for item in emitted
        if item.get("type") == "tts.chunk" and isinstance(item.get("text_segment"), str)
    ]
    start_announcements = [
        segment for segment in speech_segments if "checking a few web sources" in segment.lower()
    ]
    end_announcements = [
        segment
        for segment in speech_segments
        if "finished checking 4 web sources" in segment.lower()
    ]
    old_start = [
        segment for segment in speech_segments if "searching the web now" in segment.lower()
    ]
    old_end = [
        segment for segment in speech_segments if "web search is complete" in segment.lower()
    ]

    assert len(start_announcements) == 1
    assert len(end_announcements) == 1
    assert old_start == []
    assert old_end == []


async def _test_commit_emits_tool_update_events() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="Search for this", sequence=1)],
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)
    llm_registry = LlmEngineRegistry()
    llm_registry.register(
        FakeLlmEngine(
            [
                LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING),
                LlmEvent(
                    kind=LlmEventKind.TOOL_UPDATE,
                    call_id="call_1",
                    tool_name="websearch",
                    tool_input={"query": "Sahil Chokse"},
                    tool_metadata={"source": "opencode"},
                    tool_output={"hits": 3},
                    metadata={"status": "running", "is_mcp": True},
                ),
                LlmEvent(
                    kind=LlmEventKind.RESPONSE_DELTA,
                    text="I found a few results.",
                    lane=LlmOutputLane.SPEECH,
                    part_id="part-1",
                ),
                LlmEvent(
                    kind=LlmEventKind.COMPLETED,
                    text="I found a few results.",
                    finish_reason="stop",
                ),
            ]
        ),
        default=True,
    )
    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start = await session.apply_message(SessionStartMessage())
    session_id = start[0].session_id

    await session.apply_message(_audio_append_message(session_id, sequence=0))
    commit_events = await session.apply_message(AudioCommitMessage(session_id=session_id))

    tool_event = next(event for event in commit_events if event.type == "llm.tool.update")
    assert tool_event.call_id == "call_1"
    assert tool_event.tool_name == "websearch"
    assert tool_event.status == "running"
    assert tool_event.is_mcp is True


def test_auto_commit_accepts_vad_inference_silence_without_explicit_end() -> None:
    asyncio.run(_test_auto_commit_accepts_vad_inference_silence_without_explicit_end())


async def _test_auto_commit_accepts_vad_inference_silence_without_explicit_end() -> None:
    """Replays the stuck-turn symptom from test.txt.

    Sequence:
    - Turn 1: START -> END + stt.final (normal completion)
    - Turn 2: START -> INFERENCE(speaking=False) + stt.final (no explicit END)

    Regression guard: turn 2 must still auto-commit and route.
    """

    session_manager = InMemorySessionManager()

    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [SttEvent(kind=SttEventKind.FINAL, text="first turn", sequence=1)],
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="second turn", sequence=2)],
                [],
            ]
        ),
        default=True,
    )

    vad_registry = VadEngineRegistry()
    vad_registry.register(
        FakeVadEngine(
            [
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=0, timestamp_ms=0.0)],
                [VadEvent(kind=VadEventKind.END_OF_SPEECH, sequence=1, timestamp_ms=900.0)],
                [VadEvent(kind=VadEventKind.START_OF_SPEECH, sequence=2, timestamp_ms=1200.0)],
                [
                    VadEvent(
                        kind=VadEventKind.INFERENCE,
                        sequence=3,
                        timestamp_ms=2100.0,
                        speaking=False,
                        probability=0.05,
                    )
                ],
            ]
        ),
        default=True,
    )

    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("moderate_route"), default=True)

    llm_registry = LlmEngineRegistry()
    llm_registry.register(
        FakeLlmEngine(
            [
                LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING),
                LlmEvent(
                    kind=LlmEventKind.RESPONSE_DELTA,
                    text="ack",
                    lane=LlmOutputLane.SPEECH,
                    part_id="part-1",
                ),
                LlmEvent(
                    kind=LlmEventKind.COMPLETED,
                    text="ack",
                    finish_reason="stop",
                ),
            ]
        ),
        default=True,
    )

    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        vad_service=VadService(vad_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_detection": {
                    "mode": "hybrid",
                    "transcript_timeout_ms": 0,
                    "min_silence_duration_ms": 0,
                }
            }
        )
    )
    session_id = start[0].session_id

    first = await session.apply_message(_audio_append_message(session_id, sequence=0))
    second = await session.apply_message(_audio_append_message(session_id, sequence=1))
    third = await session.apply_message(_audio_append_message(session_id, sequence=2))
    fourth = await session.apply_message(_audio_append_message(session_id, sequence=3))

    route_first = [event for event in second if event.type == "route.selected"]
    route_second = [event for event in fourth if event.type == "route.selected"]

    assert event_types(first) == ["vad.state", "stt.final"]
    assert route_first, "Turn 1 should auto-commit and route"
    assert event_types(third) == ["vad.state", "stt.final"]
    assert route_second, (
        "Turn 2 should auto-commit on VAD INFERENCE speaking=False even without "
        "an explicit END_OF_SPEECH event"
    )

    state = await session_manager.get(session_id)
    assert len(state.turns) >= 2
    assert any((turn.user_text or "") for turn in state.turns)


def _audio_append_message(session_id: str, *, sequence: int) -> AudioAppendMessage:
    return AudioAppendMessage(
        session_id=session_id,
        chunk=AudioChunkPayload(
            chunk_id=f"{session_id}:{sequence}",
            sequence=sequence,
            encoding=AudioEncoding.PCM_S16LE.value,
            sample_rate_hz=16000,
            channels=1,
            duration_ms=20.0,
            transport=AudioTransport.INLINE_BASE64,
            data_base64=base64.b64encode(b"\x00\x00").decode("ascii"),
        ),
    )


def _audio_append_payload(session_id: str, *, sequence: int) -> dict[str, Any]:
    return {
        "type": "audio.append",
        "session_id": session_id,
        "chunk": {
            "chunk_id": f"{session_id}:{sequence}",
            "sequence": sequence,
            "encoding": AudioEncoding.PCM_S16LE.value,
            "sample_rate_hz": 16000,
            "channels": 1,
            "duration_ms": 20.0,
            "transport": AudioTransport.INLINE_BASE64.value,
            "data_base64": base64.b64encode(b"\x00\x00").decode("ascii"),
        },
    }


def test_commit_transitions_through_transcribing_before_thinking() -> None:
    asyncio.run(_test_commit_transitions_through_transcribing_before_thinking())


async def _test_commit_transitions_through_transcribing_before_thinking() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(
        FakeSttEngine(
            [
                [],
                [SttEvent(kind=SttEventKind.FINAL, text="Plan this.", sequence=1)],
            ]
        ),
        default=True,
    )
    router_registry = RouterEngineRegistry()
    router_registry.register(FakeRouterEngine("simple_route"), default=True)
    llm_registry = LlmEngineRegistry()
    llm_registry.register(
        FakeLlmEngine(
            [
                LlmEvent(kind=LlmEventKind.PHASE, phase=LlmPhase.THINKING),
                LlmEvent(
                    kind=LlmEventKind.RESPONSE_DELTA,
                    text="Done.",
                    lane=LlmOutputLane.SPEECH,
                    part_id="part-1",
                ),
                LlmEvent(kind=LlmEventKind.COMPLETED, text="Done.", finish_reason="stop"),
            ]
        ),
        default=True,
    )
    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(config={"turn_detection": {"stabilization_ms": 50}})
    )
    session_id = start_events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))
    commit_events = await session.apply_message(AudioCommitMessage(session_id=session_id))

    statuses = [
        event.status.value if hasattr(event.status, "value") else event.status
        for event in commit_events
        if event.type == "session.status"
    ]
    assert "transcribing" in statuses
    assert "thinking" in statuses
    assert statuses.index("transcribing") < statuses.index("thinking")


def test_stt_stabilization_uses_latest_final_revision_before_routing() -> None:
    asyncio.run(_test_stt_stabilization_uses_latest_final_revision_before_routing())


async def _test_stt_stabilization_uses_latest_final_revision_before_routing() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(DelayedFinalFakeSttEngine(), default=True)
    router_registry = RouterEngineRegistry()
    router_engine = FakeRouterEngine("simple_route")
    router_registry.register(router_engine, default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_detection": {
                    "stabilization_ms": 20,
                }
            }
        )
    )
    session_id = start_events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))
    await session.apply_message(AudioCommitMessage(session_id=session_id))

    assert router_engine.requests
    assert router_engine.requests[0].user_text == "hello there world"


def test_slow_stt_chained_revisions_produce_single_stable_turn_without_oscillation() -> None:
    asyncio.run(_test_slow_stt_chained_revisions_produce_single_stable_turn_without_oscillation())


async def _test_slow_stt_chained_revisions_produce_single_stable_turn_without_oscillation() -> None:
    session_manager = InMemorySessionManager()
    stt_registry = SttEngineRegistry()
    stt_registry.register(ChainedRevisionFakeSttEngine(), default=True)
    router_registry = RouterEngineRegistry()
    router_engine = FakeRouterEngine("moderate_route")
    router_registry.register(router_engine, default=True)
    llm_registry = LlmEngineRegistry()
    llm_engine = EchoDelayedFakeLlmEngine(delay_seconds=0.02)
    llm_registry.register(llm_engine, default=True)
    tts_registry = TtsEngineRegistry()
    tts_registry.register(FakeTtsEngine(), default=True)

    session = RealtimeConversationSession(
        session_manager,
        config=RuntimeConfig(),
        stt_service=SttService(stt_registry),
        router_service=RouterService(router_registry),
        llm_service=LlmService(llm_registry),
        tts_service=TtsService(tts_registry),
    )

    start_events = await session.apply_message(
        SessionStartMessage(
            config={
                "turn_queue": {"policy": "send_now"},
                "turn_detection": {"mode": "manual", "stabilization_ms": 40},
            }
        )
    )
    session_id = start_events[0].session_id
    await session.apply_message(_audio_append_message(session_id, sequence=0))

    emitted: list[dict[str, Any]] = []

    async def emit(payload: dict[str, Any]) -> None:
        emitted.append(payload)

    await session.apply(
        {
            "type": "audio.commit",
            "session_id": session_id,
        },
        emit=emit,
    )
    await asyncio.sleep(0.2)

    route_events = [item for item in emitted if item.get("type") == "route.selected"]
    thinking_statuses = [
        item
        for item in emitted
        if item.get("type") == "session.status" and item.get("status") == "thinking"
    ]
    assert len(route_events) == 1
    assert len(thinking_statuses) == 1
    assert router_engine.requests
    assert router_engine.requests[0].user_text == "something about socrates not god i do not"


def event_types(events: list[Any]) -> list[str]:
    return [event.type for event in events]
