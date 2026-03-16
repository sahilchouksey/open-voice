from __future__ import annotations

from collections.abc import AsyncIterator

from open_voice_runtime.audio.types import AudioChunk, AudioEncoding, AudioFormat
from open_voice_runtime.core.errors import AudioFormatError
from open_voice_runtime.integrations.kokoro import (
    DEFAULT_KOKORO_SAMPLE_RATE_HZ,
    KOKORO_VOICE_IDS,
    KokoroClient,
    kokoro_voice_language,
)
from open_voice_runtime.tts.contracts import (
    TtsCapabilities,
    TtsEvent,
    TtsEventKind,
    TtsRequest,
    TtsResult,
    TtsVoice,
)
from open_voice_runtime.tts.engine import BaseTtsEngine

KOKORO_TTS_VOICES = tuple(
    TtsVoice(id=voice_id, label=voice_id, language=kokoro_voice_language(voice_id))
    for voice_id in KOKORO_VOICE_IDS
)


class KokoroTtsEngine(BaseTtsEngine):
    id = "kokoro"
    label = "Kokoro ONNX"
    capabilities = TtsCapabilities(streaming=True, voices=KOKORO_TTS_VOICES)

    def __init__(self, client: KokoroClient | None = None) -> None:
        self._client = client or KokoroClient()
        self.available = self._client.available
        self.status = self._client.status

    async def load(self) -> None:
        await self._client.load()
        self.available = self._client.available
        self.status = self._client.status

    async def close(self) -> None:
        await self._client.close()

    async def synthesize(self, request: TtsRequest) -> TtsResult:
        stream = await self.stream(request)
        chunks: list[bytes] = []
        duration_ms: float | None = None
        async for item in stream:
            if item.kind is TtsEventKind.AUDIO_CHUNK and item.audio_chunk is not None:
                chunks.append(item.audio_chunk.data)
            if item.kind is TtsEventKind.COMPLETED:
                duration_ms = item.duration_ms
        return TtsResult(
            audio=b"".join(chunks),
            audio_format=request.audio_format,
            duration_ms=duration_ms,
            metadata={"voice_id": request.voice_id, "language": request.language},
        )

    async def stream(self, request: TtsRequest) -> AsyncIterator[TtsEvent]:
        await self.load()
        _validate_request_audio_format(request.audio_format)
        speed = _request_speed(request)

        async def generator() -> AsyncIterator[TtsEvent]:
            sequence = 0
            total_duration_ms = 0.0
            saw_duration = False
            async for segment in self._client.stream_synthesis(
                text=request.text,
                voice_id=request.voice_id,
                language=request.language,
                speed=speed,
                is_phonemes=_request_is_phonemes(request),
                trim=_request_trim(request),
            ):
                duration_ms = segment.duration_ms
                if duration_ms is not None:
                    total_duration_ms += duration_ms
                    saw_duration = True
                yield TtsEvent(
                    kind=TtsEventKind.AUDIO_CHUNK,
                    audio_chunk=AudioChunk(
                        data=segment.audio,
                        format=request.audio_format,
                        sequence=sequence,
                        duration_ms=duration_ms,
                        metadata={"voice_id": segment.voice_id or request.voice_id},
                    ),
                    text_segment=segment.text or _request_text_segment(request),
                    duration_ms=duration_ms,
                )
                sequence += 1

            yield TtsEvent(
                kind=TtsEventKind.COMPLETED,
                duration_ms=total_duration_ms if saw_duration else None,
            )

        return generator()


def _validate_request_audio_format(audio_format: AudioFormat) -> None:
    if audio_format.channels != 1:
        raise AudioFormatError(
            "Kokoro only supports mono output.",
            details={"channels": audio_format.channels},
        )
    if audio_format.encoding is not AudioEncoding.PCM_S16LE:
        raise AudioFormatError(
            "Kokoro only supports PCM s16le output.",
            details={"encoding": audio_format.encoding.value},
        )
    if audio_format.sample_rate_hz != DEFAULT_KOKORO_SAMPLE_RATE_HZ:
        raise AudioFormatError(
            "Kokoro currently emits 24kHz audio only.",
            details={"sample_rate_hz": audio_format.sample_rate_hz},
        )


def _request_speed(request: TtsRequest) -> float | None:
    for key in ("tts_speed", "speed"):
        value = request.metadata.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                continue
    return None


def _request_is_phonemes(request: TtsRequest) -> bool:
    value = request.metadata.get("is_phonemes")
    return value is True


def _request_trim(request: TtsRequest) -> bool:
    value = request.metadata.get("trim")
    if isinstance(value, bool):
        return value
    return True


def _request_text_segment(request: TtsRequest) -> str:
    value = request.metadata.get("text_segment")
    if isinstance(value, str) and value.strip():
        return value
    return request.text
