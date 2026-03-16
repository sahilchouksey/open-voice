from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from open_voice_runtime.audio.types import AudioChunk


class VadEventKind(str, Enum):
    START_OF_SPEECH = "start_of_speech"
    INFERENCE = "inference"
    END_OF_SPEECH = "end_of_speech"


@dataclass(frozen=True, slots=True)
class VadCapabilities:
    streaming: bool = True
    sample_rates_hz: tuple[int, ...] = (16000,)


@dataclass(frozen=True, slots=True)
class VadConfig:
    min_speech_duration_ms: int = 100
    min_silence_duration_ms: int = 600
    activation_threshold: float = 0.5
    chunk_size: int = 512


@dataclass(slots=True)
class VadEvent:
    kind: VadEventKind
    sequence: int
    timestamp_ms: float
    probability: float | None = None
    speaking: bool | None = None
    speech_duration_ms: float | None = None
    silence_duration_ms: float | None = None
    chunk: AudioChunk | None = None


@dataclass(slots=True)
class VadResult:
    events: list[VadEvent] = field(default_factory=list)
