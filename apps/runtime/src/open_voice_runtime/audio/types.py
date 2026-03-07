from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AudioEncoding(str, Enum):
    PCM_S16LE = "pcm_s16le"
    PCM_F32LE = "pcm_f32le"


@dataclass(frozen=True, slots=True)
class AudioFormat:
    sample_rate_hz: int
    channels: int
    encoding: AudioEncoding = AudioEncoding.PCM_S16LE


@dataclass(slots=True)
class AudioChunk:
    data: bytes
    format: AudioFormat
    sequence: int
    duration_ms: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AudioStreamConfig:
    format: AudioFormat
    chunk_duration_ms: int
    vad_enabled: bool = False
