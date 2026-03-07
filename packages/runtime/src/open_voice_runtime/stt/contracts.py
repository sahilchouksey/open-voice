from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from open_voice_runtime.audio.types import AudioChunk, AudioFormat


class SttEventKind(str, Enum):
    PARTIAL = "partial"
    FINAL = "final"


@dataclass(frozen=True, slots=True)
class SttCapabilities:
    streaming: bool = True
    batch: bool = True
    partial_results: bool = True
    languages: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SttConfig:
    language: str | None = None
    partial_results: bool = True


@dataclass(slots=True)
class SttFileRequest:
    audio: bytes
    audio_format: AudioFormat
    config: SttConfig = field(default_factory=SttConfig)


@dataclass(slots=True)
class SttFileResult:
    text: str
    confidence: float | None = None
    language: str | None = None
    duration_ms: float | None = None


@dataclass(slots=True)
class SttEvent:
    kind: SttEventKind
    text: str
    sequence: int
    confidence: float | None = None
    chunk: AudioChunk | None = None
