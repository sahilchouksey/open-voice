"""
Parakeet v2 STT Engine for Open Voice Runtime

NVIDIA NeMo `nvidia/parakeet-tdt-0.6b-v2` (English-optimized)
with Silero VAD chunking for real-time transcription.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import numpy as np
import soundfile as sf
import torch

from open_voice_runtime.audio.types import AudioChunk
from open_voice_runtime.stt.contracts import (
    SttCapabilities,
    SttConfig,
    SttEvent,
    SttEventKind,
    SttFileRequest,
    SttFileResult,
)
from open_voice_runtime.stt.engine import BaseSttEngine, BaseSttStream

# ── Configuration ─────────────────────────────────────────────────────────────
MODEL_NAME = "nvidia/parakeet-tdt-0.6b-v2"
_VAD_SR = 16000
_VAD_FRAME = 512
_SILENCE_THRESH = 0.35
_SILENCE_FRAMES = 12
_MIN_SPEECH_SAMPLES = 3200
_INTERIM_INTERVAL = 1.7


# ── Lazy model cache ──────────────────────────────────────────────────────────
_model_cache: dict[str, object] = {}
_model_device: dict[str, str] = {}
_model_cache_lock = threading.Lock()
_model_infer_locks: dict[str, threading.Lock] = {}
_vad_model = None
_vad_lock = threading.Lock()


def extract_text(output) -> str:
    """Extract text from NeMo model output."""
    if output is None:
        return ""

    if isinstance(output, list):
        if not output:
            return ""
        first = output[0]
        if isinstance(first, str):
            return first.strip()
        text = getattr(first, "text", None)
        if isinstance(text, str):
            return text.strip()
        if isinstance(first, dict):
            for key in ("text", "pred_text", "transcript"):
                val = first.get(key)
                if isinstance(val, str):
                    return val.strip()

    text = getattr(output, "text", None)
    if isinstance(text, str):
        return text.strip()
    return str(output).strip()


def _load_model_instance(model_name: str, requested_device: str):
    """Load Parakeet model instance."""
    import nemo.collections.asr as nemo_asr

    device = requested_device.lower().strip()
    if device not in {"cuda", "cpu"}:
        device = "cuda"

    model = nemo_asr.models.ASRModel.from_pretrained(model_name=model_name)
    if device == "cuda" and torch.cuda.is_available():
        model = model.to("cuda")
    else:
        model = model.to("cpu")
        device = "cpu"

    model.eval()
    return model, device


def get_parakeet_model(model_name: str, preferred_device: str):
    """Get cached Parakeet model (lazy loading)."""
    with _model_cache_lock:
        if model_name in _model_cache:
            return _model_cache[model_name], _model_device[model_name]

        requested = preferred_device.lower().strip()
        print(f"[parakeet] loading {model_name} on {requested} ...")

        model = None
        try:
            model, loaded_device = _load_model_instance(model_name, requested)
        except Exception as e:
            if requested == "cuda" and _is_cuda_issue(e):
                print(f"[parakeet] CUDA load failed ({e}). Falling back to CPU.")
                with contextlib.suppress(Exception):
                    del model
                import gc

                gc.collect()
                with contextlib.suppress(Exception):
                    torch.cuda.empty_cache()
                model, loaded_device = _load_model_instance(model_name, "cpu")
            else:
                raise

        _model_cache[model_name] = model
        _model_infer_locks[model_name] = threading.Lock()
        _model_device[model_name] = loaded_device
        print(f"[parakeet] model loaded: {model_name} ({loaded_device})")
        return model, loaded_device


def _is_cuda_issue(err: Exception) -> bool:
    """Check if error is CUDA-related."""
    msg = str(err).lower()
    return (
        "cuda out of memory" in msg
        or "out of memory" in msg
        and "cuda" in msg
        or "driver" in msg
        and "cuda" in msg
        or "cuda error" in msg
    )


def transcribe_with_parakeet(model_name: str, model, audio_np: np.ndarray) -> str:
    """Transcribe audio using Parakeet model."""
    lock = _model_infer_locks[model_name]
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
        sf.write(tmp.name, audio_np, 16000, subtype="PCM_16")
        with lock:
            output = model.transcribe([tmp.name], batch_size=1)
    return extract_text(output)


def get_vad_base():
    """Get Silero VAD model."""
    global _vad_model
    if _vad_model is None:
        with _vad_lock:
            if _vad_model is None:
                print("[vad] loading silero-vad for parakeet engine ...")
                from silero_vad import load_silero_vad

                _vad_model = load_silero_vad(onnx=False)
                print("[vad] loaded for parakeet engine")
    return _vad_model


# ── Stream Implementation ─────────────────────────────────────────────────────
@dataclass(slots=True)
class ParakeetStreamState:
    speech_buf: list[float]
    silence_count: int
    in_speech: bool
    line_counter: int
    last_interim: float
    vad: Any
    queue: asyncio.Queue[SttEvent | Exception | None]


class ParakeetSttStream(BaseSttStream):
    def __init__(self, model: Any, vad_model: Any, config: SttConfig) -> None:
        self._model = model
        self._config = config
        self._queue: asyncio.Queue[SttEvent | Exception | None] = asyncio.Queue()
        self._closed = False
        self._state = self._create_state(vad_model)

    def _create_state(self, vad_model: Any) -> ParakeetStreamState:
        import copy

        vad = copy.deepcopy(vad_model)
        vad.reset_states()
        return ParakeetStreamState(
            speech_buf=[],
            silence_count=0,
            in_speech=False,
            line_counter=0,
            last_interim=0.0,
            vad=vad,
            queue=self._queue,
        )

    async def push_audio(self, chunk: AudioChunk) -> None:
        """Push audio chunk for processing."""
        samples = chunk.samples_float()
        if not samples:
            return

        tensor = torch.from_numpy(np.array(samples, dtype=np.float32))

        # Pad to VAD frame size
        pad = (_VAD_FRAME - len(tensor) % _VAD_FRAME) % _VAD_FRAME
        if pad:
            tensor = torch.cat([tensor, torch.zeros(pad)])

        speech_prob = 0.0
        for i in range(0, len(tensor), _VAD_FRAME):
            frame = tensor[i : i + _VAD_FRAME]
            speech_prob = self._state.vad(frame.unsqueeze(0), _VAD_SR).item()

        if speech_prob >= _SILENCE_THRESH:
            self._state.speech_buf.extend(samples)
            self._state.silence_count = 0
            if not self._state.in_speech:
                self._state.in_speech = True
                self._state.last_interim = time.time()
            else:
                now = time.time()
                if now - self._state.last_interim >= _INTERIM_INTERVAL:
                    self._state.last_interim = now
                    await self._rolling_interim()
        elif self._state.in_speech:
            self._state.silence_count += 1
            self._state.speech_buf.extend(samples)
            if self._state.silence_count >= _SILENCE_FRAMES:
                self._state.in_speech = False
                self._state.silence_count = 0
                await self._flush_and_transcribe()

    async def _rolling_interim(self) -> None:
        """Emit partial transcript."""
        if len(self._state.speech_buf) < _MIN_SPEECH_SAMPLES:
            return

        audio_np = np.array(self._state.speech_buf, dtype=np.float32)
        self._state.line_counter += 1
        lid = self._state.line_counter

        try:
            text = await asyncio.to_thread(
                transcribe_with_parakeet, MODEL_NAME, self._model, audio_np
            )
        except Exception:
            return

        if text:
            await self._queue.put(
                SttEvent(
                    kind=SttEventKind.PARTIAL,
                    text=text,
                    sequence=lid,
                )
            )

    async def _flush_and_transcribe(self) -> None:
        """Flush speech buffer and emit final transcript."""
        if len(self._state.speech_buf) < _MIN_SPEECH_SAMPLES:
            self._state.speech_buf.clear()
            return

        audio_np = np.array(self._state.speech_buf, dtype=np.float32)
        self._state.speech_buf.clear()
        self._state.line_counter += 1
        lid = self._state.line_counter

        try:
            text = await asyncio.to_thread(
                transcribe_with_parakeet, MODEL_NAME, self._model, audio_np
            )
        except Exception as e:
            print(f"[parakeet] transcribe error: {e}")
            return

        if text:
            await self._queue.put(
                SttEvent(
                    kind=SttEventKind.FINAL,
                    text=text,
                    sequence=lid,
                )
            )

    async def flush(self) -> None:
        """Flush any remaining audio."""
        if self._state.speech_buf:
            await self._flush_and_transcribe()

    async def close(self) -> None:
        """Close the stream."""
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)

    async def events(self) -> AsyncIterator[SttEvent]:
        """Yield STT events."""
        while True:
            item = await self._queue.get()
            if item is None:
                return
            if isinstance(item, Exception):
                raise item
            yield item

    async def drain(self, wait_seconds: float = 0.0) -> list[SttEvent]:
        """Drain available events."""
        items: list[SttEvent] = []

        if wait_seconds > 0.0:
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=wait_seconds)
                if isinstance(item, Exception):
                    raise item
                if item is not None:
                    items.append(item)
            except TimeoutError:
                return []

        while True:
            try:
                item = self._queue.get_nowait()
                if isinstance(item, Exception):
                    raise item
                if item is not None:
                    items.append(item)
            except asyncio.QueueEmpty:
                break

        return items


# ── Engine Implementation ─────────────────────────────────────────────────────
class ParakeetSttEngine(BaseSttEngine):
    id = "parakeet-v2"
    label = "Parakeet v2 (English)"
    capabilities = SttCapabilities(
        streaming=True,
        batch=True,
        partial_results=True,
        languages=("en",),
    )

    def __init__(self, device: str = "cuda") -> None:
        self._device = device
        self._model: Any | None = None
        self._vad_model: Any | None = None

    async def load(self) -> None:
        """Load Parakeet model and VAD."""
        print(f"[parakeet-v2] loading model {MODEL_NAME} ...")
        self._model, loaded_device = await asyncio.to_thread(
            get_parakeet_model, MODEL_NAME, self._device
        )
        print(f"[parakeet-v2] model loaded on {loaded_device}")

        self._vad_model = await asyncio.to_thread(get_vad_base)
        print("[parakeet-v2] ready")

    async def close(self) -> None:
        """Cleanup."""
        self._model = None
        self._vad_model = None

    async def create_stream(self, config: SttConfig) -> BaseSttStream:
        """Create a new transcription stream."""
        if self._model is None or self._vad_model is None:
            raise RuntimeError("Engine not loaded. Call load() first.")
        return ParakeetSttStream(self._model, self._vad_model, config)

    async def transcribe_file(self, request: SttFileRequest) -> SttFileResult:
        """Batch transcription of audio file."""
        if self._model is None:
            raise RuntimeError("Engine not loaded. Call load() first.")

        import soundfile as sf
        import tempfile
        import io

        audio_np = np.frombuffer(request.audio, dtype=np.int16).astype(np.float32) / 32768.0

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            sf.write(tmp.name, audio_np, 16000, subtype="PCM_16")
            text = await asyncio.to_thread(
                transcribe_with_parakeet, MODEL_NAME, self._model, audio_np
            )

        return SttFileResult(text=text, confidence=None, language="en")
