from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from open_voice_runtime.audio.types import AudioChunk, AudioEncoding, AudioFormat
from open_voice_runtime.conversation.events import (
    ConversationEvent,
    ConversationInterruptedEvent,
    ErrorEvent,
    SessionCreatedEvent,
    SessionReadyEvent,
    SessionClosedEvent,
    SttFinalEvent,
    SttPartialEvent,
)
from open_voice_runtime.core.errors import OpenVoiceError, TransportProtocolError
from open_voice_runtime.session.manager import SessionManager
from open_voice_runtime.session.models import (
    EngineSelection,
    SessionCreateRequest,
    SessionState,
    SessionStatus,
    SessionTransition,
)
from open_voice_runtime.session.state_machine import can_transition
from open_voice_runtime.transport.websocket.codec import (
    parse_client_message,
    serialize_conversation_event,
)
from open_voice_runtime.transport.websocket.protocol import (
    AudioAppendMessage,
    AudioCommitMessage,
    ClientMessage,
    ConfigUpdateMessage,
    ConversationInterruptMessage,
    EngineSelectMessage,
    SessionCloseMessage,
    SessionStartMessage,
)


@dataclass(slots=True)
class RealtimeSessionBuffer:
    chunks: list[AudioChunk] = field(default_factory=list)
    commits: int = 0


