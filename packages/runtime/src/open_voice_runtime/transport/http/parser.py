from __future__ import annotations

from typing import Any

from open_voice_runtime.app.config import normalize_runtime_config_payload
from open_voice_runtime.core.errors import TransportProtocolError
from open_voice_runtime.session.models import EngineSelection, SessionCreateRequest


def parse_session_create_request(payload: dict[str, Any] | None) -> SessionCreateRequest:
    data = payload or {}
    if not isinstance(data, dict):
        raise TransportProtocolError("Session create request body must be an object.")

    metadata = _parse_metadata(data.get("metadata"))
    runtime_config = _parse_runtime_config(data.get("runtime_config"))
    if runtime_config:
        metadata = dict(metadata)
        metadata["runtime_config"] = runtime_config

    return SessionCreateRequest(
        engine_selection=_parse_engine_selection(data.get("engine_selection")),
        metadata=metadata,
    )


def _parse_engine_selection(value: Any) -> EngineSelection:
    if value is None:
        return EngineSelection()
    if not isinstance(value, dict):
        raise TransportProtocolError("Session create field 'engine_selection' must be an object.")
    return EngineSelection(
        stt=_optional_string(value.get("stt")),
        router=_optional_string(value.get("router")),
        llm=_optional_string(value.get("llm")),
        tts=_optional_string(value.get("tts")),
    )


def _parse_metadata(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise TransportProtocolError("Session create field 'metadata' must be an object.")


def _parse_runtime_config(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TransportProtocolError("Session create field 'runtime_config' must be an object.")
    try:
        return normalize_runtime_config_payload(value)
    except TypeError as exc:
        raise TransportProtocolError(str(exc)) from exc


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise TransportProtocolError("Expected a string or null in session create request.")
