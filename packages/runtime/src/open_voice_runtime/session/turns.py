from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from time import monotonic

from open_voice_runtime.audio.types import AudioChunk
from open_voice_runtime.stt.contracts import SttEvent, SttEventKind
from open_voice_runtime.vad.contracts import VadEvent, VadEventKind


class TurnDetectionMode(str, Enum):
    MANUAL = "manual"
    STT_TIMEOUT = "stt_timeout"
    VAD_TIMEOUT = "vad_timeout"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class TurnDetectionConfig:
    mode: TurnDetectionMode = TurnDetectionMode.MANUAL
    transcript_timeout_ms: int = 900
    stabilization_ms: int = 0
    min_silence_duration_ms: int = 600
    # EndPointing configuration (inspired by LiveKit)
    endpointing_mode: str = "fixed"  # "fixed" or "dynamic"
    endpointing_min_delay: float = (
        0.5  # Minimum time after speech to declare turn complete (seconds)
    )
    endpointing_max_delay: float = 3.0  # Maximum time to wait before terminating turn (seconds)


@dataclass(slots=True)
class SessionTurnBuffer:
    chunks: list[AudioChunk] = field(default_factory=list)
    commits: int = 0
    final_segments: dict[int, str] = field(default_factory=dict)
    interim_text: str = ""
    speaking: bool = False
    speech_started_at: float | None = None
    speech_ended_at: float | None = None
    last_audio_at: float | None = None
    last_stt_activity_at: float | None = None


@dataclass(frozen=True, slots=True)
class TurnRecognitionResult:
    stt_events: list[SttEvent]
    final_text: str | None
    vad_events: list[VadEvent] = field(default_factory=list)
    should_auto_commit: bool = False


