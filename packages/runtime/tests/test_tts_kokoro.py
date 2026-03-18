from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

import open_voice_runtime.app.dependencies as dependencies_module
import open_voice_runtime.integrations.kokoro.client as kokoro_client_module
from open_voice_runtime.app.dependencies import build_runtime_dependencies
from open_voice_runtime.audio.types import AudioEncoding, AudioFormat
from open_voice_runtime.core.errors import AudioFormatError
from open_voice_runtime.integrations.kokoro import KokoroAudioSegment
from open_voice_runtime.tts.contracts import TtsEventKind, TtsRequest
from open_voice_runtime.tts.engines.kokoro import KokoroTtsEngine


class FakeKokoroClient:
    def __init__(self, segments: list[KokoroAudioSegment]) -> None:
        self._segments = segments
        self.requests: list[dict[str, Any]] = []
        self.available = True
        self.status = "ready"

    async def load(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def stream_synthesis(
        self,
        *,
        text: str,
        voice_id: str | None = None,
        language: str | None = None,
        speed: float | None = None,
        is_phonemes: bool = False,
        trim: bool = True,
    ) -> AsyncIterator[KokoroAudioSegment]:
        self.requests.append(
            {
                "text": text,
                "voice_id": voice_id,
                "language": language,
                "speed": speed,
                "is_phonemes": is_phonemes,
                "trim": trim,
            }
        )
        for segment in self._segments:
            yield segment


def test_kokoro_engine_streams_pcm_audio_chunks() -> None:
    asyncio.run(_test_kokoro_engine_streams_pcm_audio_chunks())


async def _test_kokoro_engine_streams_pcm_audio_chunks() -> None:
    client = FakeKokoroClient(
        [
            KokoroAudioSegment(
                text="Hello there.",
                audio=b"\x01\x00\x02\x00",
                sample_rate_hz=24000,
                duration_ms=20.0,
                voice_id="af_heart",
            ),
            KokoroAudioSegment(
                text="General Kenobi.",
                audio=b"\x03\x00\x04\x00",
                sample_rate_hz=24000,
                duration_ms=30.0,
                voice_id="af_heart",
            ),
        ]
    )
    engine = KokoroTtsEngine(client=client)

    stream = await engine.stream(
        _tts_request(
            metadata={"tts_speed": "1.15"},
        )
    )
    events = [event async for event in stream]

    assert [event.kind for event in events] == [
        TtsEventKind.AUDIO_CHUNK,
        TtsEventKind.AUDIO_CHUNK,
        TtsEventKind.COMPLETED,
    ]
    assert client.requests[0] == {
        "text": "Hello there. General Kenobi.",
        "voice_id": "af_heart",
        "language": "en-US",
        "speed": 1.15,
        "is_phonemes": False,
        "trim": True,
    }
    assert events[0].audio_chunk is not None
    assert events[0].audio_chunk.sequence == 0
    assert events[0].audio_chunk.data == b"\x01\x00\x02\x00"
    assert events[1].audio_chunk is not None
    assert events[1].audio_chunk.sequence == 1
    assert events[1].text_segment == "General Kenobi."
    assert events[2].duration_ms == 50.0


def test_kokoro_engine_rejects_unsupported_audio_format() -> None:
    asyncio.run(_test_kokoro_engine_rejects_unsupported_audio_format())


async def _test_kokoro_engine_rejects_unsupported_audio_format() -> None:
    engine = KokoroTtsEngine(client=FakeKokoroClient([]))

    with pytest.raises(AudioFormatError):
        await engine.stream(
            _tts_request(
                audio_format=AudioFormat(
                    sample_rate_hz=16000,
                    channels=1,
                    encoding=AudioEncoding.PCM_S16LE,
                )
            )
        )


def test_build_runtime_dependencies_registers_kokoro_when_available(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kokoro_client_module, "kokoro_backend_available", lambda: True)
    model_path = tmp_path / "kokoro-v1.0.onnx"
    voices_path = tmp_path / "voices-v1.0.bin"
    model_path.write_bytes(b"model")
    voices_path.write_bytes(b"voices")
    monkeypatch.setenv("OPEN_VOICE_KOKORO_ONNX_ASSET_DIR", str(tmp_path))

    dependencies = build_runtime_dependencies()

    assert dependencies.tts_registry.has("kokoro")
    assert dependencies.tts_registry.get_default().id == "kokoro"
    assert dependencies.tts_service.is_available("kokoro") is True

    entry = next(item for item in dependencies.engine_catalog["tts"] if item.id == "kokoro")
    assert entry.available is True
    assert entry.status == "ready"


def test_build_runtime_dependencies_marks_kokoro_unavailable_without_assets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(kokoro_client_module, "kokoro_backend_available", lambda: True)
    monkeypatch.delenv("OPEN_VOICE_KOKORO_ONNX_ASSET_DIR", raising=False)
    monkeypatch.delenv("OPEN_VOICE_KOKORO_ONNX_MODEL_PATH", raising=False)
    monkeypatch.delenv("OPEN_VOICE_KOKORO_ONNX_VOICES_PATH", raising=False)

    dependencies = build_runtime_dependencies()

    assert dependencies.tts_registry.has("kokoro")
    assert dependencies.tts_service.is_available("kokoro") is False

    entry = next(item for item in dependencies.engine_catalog["tts"] if item.id == "kokoro")
    assert entry.available is False
    assert entry.status == "missing_assets"


def _tts_request(
    *,
    audio_format: AudioFormat | None = None,
    metadata: dict[str, Any] | None = None,
) -> TtsRequest:
    return TtsRequest(
        session_id="session-1",
        turn_id="turn-1",
        text="Hello there. General Kenobi.",
        audio_format=audio_format
        or AudioFormat(
            sample_rate_hz=24000,
            channels=1,
            encoding=AudioEncoding.PCM_S16LE,
        ),
        voice_id="af_heart",
        language="en-US",
        metadata=metadata or {},
    )
