from __future__ import annotations

import importlib.util
import importlib
from dataclasses import dataclass
import os
from typing import Any

from open_voice_runtime.core.errors import ErrorCode, OpenVoiceError


def moonshine_voice_available() -> bool:
    return importlib.util.find_spec("moonshine_voice") is not None


@dataclass(frozen=True, slots=True)
class MoonshineConfig:
    language: str = "en"
    model_arch: str = "MEDIUM_STREAMING"
    update_interval: float = 0.03


class MoonshineVoiceClient:
    def __init__(self, config: MoonshineConfig | None = None) -> None:
        self._config = config or MoonshineConfig(
            update_interval=_moonshine_update_interval_seconds()
        )

    def create_transcriber(self, *, language: str | None = None):
        moonshine = self._module()
        api = self._api_module()

        model_arch = getattr(api.ModelArch, self._config.model_arch)
        model_path, resolved_arch = moonshine.get_model_for_language(
            wanted_language=language or self._config.language,
            wanted_model_arch=model_arch,
        )
        return moonshine.Transcriber(
            model_path=str(model_path),
            model_arch=resolved_arch,
            update_interval=self._config.update_interval,
        )

    def listener_base(self):
        return self._module().TranscriptEventListener

    def line_started_type(self):
        return self._transcriber_module().LineStarted

    def line_text_changed_type(self):
        return self._transcriber_module().LineTextChanged

    def line_completed_type(self):
        return self._transcriber_module().LineCompleted

    def _module(self) -> Any:
        if not moonshine_voice_available():
            raise OpenVoiceError(
                code=ErrorCode.ENGINE_UNAVAILABLE,
                message="moonshine-voice is not installed in the active runtime environment.",
                retryable=False,
                details={"dependency": "moonshine-voice==0.0.49"},
            )

        import moonshine_voice

        return moonshine_voice

    def _api_module(self) -> Any:
        return importlib.import_module("moonshine_voice.moonshine_api")

    def _transcriber_module(self) -> Any:
        return importlib.import_module("moonshine_voice.transcriber")


def _moonshine_update_interval_seconds() -> float:
    raw = os.getenv("OPEN_VOICE_MOONSHINE_UPDATE_INTERVAL_MS")
    if raw is None:
        return 0.03
    try:
        value_ms = float(raw)
    except (TypeError, ValueError):
        return 0.03
    value_ms = max(10.0, min(120.0, value_ms))
    return value_ms / 1000.0
