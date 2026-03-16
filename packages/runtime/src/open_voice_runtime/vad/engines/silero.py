from __future__ import annotations

import asyncio
import importlib.util
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch

from open_voice_runtime.audio.preprocessing import audio_chunk_to_mono_floats
from open_voice_runtime.audio.types import AudioChunk
from open_voice_runtime.vad.contracts import (
    VadCapabilities,
    VadConfig,
    VadEvent,
    VadEventKind,
    VadResult,
)
from open_voice_runtime.vad.engine import BaseVadEngine, BaseVadStream


def silero_vad_available() -> bool:
    return importlib.util.find_spec("silero_vad") is not None


@dataclass(slots=True)
class _SileroState:
    sequence: int = 0


class SileroVadStream(BaseVadStream):
    def __init__(self, model: Any, config: VadConfig) -> None:
        self._model = model
        self._config = config
        self._iterator = self._create_iterator()
        self._state = _SileroState()
        self._closed = False
        self._speaking = False
        self._buffer = np.empty(0, dtype=np.float32)

    async def push_audio(self, chunk: AudioChunk) -> VadResult:
        if self._closed:
            return VadResult()

        mono = audio_chunk_to_mono_floats(chunk)
        if chunk.format.sample_rate_hz != 16000:
            mono = _resample_linear(mono, chunk.format.sample_rate_hz, 16000)
        events: list[VadEvent] = []
        # Convert list to numpy array
        mono_array = np.array(mono, dtype=np.float32)
        self._buffer = np.concatenate([self._buffer, mono_array])
        frame_size = self._config.chunk_size

        while len(self._buffer) >= frame_size:
            frame = self._buffer[:frame_size]
            self._buffer = self._buffer[frame_size:]
            tensor = torch.from_numpy(frame)

            event = await asyncio.to_thread(self._iterator, tensor)
            if event is not None:
                if "start" in event:
                    self._speaking = True
                    events.append(
                        VadEvent(
                            kind=VadEventKind.START_OF_SPEECH,
                            sequence=self._state.sequence,
                            timestamp_ms=(float(event["start"]) / 16000.0) * 1000.0,
                            speaking=True,
                            chunk=chunk,
                        )
                    )
                    self._state.sequence += 1
                if "end" in event:
                    self._speaking = False
                    events.append(
                        VadEvent(
                            kind=VadEventKind.END_OF_SPEECH,
                            sequence=self._state.sequence,
                            timestamp_ms=(float(event["end"]) / 16000.0) * 1000.0,
                            speaking=False,
                            chunk=chunk,
                        )
                    )
                    self._state.sequence += 1

            probability = await asyncio.to_thread(self._confidence, tensor)
            events.append(
                VadEvent(
                    kind=VadEventKind.INFERENCE,
                    sequence=self._state.sequence,
                    timestamp_ms=0.0,
                    probability=probability,
                    speaking=self._speaking,
                )
            )
        return VadResult(events=events)

    async def flush(self) -> VadResult:
        return VadResult()

    async def close(self) -> None:
        self._closed = True
        self._iterator.reset_states()

    def _create_iterator(self) -> Any:
        from silero_vad import VADIterator

        return VADIterator(
            self._model,
            threshold=self._config.activation_threshold,
            sampling_rate=16000,
            min_silence_duration_ms=self._config.min_silence_duration_ms,
        )

    def _confidence(self, tensor: torch.Tensor) -> float:
        frame = tensor
        if frame.numel() < self._config.chunk_size:
            frame = torch.nn.functional.pad(frame, (0, self._config.chunk_size - frame.numel()))
        elif frame.numel() > self._config.chunk_size:
            frame = frame[: self._config.chunk_size]
        return float(self._model(frame, 16000).item())


class SileroVadEngine(BaseVadEngine):
    id = "silero"
    label = "Silero VAD"
    capabilities = VadCapabilities(streaming=True, sample_rates_hz=(16000,))

    def __init__(self) -> None:
        self._model: Any | None = None
        self.available = silero_vad_available()
        self.status = "ready" if self.available else "missing_dependency"

    async def load(self) -> None:
        if self._model is not None:
            return
        if not silero_vad_available():
            raise RuntimeError("silero-vad is not installed")
        from silero_vad import load_silero_vad

        self._model = await asyncio.to_thread(load_silero_vad, onnx=True)

    async def close(self) -> None:
        self._model = None

    async def create_stream(self, config: VadConfig) -> BaseVadStream:
        await self.load()
        assert self._model is not None
        return SileroVadStream(self._model, config)


def _resample_linear(mono: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate:
        return mono.astype(np.float32, copy=False)
    if len(mono) == 0:
        return mono.astype(np.float32, copy=False)
    duration = len(mono) / float(source_rate)
    new_size = max(1, int(round(duration * target_rate)))
    x_old = np.linspace(0.0, 1.0, num=len(mono), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=new_size, endpoint=False)
    return np.interp(x_new, x_old, mono).astype(np.float32, copy=False)
