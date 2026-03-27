from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import dataclass
import os
from typing import Any, cast

from open_voice_runtime.audio.preprocessing import (
    audio_bytes_to_mono_floats,
    audio_chunk_to_mono_floats,
)
from open_voice_runtime.audio.types import AudioChunk
from open_voice_runtime.integrations.moonshine_voice import MoonshineVoiceClient
from open_voice_runtime.stt.contracts import (
    SttCapabilities,
    SttConfig,
    SttEvent,
    SttEventKind,
    SttFileRequest,
    SttFileResult,
)
from open_voice_runtime.stt.engine import BaseSttEngine, BaseSttStream


@dataclass(slots=True)
class MoonshineStreamState:
    stream: Any
    queue: asyncio.Queue[SttEvent | Exception | None]


class MoonshineSttStream(BaseSttStream):
    def __init__(self, client: MoonshineVoiceClient, transcriber: Any, config: SttConfig) -> None:
        self._client = client
        self._transcriber = transcriber
        self._config = config
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[SttEvent | Exception | None] = asyncio.Queue()
        self._closed = False
        self._line_update_interval = _moonshine_update_interval_seconds()
        self._state = self._create_state()

    async def push_audio(self, chunk: AudioChunk) -> None:
        samples = audio_chunk_to_mono_floats(chunk)
        await asyncio.to_thread(self._state.stream.add_audio, samples, chunk.format.sample_rate_hz)

    async def flush(self) -> None:
        transcript = await asyncio.to_thread(self._state.stream.stop)
        self._emit_completed_transcript(transcript)
        await asyncio.to_thread(self._state.stream.close)
        self._state = self._create_state()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await asyncio.to_thread(self._state.stream.close)
        await self._queue.put(None)

    async def events(self) -> AsyncGenerator[SttEvent, None]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield cast(SttEvent, item)

    async def drain(self, wait_seconds: float = 0.0) -> list[SttEvent]:
        items: list[SttEvent] = []

        if wait_seconds > 0.0:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=wait_seconds)
            except TimeoutError:
                return []
            items.extend(self._coerce_queue_item(item))

        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            items.extend(self._coerce_queue_item(item))

        return items

    def _create_state(self) -> MoonshineStreamState:
        listener_base = self._client.listener_base()
        line_started_type = self._client.line_started_type()
        line_text_changed_type = self._client.line_text_changed_type()
        line_completed_type = self._client.line_completed_type()
        queue = self._queue
        loop = self._loop

        class Listener(listener_base):
            def on_line_started(self, event):
                if event.line.text:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        SttEvent(
                            kind=SttEventKind.PARTIAL,
                            text=event.line.text,
                            sequence=int(event.line.line_id),
                            confidence=None,
                        ),
                    )

            def on_line_text_changed(self, event):
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    SttEvent(
                        kind=SttEventKind.PARTIAL,
                        text=event.line.text,
                        sequence=int(event.line.line_id),
                        confidence=None,
                    ),
                )

            def on_line_completed(self, event):
                if event.line.text:
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        SttEvent(
                            kind=SttEventKind.FINAL,
                            text=event.line.text,
                            sequence=int(event.line.line_id),
                            confidence=None,
                        ),
                    )

            def on_error(self, event):
                err = RuntimeError(str(event.error))
                loop.call_soon_threadsafe(queue.put_nowait, err)

        stream = self._transcriber.create_stream(update_interval=self._line_update_interval)
        stream.add_listener(Listener())
        stream.start()
        return MoonshineStreamState(stream=stream, queue=self._queue)

    def _emit_completed_transcript(self, transcript: Any) -> None:
        for line in getattr(transcript, "lines", []):
            if not getattr(line, "text", ""):
                continue
            self._queue.put_nowait(
                SttEvent(
                    kind=SttEventKind.FINAL,
                    text=line.text,
                    sequence=int(line.line_id),
                    confidence=None,
                )
            )

    def _coerce_queue_item(self, item: SttEvent | Exception | None) -> list[SttEvent]:
        if item is None:
            return []
        if isinstance(item, Exception):
            raise item
        return [item]


class MoonshineSttEngine(BaseSttEngine):
    id = "moonshine"
    label = "Moonshine Voice"
    capabilities = SttCapabilities(
        streaming=True,
        batch=True,
        partial_results=True,
        languages=("en",),
    )

    def __init__(self, client: MoonshineVoiceClient | None = None) -> None:
        self._client = client or MoonshineVoiceClient()
        self._transcriber: Any | None = None

    async def load(self) -> None:
        if self._transcriber is not None:
            return
        self._transcriber = await asyncio.to_thread(self._client.create_transcriber)

    async def close(self) -> None:
        if self._transcriber is None:
            return
        transcriber = self._transcriber
        self._transcriber = None
        await asyncio.to_thread(transcriber.close)

    async def create_stream(self, config: SttConfig) -> BaseSttStream:
        await self.load()
        assert self._transcriber is not None
        return MoonshineSttStream(self._client, self._transcriber, config)

    async def transcribe_file(self, request: SttFileRequest) -> SttFileResult:
        await self.load()
        assert self._transcriber is not None
        audio = audio_bytes_to_mono_floats(request.audio, request.audio_format)
        transcript = await asyncio.to_thread(
            self._transcriber.transcribe_without_streaming,
            audio,
            request.audio_format.sample_rate_hz,
        )
        text = " ".join(line.text for line in transcript.lines if line.text).strip()
        return SttFileResult(
            text=text,
            confidence=None,
            language=request.config.language,
            duration_ms=None,
        )


def _moonshine_update_interval_seconds() -> float:
    raw = os.getenv("OPEN_VOICE_MOONSHINE_UPDATE_INTERVAL_MS")
    if raw is None:
        return 0.08
    try:
        value_ms = float(raw)
    except (TypeError, ValueError):
        return 0.08
    value_ms = max(40.0, min(200.0, value_ms))
    return value_ms / 1000.0
