from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from time import monotonic

from open_voice_runtime.audio.types import AudioEncoding, AudioFormat
from open_voice_runtime.session_worker.input_buffer import BufferedUtterance
from open_voice_runtime.session_worker.state import HostTranscriptionCapacity
from open_voice_runtime.stt.contracts import SttConfig, SttFileRequest
from open_voice_runtime.stt.service import SttService


@dataclass(slots=True)
class TranscriptionResult:
    text: str
    confidence: float | None
    queued_ms: int
    execution_ms: int
    duration_ms: float | None
    language: str | None


def _resolve_transcription_worker_count(min_workers: int = 1, max_workers: int = 4) -> int:
    cpu_count = max(1, os.cpu_count() or 1)
    configured = 1
    if cpu_count <= 2:
        configured = 1
    elif cpu_count <= 4:
        configured = 2
    elif cpu_count <= 8:
        configured = 3
    else:
        configured = 4
    return max(min_workers, min(max_workers, configured))


class TranscriptionCoordinator:
    def __init__(
        self,
        stt_service: SttService | None,
        *,
        min_workers: int = 1,
        max_workers: int = 4,
    ) -> None:
        self._stt_service = stt_service
        self._capacity = HostTranscriptionCapacity(
            min_workers=min_workers,
            max_workers=max_workers,
            configured_workers=_resolve_transcription_worker_count(min_workers, max_workers),
        )
        self._semaphore = asyncio.Semaphore(self._capacity.configured_workers)
        self._metrics_lock = asyncio.Lock()

    def metrics_snapshot(self) -> HostTranscriptionCapacity:
        return HostTranscriptionCapacity(
            min_workers=self._capacity.min_workers,
            max_workers=self._capacity.max_workers,
            configured_workers=self._capacity.configured_workers,
            active_jobs=self._capacity.active_jobs,
            queued_jobs=self._capacity.queued_jobs,
        )

    async def transcribe(
        self,
        utterance: BufferedUtterance,
        *,
        engine_id: str | None = None,
        language: str | None = None,
    ) -> TranscriptionResult:
        if self._stt_service is None:
            return TranscriptionResult(
                text="",
                confidence=None,
                queued_ms=0,
                execution_ms=0,
                duration_ms=utterance.duration_ms,
                language=language,
            )
        queued_at = monotonic()
        async with self._metrics_lock:
            self._capacity.queued_jobs += 1
        async with self._semaphore:
            started_at = monotonic()
            async with self._metrics_lock:
                self._capacity.queued_jobs = max(0, self._capacity.queued_jobs - 1)
                self._capacity.active_jobs += 1
            try:
                result = await self._stt_service.transcribe_file(
                    SttFileRequest(
                        audio=utterance.pcm_bytes,
                        audio_format=AudioFormat(
                            sample_rate_hz=utterance.sample_rate_hz,
                            channels=utterance.channels,
                            encoding=AudioEncoding(utterance.encoding),
                        ),
                        config=SttConfig(language=language, partial_results=False),
                    ),
                    engine_id=engine_id,
                )
            finally:
                async with self._metrics_lock:
                    self._capacity.active_jobs = max(0, self._capacity.active_jobs - 1)
            finished_at = monotonic()
        return TranscriptionResult(
            text=result.text,
            confidence=result.confidence,
            queued_ms=int(max(0.0, started_at - queued_at) * 1000),
            execution_ms=int(max(0.0, finished_at - started_at) * 1000),
            duration_ms=result.duration_ms,
            language=result.language,
        )