class TurnRecognition:
    def __init__(self) -> None:
        self._buffers: dict[str, SessionTurnBuffer] = {}

    def buffer_for(self, session_id: str) -> SessionTurnBuffer:
        return self._buffers.setdefault(session_id, SessionTurnBuffer())

    def clear_buffer(self, session_id: str) -> None:
        """Clear the audio buffer for a session, typically after interruption."""
        self._buffers.pop(session_id, None)

    def buffered_final_text(self, session_id: str) -> str | None:
        """Return the current committed-final transcript snapshot for this session buffer."""
        buffer = self.buffer_for(session_id)
        return _committed_user_text(buffer)

    def seed_final_text(self, session_id: str, text: str) -> None:
        """Seed a final transcript segment into the current buffer.

        Used when preserving already-recognized speech across an interruption.
        """
        value = text.strip()
        if not value:
            return
        buffer = self.buffer_for(session_id)
        if value in buffer.final_segments.values():
            return
        sequence = max(buffer.final_segments.keys(), default=0) + 1
        buffer.final_segments[sequence] = value
        buffer.last_stt_activity_at = monotonic()

    def final_segment_count(self, session_id: str) -> int:
        """Return how many committed final segments are currently buffered."""
        buffer = self.buffer_for(session_id)
        return len(buffer.final_segments)

    def append_audio(self, session_id: str, chunk: AudioChunk) -> SessionTurnBuffer:
        buffer = self.buffer_for(session_id)
        buffer.chunks.append(chunk)
        buffer.last_audio_at = monotonic()
        return buffer

    def remember_stt_events(self, session_id: str, stt_events: list[SttEvent]) -> SessionTurnBuffer:
        buffer = self.buffer_for(session_id)
        if _remember_stt_activity(buffer, stt_events):
            buffer.last_stt_activity_at = monotonic()
        return buffer

    def snapshot_before_stt(self, session_id: str) -> SessionTurnBuffer:
        buffer = self.buffer_for(session_id)
        return SessionTurnBuffer(
            chunks=list(buffer.chunks),
            commits=buffer.commits,
            final_segments=dict(buffer.final_segments),
            interim_text=buffer.interim_text,
            speaking=buffer.speaking,
            speech_started_at=buffer.speech_started_at,
            speech_ended_at=buffer.speech_ended_at,
            last_audio_at=buffer.last_audio_at,
            last_stt_activity_at=buffer.last_stt_activity_at,
        )

    def remember_vad_events(self, session_id: str, vad_events: list[VadEvent]) -> SessionTurnBuffer:
        buffer = self.buffer_for(session_id)
        now = monotonic()
        for item in vad_events:
            if item.kind is VadEventKind.START_OF_SPEECH:
                buffer.speaking = True
                buffer.speech_started_at = now
                buffer.speech_ended_at = None
            elif item.kind is VadEventKind.END_OF_SPEECH:
                buffer.speaking = False
                buffer.speech_ended_at = now
            elif item.kind is VadEventKind.INFERENCE:
                # Handle speaking transition from True to False in inference events
                # (some VAD implementations don't emit explicit END_OF_SPEECH)
                if buffer.speaking and item.speaking is False:
                    buffer.speaking = False
                    buffer.speech_ended_at = now
                elif not buffer.speaking and item.speaking is True:
                    buffer.speaking = True
                    buffer.speech_started_at = now
                    buffer.speech_ended_at = None
                elif item.speaking is not None:
                    buffer.speaking = item.speaking
        return buffer

    async def collect_commit_result(
        self,
        session_id: str,
        drain: Callable[[float], Awaitable[list[SttEvent]]],
        *,
        timeout_seconds: float,
        stabilization_seconds: float = 0.0,
    ) -> TurnRecognitionResult:
        buffer = self.buffer_for(session_id)
        stt_events = await _drain_commit_events(
            drain,
            timeout_seconds=timeout_seconds,
            stabilization_seconds=stabilization_seconds,
        )
        _remember_final_segments(buffer, stt_events)
        return TurnRecognitionResult(
            stt_events=stt_events,
            final_text=_committed_user_text(buffer),
        )

    def evaluate_auto_commit(
        self,
        session_id: str,
        *,
        config: TurnDetectionConfig,
        stt_events: list[SttEvent],
        vad_events: list[VadEvent],
    ) -> TurnRecognitionResult:
        buffer = self.buffer_for(session_id)
        buffer_before_stt = self.snapshot_before_stt(session_id)
        self.remember_stt_events(session_id, stt_events)
        self.remember_vad_events(session_id, vad_events)
        now = monotonic()
        saw_new_stt_activity = _stt_events_changed(buffer_before_stt, stt_events)

        final_text = _committed_user_text(buffer)
        should_auto_commit = False

        if config.mode is TurnDetectionMode.MANUAL:
            should_auto_commit = False
        elif config.mode is TurnDetectionMode.STT_TIMEOUT:
            should_auto_commit = _stt_timeout_ready(buffer, now, config)
        elif config.mode is TurnDetectionMode.VAD_TIMEOUT:
            should_auto_commit = _vad_timeout_ready(buffer, now, config)
        elif config.mode is TurnDetectionMode.HYBRID:
            should_auto_commit = _hybrid_ready(
                buffer,
                now,
                config,
                saw_new_stt_activity=saw_new_stt_activity,
            )

        return TurnRecognitionResult(
            stt_events=stt_events,
            vad_events=vad_events,
            final_text=final_text,
            should_auto_commit=should_auto_commit,
        )

    def fake_commit_result(self, session_id: str) -> TurnRecognitionResult:
        buffer = self.buffer_for(session_id)
        if not buffer.chunks:
            return TurnRecognitionResult(stt_events=[], final_text=None, should_auto_commit=False)
        text = _render_fake_transcript(buffer)
        stt_events = [
            SttEvent(
                kind=SttEventKind.PARTIAL,
                text=f"processing {len(buffer.chunks)} audio chunks",
                sequence=buffer.commits,
            ),
            SttEvent(
                kind=SttEventKind.FINAL,
                text=text,
                sequence=buffer.commits,
                confidence=0.25,
            ),
        ]
        _remember_final_segments(buffer, stt_events)
        return TurnRecognitionResult(stt_events=stt_events, final_text=text)

    def clear_turn(self, session_id: str) -> None:
        buffer = self.buffer_for(session_id)
        buffer.chunks.clear()
        buffer.final_segments.clear()
        buffer.interim_text = ""
        buffer.speaking = False
        buffer.speech_started_at = None
        buffer.speech_ended_at = None
        buffer.last_audio_at = None
        buffer.last_stt_activity_at = None

    def interrupt(self, session_id: str) -> None:
        """Interrupt the current turn, clearing all buffered audio and STT state.

        This is called when the user interrupts the assistant (barge-in).
        Similar to LiveKit's session.interrupt() pattern.
        """
        self.clear_turn(session_id)

    def clear_user_turn(self, session_id: str) -> None:
        """Clear the user's current turn, discarding all buffered audio and transcripts.

        This is called after an interrupt to ensure we don't process any audio
        that was received during the assistant's response. Similar to LiveKit's
        session.clear_user_turn() pattern.

        This is an alias for clear_turn() to match LiveKit's naming convention.
        """
        self.clear_turn(session_id)

    def complete_turn(self, session_id: str) -> None:
        buffer = self.buffer_for(session_id)
        buffer.chunks.clear()
        buffer.final_segments.clear()
        buffer.interim_text = ""
        buffer.speaking = False
        buffer.speech_started_at = None
        buffer.speech_ended_at = None
        buffer.commits += 1

    def close(self, session_id: str) -> None:
        self._buffers.pop(session_id, None)


