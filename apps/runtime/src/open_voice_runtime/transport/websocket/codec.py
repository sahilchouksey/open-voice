from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from open_voice_runtime.audio.types import AudioChunk, AudioFormat
from open_voice_runtime.conversation.events import ConversationEvent
from open_voice_runtime.core.errors import TransportProtocolError
from open_voice_runtime.session.models import EngineSelection
from open_voice_runtime.transport.websocket.protocol import (
    AudioAppendMessage,
    AudioChunkPayload,
    AudioCommitMessage,
    AudioTransport,
    ClientMessage,
    ClientMessageType,
    ConfigUpdateMessage,
    ConversationInterruptMessage,
    EngineSelectMessage,
    SessionCloseMessage,
    SessionStartMessage,
)


def parse_client_message(payload: dict[str, Any]) -> ClientMessage:
    try:
        kind = ClientMessageType(payload["type"])
    except KeyError as exc:
        raise TransportProtocolError(
            "Realtime client message must include a 'type' field."
        ) from exc
    except ValueError as exc:
        raise TransportProtocolError(
            f"Unsupported realtime client message type: {payload['type']!r}."
        ) from exc

    if kind is ClientMessageType.SESSION_START:
        return SessionStartMessage(
            session_id=_as_optional_str(payload.get("session_id")),
            engine_selection=_parse_engine_selection(payload.get("engine_selection")),
            metadata=_as_dict(payload.get("metadata")),
        )

    if kind is ClientMessageType.AUDIO_APPEND:
        return AudioAppendMessage(
            session_id=_require_str(payload, "session_id"),
            chunk=_parse_audio_chunk(_require_dict(payload, "chunk")),
        )

    if kind is ClientMessageType.AUDIO_COMMIT:
        return AudioCommitMessage(
            session_id=_require_str(payload, "session_id"),
            sequence=_as_optional_int(payload.get("sequence")),
        )

    if kind is ClientMessageType.CONVERSATION_INTERRUPT:
        return ConversationInterruptMessage(
            session_id=_require_str(payload, "session_id"),
            reason=_as_optional_str(payload.get("reason")),
        )

    if kind is ClientMessageType.ENGINE_SELECT:
        return EngineSelectMessage(
            session_id=_require_str(payload, "session_id"),
            engine_selection=_parse_engine_selection(payload.get("engine_selection")),
        )

    if kind is ClientMessageType.CONFIG_UPDATE:
        return ConfigUpdateMessage(
            session_id=_require_str(payload, "session_id"),
            config=_require_dict(payload, "config"),
        )

    return SessionCloseMessage(session_id=_require_str(payload, "session_id"))


def serialize_conversation_event(event: ConversationEvent) -> dict[str, Any]:
    return _serialize_value(event)


def _parse_engine_selection(value: Any) -> EngineSelection:
    data = _as_dict(value)
    return EngineSelection(
        stt=_as_optional_str(data.get("stt")),
        router=_as_optional_str(data.get("router")),
        llm=_as_optional_str(data.get("llm")),
        tts=_as_optional_str(data.get("tts")),
    )


def _parse_audio_chunk(value: dict[str, Any]) -> AudioChunkPayload:
    return AudioChunkPayload(
        chunk_id=_require_str(value, "chunk_id"),
        sequence=_require_int(value, "sequence"),
        encoding=_require_str(value, "encoding"),
        sample_rate_hz=_require_int(value, "sample_rate_hz"),
        channels=_require_int(value, "channels"),
        duration_ms=_as_optional_float(value.get("duration_ms")),
        transport=AudioTransport(value.get("transport", AudioTransport.INLINE_BASE64.value)),
        data_base64=_as_optional_str(value.get("data_base64")),
    )


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, AudioFormat):
        return {
            "sample_rate_hz": value.sample_rate_hz,
            "channels": value.channels,
            "encoding": value.encoding.value,
        }
    if isinstance(value, AudioChunk):
        return {
            "encoding": value.format.encoding.value,
            "sample_rate_hz": value.format.sample_rate_hz,
            "channels": value.format.channels,
            "sequence": value.sequence,
            "duration_ms": value.duration_ms,
        }
    if is_dataclass(value):
        return {field.name: _serialize_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    return value


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if isinstance(value, dict):
        return value
    raise TransportProtocolError(f"Realtime client message field '{key}' must be an object.")


def _as_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    raise TransportProtocolError("Expected an object in realtime client message payload.")


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str):
        return value
    raise TransportProtocolError(f"Realtime client message field '{key}' must be a string.")


def _as_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise TransportProtocolError("Expected a string or null in realtime client message payload.")


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, int):
        return value
    raise TransportProtocolError(f"Realtime client message field '{key}' must be an integer.")


def _as_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    raise TransportProtocolError("Expected an integer or null in realtime client message payload.")


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    raise TransportProtocolError("Expected a number or null in realtime client message payload.")
