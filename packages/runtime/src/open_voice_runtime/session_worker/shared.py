from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from typing import Any

from open_voice_runtime.audio.types import AudioChunk, AudioEncoding, AudioFormat
from open_voice_runtime.conversation.events import ConversationEvent, VadStateEvent
from open_voice_runtime.core.errors import TransportProtocolError
from open_voice_runtime.session.models import EngineSelection, SessionState
from open_voice_runtime.transport.websocket.protocol import AudioAppendMessage
from open_voice_runtime.vad.contracts import VadEvent, VadConfig


ConversationEventEmitter = Callable[[ConversationEvent], Awaitable[None]]


def merge_engine_selection(current: EngineSelection, update: EngineSelection) -> EngineSelection:
    return EngineSelection(
        stt=update.stt or current.stt,
        router=update.router or current.router,
        llm=update.llm or current.llm,
        tts=update.tts or current.tts,
    )


def audio_chunk_from_message(message: AudioAppendMessage) -> AudioChunk:
    if message.chunk.transport.value == "binary-frame":
        raise TransportProtocolError("Binary-frame audio transport is not implemented yet.")
    if message.chunk.data_base64 is None:
        raise TransportProtocolError("Inline audio chunks must include 'data_base64'.")
    try:
        data = base64.b64decode(message.chunk.data_base64)
    except ValueError as exc:
        raise TransportProtocolError("Audio chunk 'data_base64' is not valid base64.") from exc
    return AudioChunk(
        data=data,
        format=AudioFormat(
            sample_rate_hz=message.chunk.sample_rate_hz,
            channels=message.chunk.channels,
            encoding=AudioEncoding(message.chunk.encoding),
        ),
        sequence=message.chunk.sequence,
        duration_ms=message.chunk.duration_ms,
    )


def merge_runtime_config_update(metadata: dict[str, Any], config: dict[str, Any]) -> None:
    if not config:
        return
    existing = metadata.get("runtime_config")
    runtime_config = dict(existing) if isinstance(existing, dict) else {}
    merge_nested_mapping(runtime_config, config)
    metadata["runtime_config"] = runtime_config


def merge_nested_mapping(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            nested_target = target[key]
            if isinstance(nested_target, dict):
                merge_nested_mapping(nested_target, value)
                continue
        target[key] = value


def conversation_events_from_vad(
    session_id: str,
    turn_id: str | None,
    vad_events: list[VadEvent],
) -> list[ConversationEvent]:
    return [
        VadStateEvent(
            session_id,
            kind=item.kind,
            sequence=item.sequence,
            turn_id=turn_id,
            speaking=item.speaking,
            probability=item.probability,
            timestamp_ms=item.timestamp_ms,
            speech_duration_ms=item.speech_duration_ms,
            silence_duration_ms=item.silence_duration_ms,
        )
        for item in vad_events
    ]


def set_generation_for_events(events: list[ConversationEvent], generation_id: str | None) -> None:
    if generation_id is None:
        return
    for event in events:
        event.generation_id = generation_id


async def emit_conversation_events(
    emit: ConversationEventEmitter,
    events: list[ConversationEvent],
) -> None:
    for event in events:
        await emit(event)


def safe_str(value: Any, default: str | None = None) -> str | None:
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return default


def vad_config(state: SessionState) -> VadConfig:
    runtime_config = state.metadata.get("runtime_config", {})
    turn = runtime_config.get("turn_detection", {}) if isinstance(runtime_config, dict) else {}
    return VadConfig(
        min_speech_duration_ms=int(turn.get("min_speech_duration_ms", 100)),
        min_silence_duration_ms=int(turn.get("min_silence_duration_ms", 600)),
        activation_threshold=float(turn.get("activation_threshold", 0.5)),
        chunk_size=int(turn.get("vad_chunk_size", 512)),
    )
