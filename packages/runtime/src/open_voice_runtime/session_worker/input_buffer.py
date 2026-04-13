from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from open_voice_runtime.audio.types import AudioChunk


@dataclass(slots=True)
class BufferedUtterance:
    session_id: str
    utterance_id: str
    sample_rate_hz: int
    channels: int
    encoding: str
    pcm_bytes: bytes
    started_at_monotonic: float
    ended_at_monotonic: float
    duration_ms: float
    chunk_count: int
    explicit_commit: bool


class InputBuffer:
    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self.reset()

    def append(self, chunk: AudioChunk) -> None:
        if self._started_at is None:
            self._started_at = monotonic()
        self._chunks.append(chunk)
        self._last_audio_at = monotonic()

    def has_audio(self) -> bool:
        return bool(self._chunks)

    def chunk_count(self) -> int:
        return len(self._chunks)

    def snapshot(self, *, utterance_id: str, explicit_commit: bool) -> BufferedUtterance | None:
        if not self._chunks:
            return None
        first = self._chunks[0]
        data = b"".join(chunk.data for chunk in self._chunks)
        duration_ms = sum(float(chunk.duration_ms or 0.0) for chunk in self._chunks)
        started_at = self._started_at or monotonic()
        ended_at = self._last_audio_at or monotonic()
        return BufferedUtterance(
            session_id=self._session_id,
            utterance_id=utterance_id,
            sample_rate_hz=first.format.sample_rate_hz,
            channels=first.format.channels,
            encoding=first.format.encoding.value,
            pcm_bytes=data,
            started_at_monotonic=started_at,
            ended_at_monotonic=ended_at,
            duration_ms=duration_ms,
            chunk_count=len(self._chunks),
            explicit_commit=explicit_commit,
        )

    def reset(self) -> None:
        self._chunks: list[AudioChunk] = []
        self._started_at: float | None = None
        self._last_audio_at: float | None = None
