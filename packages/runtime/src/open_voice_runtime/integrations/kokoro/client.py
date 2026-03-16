from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from numbers import Integral, Real
from pathlib import Path
from typing import Any

from open_voice_runtime.core.errors import ErrorCode, OpenVoiceError

DEFAULT_KOKORO_SAMPLE_RATE_HZ = 24_000
DEFAULT_KOKORO_VOICE = "af_bella"
DEFAULT_KOKORO_MODEL_FILENAME = "kokoro-v1.0.onnx"
DEFAULT_KOKORO_VOICES_FILENAME = "voices-v1.0.bin"
DEFAULT_KOKORO_MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.0/kokoro-v1.0.onnx"
)
DEFAULT_KOKORO_VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
)

KOKORO_VOICE_IDS = (
    "af_alloy",
    "af_aoede",
    "af_bella",
    "af_heart",
    "af_jessica",
    "af_kore",
    "af_nicole",
    "af_nova",
    "af_river",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_echo",
    "am_eric",
    "am_fenrir",
    "am_liam",
    "am_michael",
    "am_onyx",
    "am_puck",
    "am_santa",
    "bf_alice",
    "bf_emma",
    "bf_isabella",
    "bf_lily",
    "bm_daniel",
    "bm_fable",
    "bm_george",
    "bm_lewis",
    "ef_dora",
    "em_alex",
    "em_santa",
    "ff_siwis",
    "hf_alpha",
    "hf_beta",
    "hm_omega",
    "hm_psi",
    "if_sara",
    "im_nicola",
    "jf_alpha",
    "jf_gongitsune",
    "jf_nezumi",
    "jf_tebukuro",
    "jm_kumo",
    "pf_dora",
    "pm_alex",
    "pm_santa",
    "zf_xiaobei",
    "zf_xiaoni",
    "zf_xiaoxiao",
    "zf_xiaoyi",
    "zm_yunjian",
    "zm_yunxi",
    "zm_yunxia",
    "zm_yunyang",
)


@dataclass(frozen=True, slots=True)
class _VoiceProfile:
    language: str
    synthesis_language: str


_VOICE_PREFIX_TO_PROFILE = {
    "af": _VoiceProfile(language="en-US", synthesis_language="en-us"),
    "am": _VoiceProfile(language="en-US", synthesis_language="en-us"),
    "bf": _VoiceProfile(language="en-GB", synthesis_language="en-gb"),
    "bm": _VoiceProfile(language="en-GB", synthesis_language="en-gb"),
    "ef": _VoiceProfile(language="es-ES", synthesis_language="es"),
    "em": _VoiceProfile(language="es-ES", synthesis_language="es"),
    "ff": _VoiceProfile(language="fr-FR", synthesis_language="fr-fr"),
    "hf": _VoiceProfile(language="hi-IN", synthesis_language="hi"),
    "hm": _VoiceProfile(language="hi-IN", synthesis_language="hi"),
    "if": _VoiceProfile(language="it-IT", synthesis_language="it"),
    "im": _VoiceProfile(language="it-IT", synthesis_language="it"),
    "jf": _VoiceProfile(language="ja-JP", synthesis_language="ja"),
    "jm": _VoiceProfile(language="ja-JP", synthesis_language="ja"),
    "pf": _VoiceProfile(language="pt-BR", synthesis_language="pt-br"),
    "pm": _VoiceProfile(language="pt-BR", synthesis_language="pt-br"),
    "zf": _VoiceProfile(language="zh-CN", synthesis_language="cmn"),
    "zm": _VoiceProfile(language="zh-CN", synthesis_language="cmn"),
}

_LANGUAGE_TO_SYNTHESIS_LANGUAGE = {
    "en": "en-us",
    "en-us": "en-us",
    "en-gb": "en-gb",
    "es": "es",
    "es-es": "es",
    "fr": "fr-fr",
    "fr-fr": "fr-fr",
    "hi": "hi",
    "hi-in": "hi",
    "it": "it",
    "it-it": "it",
    "ja": "ja",
    "ja-jp": "ja",
    "pt": "pt-br",
    "pt-br": "pt-br",
    "zh": "cmn",
    "zh-cn": "cmn",
    "cmn": "cmn",
}


def kokoro_backend_available() -> bool:
    return importlib.util.find_spec("kokoro_onnx") is not None