class RealtimeConversationSession:
    def __init__(self, sessions: SessionManager) -> None:
        self._sessions = sessions
        self._buffers: dict[str, RealtimeSessionBuffer] = {}

    async def apply(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            message = parse_client_message(payload)
            events = await self.apply_message(message)
        except OpenVoiceError as error:
            session_id = _session_id_from_payload(payload)
            if session_id is None:
                raise
            events = [ErrorEvent(session_id, error)]
        return [serialize_conversation_event(event) for event in events]

    async def apply_message(self, message: ClientMessage) -> list[ConversationEvent]:
        if isinstance(message, SessionStartMessage):
            return await self._start_session(message)
        if isinstance(message, AudioAppendMessage):
            return await self._append_audio(message)
        if isinstance(message, AudioCommitMessage):
            return await self._commit_audio(message)
        if isinstance(message, ConversationInterruptMessage):
            return await self._interrupt(message)
        if isinstance(message, EngineSelectMessage):
            return await self._select_engines(message)
        if isinstance(message, ConfigUpdateMessage):
            return await self._update_config(message)
        if isinstance(message, SessionCloseMessage):
            return await self._close_session(message)
        raise TransportProtocolError("Unsupported realtime client message instance.")

    async def _start_session(self, message: SessionStartMessage) -> list[ConversationEvent]:
        created = False
        if message.session_id is None:
            state = await self._sessions.create(
                SessionCreateRequest(
                    engine_selection=message.engine_selection,
                    metadata=message.metadata,
                )
            )
            created = True
        else:
            state = await self._sessions.get(message.session_id)
            state.engine_selection = _merge_engine_selection(
                state.engine_selection, message.engine_selection
            )
            state.metadata.update(message.metadata)
            state.touch()

        self._buffers.setdefault(state.session_id, RealtimeSessionBuffer())

        if can_transition(state.status, SessionStatus.LOADING):
            await self._sessions.update(
                state.session_id,
                SessionTransition(to_status=SessionStatus.LOADING, reason="session.start"),
            )
            state = await self._sessions.update(
                state.session_id,
                SessionTransition(to_status=SessionStatus.READY, reason="session.loaded"),
            )

        if can_transition(state.status, SessionStatus.LISTENING):
            await self._sessions.update(
                state.session_id,
                SessionTransition(
                    to_status=SessionStatus.LISTENING, reason="session.awaiting_audio"
                ),
            )

        events: list[ConversationEvent] = []
        if created:
            events.append(SessionCreatedEvent(state.session_id))
        events.append(SessionReadyEvent(state.session_id))
        return events

    async def _append_audio(self, message: AudioAppendMessage) -> list[ConversationEvent]:
        state = await self._sessions.get(message.session_id)
        if state.status is SessionStatus.INTERRUPTED:
            await self._sessions.update(
                state.session_id,
                SessionTransition(to_status=SessionStatus.LISTENING, reason="audio.append"),
            )
        elif state.status is SessionStatus.READY:
            await self._sessions.update(
                state.session_id,
                SessionTransition(to_status=SessionStatus.LISTENING, reason="audio.append"),
            )

        self._buffers.setdefault(state.session_id, RealtimeSessionBuffer()).chunks.append(
            _audio_chunk_from_message(message)
        )
        return []

    async def _commit_audio(self, message: AudioCommitMessage) -> list[ConversationEvent]:
        state = await self._sessions.get(message.session_id)
        buffer = self._buffers.setdefault(state.session_id, RealtimeSessionBuffer())
        if not buffer.chunks:
            return []

        if state.active_turn_id is None:
            turn_id = state.begin_turn()
        else:
            turn_id = state.active_turn_id

        text = _render_fake_transcript(buffer)
        partial = SttPartialEvent(
            state.session_id, f"processing {len(buffer.chunks)} audio chunks", turn_id=turn_id
        )
        final = SttFinalEvent(state.session_id, text, turn_id=turn_id, confidence=0.25)
        state.complete_turn(user_text=text)
        buffer.chunks.clear()
        buffer.commits += 1
        return [partial, final]

    async def _interrupt(self, message: ConversationInterruptMessage) -> list[ConversationEvent]:
        state = await self._sessions.get(message.session_id)
        buffer = self._buffers.setdefault(state.session_id, RealtimeSessionBuffer())
        buffer.chunks.clear()

        if can_transition(state.status, SessionStatus.INTERRUPTED):
            await self._sessions.update(
                state.session_id,
                SessionTransition(
                    to_status=SessionStatus.INTERRUPTED, reason=message.reason or "client"
                ),
            )
            if can_transition(SessionStatus.INTERRUPTED, SessionStatus.LISTENING):
                await self._sessions.update(
                    state.session_id,
                    SessionTransition(
                        to_status=SessionStatus.LISTENING, reason="resume_after_interrupt"
                    ),
                )

        return [
            ConversationInterruptedEvent(
                state.session_id,
                turn_id=state.active_turn_id,
                reason=message.reason,
            )
        ]

    async def _select_engines(self, message: EngineSelectMessage) -> list[ConversationEvent]:
        state = await self._sessions.get(message.session_id)
        state.engine_selection = _merge_engine_selection(
            state.engine_selection, message.engine_selection
        )
        state.touch()
        return []

    async def _update_config(self, message: ConfigUpdateMessage) -> list[ConversationEvent]:
        state = await self._sessions.get(message.session_id)
        state.metadata.setdefault("runtime_config", {})
        config = state.metadata["runtime_config"]
        if isinstance(config, dict):
            config.update(message.config)
        state.touch()
        return []

    async def _close_session(self, message: SessionCloseMessage) -> list[ConversationEvent]:
        await self._sessions.close(message.session_id)
        self._buffers.pop(message.session_id, None)
        return [SessionClosedEvent(message.session_id)]


def _merge_engine_selection(current: EngineSelection, update: EngineSelection) -> EngineSelection:
    return EngineSelection(
        stt=update.stt or current.stt,
        router=update.router or current.router,
        llm=update.llm or current.llm,
        tts=update.tts or current.tts,
    )


def _audio_chunk_from_message(message: AudioAppendMessage) -> AudioChunk:
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


def _render_fake_transcript(buffer: RealtimeSessionBuffer) -> str:
    count = len(buffer.chunks)
    total = sum(len(chunk.data) for chunk in buffer.chunks)
    ms = sum(chunk.duration_ms or 0.0 for chunk in buffer.chunks)
    return f"stub transcript from {count} audio chunks ({total} bytes, {ms:.1f} ms)"


def _session_id_from_payload(payload: dict[str, Any]) -> str | None:
    value = payload.get("session_id")
    if isinstance(value, str):
        return value
    return None
