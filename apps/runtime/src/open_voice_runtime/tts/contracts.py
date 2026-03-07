from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from open_voice_runtime.audio.types import AudioChunk, AudioFormat


class TtsEventKind(str, Enum):
    AUDIO_CHUNK = "audio_chunk"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class TtsVoice:
    id: str
    label: str
    language: str | None = None


@dataclass(frozen=True, slots=True)
class TtsCapabilities:
    streaming: bool = True
    voices: tuple[TtsVoice, ...] = ()


@dataclass(slots=True)
class TtsRequest:
    session_id: str
    turn_id: str
    text: str
    audio_format: AudioFormat
    voice_id: str | None = None
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TtsResult:
    audio: bytes
    audio_format: AudioFormat
    duration_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TtsEvent:
    kind: TtsEventKind
    audio_chunk: AudioChunk | None = None
    text_segment: str | None = None
    duration_ms: float | None = None