def kokoro_voice_language(voice_id: str) -> str | None:
    profile = _VOICE_PREFIX_TO_PROFILE.get(_voice_prefix(voice_id))
    if profile is None:
        return None
    return profile.language


def _default_provider_list() -> tuple[str, ...]:
    configured = os.getenv("OPEN_VOICE_KOKORO_ONNX_PROVIDERS")
    if configured is not None:
        values = tuple(part for part in (item.strip() for item in configured.split(",")) if part)
        if values:
            return values

    provider = os.getenv("OPEN_VOICE_KOKORO_ONNX_PROVIDER") or os.getenv("ONNX_PROVIDER")
    if provider is None:
        return ()
    stripped = provider.strip()
    if not stripped:
        return ()
    return (stripped,)


@dataclass(frozen=True, slots=True)
class KokoroConfig:
    asset_dir: str | None = field(
        default_factory=lambda: _env_str("OPEN_VOICE_KOKORO_ONNX_ASSET_DIR")
    )
    model_path: str | None = field(
        default_factory=lambda: _env_str("OPEN_VOICE_KOKORO_ONNX_MODEL_PATH")
    )
    voices_path: str | None = field(
        default_factory=lambda: _env_str("OPEN_VOICE_KOKORO_ONNX_VOICES_PATH")
    )
    vocab_path: str | None = field(
        default_factory=lambda: _env_str("OPEN_VOICE_KOKORO_ONNX_VOCAB_PATH")
    )
    espeak_data_path: str | None = field(
        default_factory=lambda: _env_str("OPEN_VOICE_KOKORO_ONNX_ESPEAK_DATA_PATH")
    )
    espeak_lib_path: str | None = field(
        default_factory=lambda: _env_str("OPEN_VOICE_KOKORO_ONNX_ESPEAK_LIB_PATH")
    )
    providers: tuple[str, ...] = field(default_factory=_default_provider_list)
    intra_op_num_threads: int | None = field(
        default_factory=lambda: _env_int("OPEN_VOICE_KOKORO_ONNX_INTRA_OP_THREADS")
    )
    default_voice: str = DEFAULT_KOKORO_VOICE
    default_speed: float = 1.1
    sample_rate_hz: int = DEFAULT_KOKORO_SAMPLE_RATE_HZ


@dataclass(frozen=True, slots=True)
class KokoroAudioSegment:
    text: str
    audio: bytes
    sample_rate_hz: int
    duration_ms: float | None = None
    voice_id: str | None = None


@dataclass(frozen=True, slots=True)
class _KokoroAssets:
    model_path: Path | None
    voices_path: Path | None
    vocab_path: Path | None


@dataclass(frozen=True, slots=True)
class _KokoroRequest:
    text: str
    voice_id: str
    language: str
    speed: float
    is_phonemes: bool
    trim: bool