async def _drain_commit_events(
    drain: Callable[[float], Awaitable[list[SttEvent]]],
    *,
    timeout_seconds: float,
    stabilization_seconds: float,
) -> list[SttEvent]:
    events: list[SttEvent] = []
    saw_final = False
    latest_final_text: str | None = None
    latest_final_changed_at: float | None = None
    deadline = asyncio.get_running_loop().time() + timeout_seconds

    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return events

        batch = await drain(min(0.25, remaining))
        now = asyncio.get_running_loop().time()
        if batch:
            events.extend(batch)
            saw_final = saw_final or any(item.kind is SttEventKind.FINAL for item in batch)
            if saw_final and stabilization_seconds > 0.0:
                final_text = _final_text_from_events(events)
                if final_text is not None and final_text.strip():
                    if final_text != latest_final_text:
                        latest_final_text = final_text
                        latest_final_changed_at = now
            continue

        if saw_final:
            if stabilization_seconds <= 0.0:
                return events
            if latest_final_changed_at is None:
                return events
            if now - latest_final_changed_at >= stabilization_seconds:
                return events
            continue

        if not saw_final:
            continue


def _final_text_from_events(stt_events: list[SttEvent]) -> str | None:
    by_sequence: dict[int, str] = {}
    for item in stt_events:
        if item.kind is not SttEventKind.FINAL:
            continue
        text = item.text.strip()
        if text:
            by_sequence[item.sequence] = text
    if not by_sequence:
        return None
    return " ".join(text for _, text in sorted(by_sequence.items()) if text)


def _remember_final_segments(buffer: SessionTurnBuffer, stt_events: list[SttEvent]) -> None:
    for item in stt_events:
        if item.kind is not SttEventKind.FINAL:
            continue
        text = item.text.strip()
        if text:
            buffer.final_segments[item.sequence] = text


def _remember_stt_activity(buffer: SessionTurnBuffer, stt_events: list[SttEvent]) -> bool:
    saw_activity = False
    for item in stt_events:
        if item.kind is SttEventKind.PARTIAL:
            text = item.text.strip()
            if text and text != buffer.interim_text.strip():
                saw_activity = True
            buffer.interim_text = item.text
            continue
        if item.kind is SttEventKind.FINAL:
            text = item.text.strip()
            if text and buffer.final_segments.get(item.sequence) != text:
                buffer.final_segments[item.sequence] = text
                saw_activity = True
            buffer.interim_text = ""
    return saw_activity


def _stt_events_changed(previous: SessionTurnBuffer, stt_events: list[SttEvent]) -> bool:
    interim_before = previous.interim_text.strip()
    finals_before = dict(previous.final_segments)
    for item in stt_events:
        if item.kind is SttEventKind.PARTIAL:
            if item.text.strip() != interim_before:
                return True
            continue
        if item.kind is SttEventKind.FINAL:
            text = item.text.strip()
            if text and finals_before.get(item.sequence) != text:
                return True
    return False


def _committed_user_text(buffer: SessionTurnBuffer) -> str | None:
    segments = [text for _, text in sorted(buffer.final_segments.items()) if text]
    if not segments:
        return None
    return " ".join(segments)


def _render_fake_transcript(buffer: SessionTurnBuffer) -> str:
    count = len(buffer.chunks)
    total = sum(len(chunk.data) for chunk in buffer.chunks)
    ms = sum(chunk.duration_ms or 0.0 for chunk in buffer.chunks)
    return f"stub transcript from {count} audio chunks ({total} bytes, {ms:.1f} ms)"


def _stt_timeout_ready(
    buffer: SessionTurnBuffer,
    now: float,
    config: TurnDetectionConfig,
) -> bool:
    if not _has_stt_candidate(buffer):
        return False
    if buffer.last_stt_activity_at is None:
        return False
    return (now - buffer.last_stt_activity_at) * 1000.0 >= config.transcript_timeout_ms


def _vad_timeout_ready(
    buffer: SessionTurnBuffer,
    now: float,
    config: TurnDetectionConfig,
) -> bool:
    if buffer.speaking or buffer.speech_ended_at is None:
        return False
    return (now - buffer.speech_ended_at) * 1000.0 >= config.min_silence_duration_ms


def _hybrid_ready(
    buffer: SessionTurnBuffer,
    now: float,
    config: TurnDetectionConfig,
    *,
    saw_new_stt_activity: bool,
) -> bool:
    stt_ready = _stt_timeout_ready(buffer, now, config)
    vad_ready = _vad_timeout_ready(buffer, now, config)
    has_turn_candidate = _has_turn_candidate(buffer)

    if stt_ready and vad_ready:
        return True
    if vad_ready and has_turn_candidate:
        return True
    if stt_ready and _has_stt_candidate(buffer) and not saw_new_stt_activity:
        return True
    return False


def _has_turn_candidate(buffer: SessionTurnBuffer) -> bool:
    if buffer.final_segments:
        return True
    if buffer.interim_text.strip():
        return True
    if buffer.last_stt_activity_at is not None:
        return True
    return buffer.speech_started_at is not None and bool(buffer.chunks)


def _has_stt_candidate(buffer: SessionTurnBuffer) -> bool:
    if buffer.final_segments:
        return True
    return bool(buffer.interim_text.strip())
