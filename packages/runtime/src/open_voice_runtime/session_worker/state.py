from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

from open_voice_runtime.session.turns import TurnDetectionConfig


class TurnLifecycle(str, Enum):
    IDLE = "idle"
    BUFFERING = "buffering"
    SPEECH_DETECTED = "speech_detected"
    ENDPOINT_PENDING = "endpoint_pending"
    TRANSCRIPTION_QUEUED = "transcription_queued"
    TRANSCRIBING = "transcribing"
    ROUTING = "routing"
    RESPONDING = "responding"
    STREAMING_OUTPUT = "streaming_output"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(slots=True)
class HostTranscriptionCapacity:
    min_workers: int
    max_workers: int
    configured_workers: int
    active_jobs: int = 0
    queued_jobs: int = 0


@dataclass(slots=True)
class TurnTrace:
    started_at: float
    transcription_queued_at: float | None = None
    transcription_started_at: float | None = None
    transcription_completed_at: float | None = None
    route_selected_at: float | None = None
    llm_started_at: float | None = None
    first_llm_delta_at: float | None = None
    tts_started_at: float | None = None
    completed_at: float | None = None
    cancelled: bool = False
    reason: str | None = None


@dataclass(slots=True)
class QueuedUtterance:
    client_turn_id: str | None
    utterance: "BufferedUtterance"


@dataclass(slots=True)
class SessionWorkerRuntimeState:
    lifecycle: TurnLifecycle = TurnLifecycle.IDLE
    active_generation_id: str | None = None
    active_turn_id: str | None = None
    response_task: asyncio.Task[None] | None = None
    current_assistant_text: str = ""
    queued_utterances: list[QueuedUtterance] = field(default_factory=list)
    current_trace: TurnTrace | None = None
    turn_detection: TurnDetectionConfig = field(default_factory=TurnDetectionConfig)


from open_voice_runtime.session_worker.input_buffer import BufferedUtterance