class KokoroClient:
    def __init__(self, config: KokoroConfig | None = None) -> None:
        self._config = config or KokoroConfig()
        self._instance: Any | None = None
        self._load_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self.status == "ready"

    @property
    def status(self) -> str:
        if not kokoro_backend_available():
            return "missing_dependency"
        assets = _resolve_assets(self._config)
        if not _assets_ready(assets):
            return "missing_assets"
        return "ready"

    async def load(self) -> None:
        await self._kokoro_instance()

    async def close(self) -> None:
        self._instance = None

    async def stream_synthesis(
        self,
        *,
        text: str,
        voice_id: str | None = None,
        language: str | None = None,
        speed: float | None = None,
        is_phonemes: bool = False,
        trim: bool = True,
    ) -> AsyncIterator[KokoroAudioSegment]:
        request = _resolve_request(
            text=text,
            voice_id=voice_id,
            language=language,
            speed=speed,
            is_phonemes=is_phonemes,
            trim=trim,
            config=self._config,
        )
        kokoro = await self._kokoro_instance()

        try:
            async for audio_payload, sample_rate_hz in kokoro.create_stream(
                request.text,
                voice=request.voice_id,
                speed=request.speed,
                lang=request.language,
                is_phonemes=request.is_phonemes,
                trim=request.trim,
            ):
                audio = _pcm_s16le_bytes(audio_payload)
                if not audio:
                    continue
                sample_rate = (
                    sample_rate_hz
                    if isinstance(sample_rate_hz, int) and sample_rate_hz > 0
                    else self._config.sample_rate_hz
                )
                yield KokoroAudioSegment(
                    text=request.text,
                    audio=audio,
                    sample_rate_hz=sample_rate,
                    duration_ms=_pcm_duration_ms(audio, sample_rate),
                    voice_id=request.voice_id,
                )
        except Exception as exc:
            raise _provider_error(exc) from exc

    async def _kokoro_instance(self) -> Any:
        if self._instance is not None:
            return self._instance

        async with self._load_lock:
            if self._instance is not None:
                return self._instance
            self._instance = await asyncio.to_thread(self._create_instance)
            return self._instance

    def _create_instance(self) -> Any:
        module = self._module()
        onnxruntime = self._onnxruntime_module()
        assets = _resolve_assets(self._config)
        if not _assets_ready(assets):
            raise _missing_assets_error(self._config, assets)

        providers = _session_providers(self._config)
        session_options = onnxruntime.SessionOptions()
        if self._config.intra_op_num_threads is not None:
            session_options.intra_op_num_threads = self._config.intra_op_num_threads

        session = onnxruntime.InferenceSession(
            str(assets.model_path),
            providers=providers,
            sess_options=session_options,
        )

        espeak_config = _espeak_config(module, self._config)
        vocab_config = str(assets.vocab_path) if assets.vocab_path is not None else None
        return module.Kokoro.from_session(
            session,
            str(assets.voices_path),
            espeak_config=espeak_config,
            vocab_config=vocab_config,
        )

    def _module(self) -> Any:
        if not kokoro_backend_available():
            raise OpenVoiceError(
                code=ErrorCode.ENGINE_UNAVAILABLE,
                message="kokoro-onnx is not installed in the active runtime environment.",
                retryable=False,
                details={"dependency": "kokoro-onnx>=0.5.0"},
            )
        return importlib.import_module("kokoro_onnx")

    def _onnxruntime_module(self) -> Any:
        try:
            return importlib.import_module("onnxruntime")
        except ImportError as exc:
            raise OpenVoiceError(
                code=ErrorCode.ENGINE_UNAVAILABLE,
                message="onnxruntime is not available for kokoro-onnx.",
                retryable=False,
                details={"dependency": "onnxruntime"},
            ) from exc


def _resolve_request(
    *,
    text: str,
    voice_id: str | None,
    language: str | None,
    speed: float | None,
    is_phonemes: bool,
    trim: bool,
    config: KokoroConfig,
) -> _KokoroRequest:
    resolved_voice = voice_id or config.default_voice
    return _KokoroRequest(
        text=text,
        voice_id=resolved_voice,
        language=_resolve_synthesis_language(language, resolved_voice),
        speed=speed if speed is not None else config.default_speed,
        is_phonemes=is_phonemes,
        trim=trim,
    )


def _resolve_synthesis_language(language: str | None, voice_id: str) -> str:
    if language is not None:
        normalized = language.strip().lower().replace("_", "-")
        synthesis_language = _LANGUAGE_TO_SYNTHESIS_LANGUAGE.get(normalized)
        if synthesis_language is not None:
            return synthesis_language
        base_language = normalized.split("-", maxsplit=1)[0]
        synthesis_language = _LANGUAGE_TO_SYNTHESIS_LANGUAGE.get(base_language)
        if synthesis_language is not None:
            return synthesis_language

    profile = _VOICE_PREFIX_TO_PROFILE.get(_voice_prefix(voice_id))
    if profile is not None:
        return profile.synthesis_language
    return "en-us"


def _resolve_assets(config: KokoroConfig) -> _KokoroAssets:
    asset_dir = Path(config.asset_dir).expanduser() if config.asset_dir else None
    model_path = _path_or_default(config.model_path, asset_dir, DEFAULT_KOKORO_MODEL_FILENAME)
    voices_path = _path_or_default(config.voices_path, asset_dir, DEFAULT_KOKORO_VOICES_FILENAME)
    vocab_path = Path(config.vocab_path).expanduser() if config.vocab_path else None
    if vocab_path is None and asset_dir is not None:
        candidate = asset_dir / "config.json"
        if candidate.exists():
            vocab_path = candidate
    return _KokoroAssets(model_path=model_path, voices_path=voices_path, vocab_path=vocab_path)


