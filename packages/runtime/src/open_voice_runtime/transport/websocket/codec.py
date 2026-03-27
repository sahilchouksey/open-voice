from __future__ import annotations

from typing import Any

from open_voice_runtime.app.config import normalize_runtime_config_payload
from open_voice_runtime.conversation.events import ConversationEvent
from open_voice_runtime.core.errors import TransportProtocolError
from open_voice_runtime.core.serialization import to_json_value
from open_voice_runtime.session.models import EngineSelection
from open_voice_runtime.transport.websocket.protocol import (
    AgentGenerateReplyMessage,
    AgentSayMessage,
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
    UserTurnCommitMessage,
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
            config=_parse_runtime_config(payload.get("config")),
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
            client_turn_id=_as_optional_str(payload.get("client_turn_id")),
        )

    if kind is ClientMessageType.USER_TURN_COMMIT:
        return UserTurnCommitMessage(
            session_id=_require_str(payload, "session_id"),
            sequence=_as_optional_int(payload.get("sequence")),
            client_turn_id=_as_optional_str(payload.get("client_turn_id")),
        )

    if kind is ClientMessageType.AGENT_SAY:
        return AgentSayMessage(
            session_id=_require_str(payload, "session_id"),
            text=_require_str(payload, "text"),
        )

    if kind is ClientMessageType.AGENT_GENERATE_REPLY:
        return AgentGenerateReplyMessage(
            session_id=_require_str(payload, "session_id"),
            user_text=_require_str(payload, "user_text"),
            instructions=_as_optional_str(payload.get("instructions")),
            allow_interruptions=_as_optional_bool(payload.get("allow_interruptions")),
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
            config=_require_runtime_config(payload, "config"),
        )

    return SessionCloseMessage(session_id=_require_str(payload, "session_id"))


def serialize_conversation_event(event: ConversationEvent) -> dict[str, Any]:
    return to_json_value(event)


def _parse_engine_selection(value: Any) -> EngineSelection:
    data = _as_dict(value)
    return EngineSelection(
        stt=_as_optional_str(data.get("stt")),
        router=_as_optional_str(data.get("router")),
        llm=_as_optional_str(data.get("llm")),
        tts=_as_optional_str(data.get("tts")),
    )


def _parse_runtime_config(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TransportProtocolError("Realtime client message field 'config' must be an object.")
    try:
        return normalize_runtime_config_payload(value)
    except TypeError as exc:
        raise TransportProtocolError(str(exc)) from exc


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


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if isinstance(value, dict):
        return value
    raise TransportProtocolError(f"Realtime client message field '{key}' must be an object.")


def _require_runtime_config(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise TransportProtocolError(f"Realtime client message field '{key}' must be an object.")
    try:
        return normalize_runtime_config_payload(value)
    except TypeError as exc:
        raise TransportProtocolError(str(exc)) from exc


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


def _as_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise TransportProtocolError("Expected a boolean or null in realtime client message payload.")