def _path_or_default(
    explicit_path: str | None,
    asset_dir: Path | None,
    default_filename: str,
) -> Path | None:
    if explicit_path:
        return Path(explicit_path).expanduser()
    if asset_dir is not None:
        return asset_dir / default_filename
    return None


def _assets_ready(assets: _KokoroAssets) -> bool:
    return bool(
        assets.model_path is not None
        and assets.model_path.exists()
        and assets.voices_path is not None
        and assets.voices_path.exists()
        and (assets.vocab_path is None or assets.vocab_path.exists())
    )


def _missing_assets_error(config: KokoroConfig, assets: _KokoroAssets) -> OpenVoiceError:
    return OpenVoiceError(
        code=ErrorCode.ENGINE_UNAVAILABLE,
        message=(
            "kokoro-onnx model assets are not configured. Set "
            "OPEN_VOICE_KOKORO_ONNX_ASSET_DIR or explicit model/voices paths."
        ),
        retryable=False,
        details={
            "engine_id": "kokoro",
            "asset_dir": config.asset_dir,
            "model_path": str(assets.model_path) if assets.model_path is not None else None,
            "voices_path": str(assets.voices_path) if assets.voices_path is not None else None,
            "vocab_path": str(assets.vocab_path) if assets.vocab_path is not None else None,
            "model_url": DEFAULT_KOKORO_MODEL_URL,
            "voices_url": DEFAULT_KOKORO_VOICES_URL,
        },
    )


def _espeak_config(module: Any, config: KokoroConfig) -> Any | None:
    if config.espeak_data_path is None and config.espeak_lib_path is None:
        return None

    espeak_type = getattr(module, "EspeakConfig", None)
    if espeak_type is None:
        return None
    return espeak_type(lib_path=config.espeak_lib_path, data_path=config.espeak_data_path)


def _session_providers(config: KokoroConfig) -> list[str]:
    if not config.providers:
        return ["CPUExecutionProvider"]

    providers = list(config.providers)
    if "CPUExecutionProvider" not in providers:
        providers.append("CPUExecutionProvider")
    return providers


def _voice_prefix(voice_id: str) -> str:
    return voice_id.split("_", maxsplit=1)[0].lower()


def _env_str(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _env_int(name: str) -> int | None:
    value = _env_str(name)
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _pcm_s16le_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()

    dimensions = getattr(value, "ndim", None)
    if isinstance(dimensions, int) and dimensions > 1 and hasattr(value, "reshape"):
        value = value.reshape(-1)

    dtype = getattr(value, "dtype", None)
    dtype_kind = getattr(dtype, "kind", None)
    if dtype_kind in {"i", "u"} and hasattr(value, "astype") and hasattr(value, "tobytes"):
        return bytes(value.astype("<i2", copy=False).tobytes())

    if hasattr(value, "clip") and hasattr(value, "astype") and hasattr(value, "tobytes"):
        clipped = value.clip(-1.0, 1.0)
        return bytes((clipped * 32767.0).astype("<i2", copy=False).tobytes())

    if isinstance(value, Iterable) and not isinstance(value, str):
        data = bytearray()
        for sample in value:
            data.extend(_pcm_sample_bytes(sample))
        return bytes(data)

    raise TypeError("kokoro-onnx returned an unsupported audio payload type.")


def _pcm_sample_bytes(sample: Any) -> bytes:
    if isinstance(sample, bool):
        raise TypeError("kokoro-onnx returned a non-audio sample value.")
    if isinstance(sample, Integral):
        value = max(-32768, min(32767, int(sample)))
        return value.to_bytes(2, byteorder="little", signed=True)
    if isinstance(sample, Real):
        clipped = max(-1.0, min(1.0, float(sample)))
        value = int(clipped * 32767.0)
        return value.to_bytes(2, byteorder="little", signed=True)
    raise TypeError("kokoro-onnx returned a non-numeric audio sample.")


def _pcm_duration_ms(audio: bytes, sample_rate_hz: int) -> float | None:
    if sample_rate_hz <= 0:
        return None
    sample_count = len(audio) / 2
    return (sample_count / sample_rate_hz) * 1000.0


def _provider_error(exc: Exception) -> OpenVoiceError:
    return OpenVoiceError(
        code=ErrorCode.PROVIDER_ERROR,
        message=f"Kokoro synthesis failed: {exc}",
        retryable=False,
        details={"engine_id": "kokoro"},
    )
