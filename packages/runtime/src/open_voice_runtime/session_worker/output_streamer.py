from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import monotonic

from open_voice_runtime.audio.types import AudioFormat, AudioEncoding
from open_voice_runtime.conversation.events import ConversationEvent, SessionStatusEvent
from open_voice_runtime.session.models import SessionState, SessionStatus
from open_voice_runtime.conversation.events import TtsChunkEvent, TtsCompletedEvent
from open_voice_runtime.tts.contracts import TtsEvent, TtsEventKind, TtsRequest
from open_voice_runtime.tts.service import TtsService


ConversationEventEmitter = Callable[[ConversationEvent], Awaitable[None]]


class OutputStreamer:
    def __init__(self, tts_service: TtsService | None) -> None:
        self._tts_service = tts_service

    async def stream_response(
        self,
        state: SessionState,
        *,
        turn_id: str | None,
        text: str | None,
        generation_id: str | None,
        emit: ConversationEventEmitter | None = None,
    ) -> tuple[list[ConversationEvent], float | None]:
        if self._tts_service is None or text is None or not text.strip():
            return [], None
        engine_id = state.engine_selection.tts
        if not self._tts_service.is_available(engine_id):
            return [], None

        request = TtsRequest(
            session_id=state.session_id,
            turn_id=turn_id or "",
            text=text,
            audio_format=_tts_audio_format(state),
            voice_id=_tts_voice_id(state),
            language=_session_language(state),
            metadata={"text_segment": text},
        )
        stream = await self._tts_service.stream(request, engine_id=engine_id)
        first_chunk_at: float | None = None
        events: list[ConversationEvent] = []
        speaking_emitted = False
        async for item in stream:
            if item.kind is TtsEventKind.AUDIO_CHUNK and item.audio_chunk is not None:
                if first_chunk_at is None:
                    first_chunk_at = monotonic()
                if not speaking_emitted:
                    speaking_event = SessionStatusEvent(
                        state.session_id,
                        SessionStatus.SPEAKING,
                        turn_id=turn_id,
                        reason="tts.generating",
                    )
                    speaking_event.generation_id = generation_id
                    speaking_emitted = True
                    if emit is not None:
                        await emit(speaking_event)
                    else:
                        events.append(speaking_event)

            conversation_event = _conversation_event_from_tts_event(
                state.session_id,
                turn_id,
                text,
                item,
            )
            if conversation_event is None:
                continue
            conversation_event.generation_id = generation_id
            if emit is not None:
                await emit(conversation_event)
            else:
                events.append(conversation_event)

        return events, first_chunk_at

    async def stream_feedback_text(
        self,
        state: SessionState,
        *,
        turn_id: str | None,
        text: str,
        generation_id: str | None,
        emit: ConversationEventEmitter,
    ) -> None:
        if self._tts_service is None or not text.strip():
            return
        engine_id = state.engine_selection.tts
        if not self._tts_service.is_available(engine_id):
            return

        request = TtsRequest(
            session_id=state.session_id,
            turn_id=turn_id or "",
            text=text,
            audio_format=_tts_audio_format(state),
            voice_id=_tts_voice_id(state),
            language=_session_language(state),
            metadata={"text_segment": text},
        )
        stream = await self._tts_service.stream(request, engine_id=engine_id)
        async for item in stream:
            conversation_event = _conversation_event_from_tts_event(
                state.session_id,
                turn_id,
                text,
                item,
            )
            if conversation_event is None:
                continue
            conversation_event.generation_id = generation_id
            await emit(conversation_event)


def _conversation_events_from_tts(
    session_id: str,
    turn_id: str | None,
    speech_text: str,
    tts_events: list[TtsEvent],
) -> list[ConversationEvent]:
    events: list[ConversationEvent] = []
    for item in tts_events:
        if item.kind is TtsEventKind.AUDIO_CHUNK and item.audio_chunk is not None:
            events.append(
                TtsChunkEvent(
                    session_id,
                    item.audio_chunk,
                    turn_id=turn_id,
                    text_segment=item.text_segment or speech_text,
                )
            )
        elif item.kind is TtsEventKind.COMPLETED:
            events.append(
                TtsCompletedEvent(session_id, turn_id=turn_id, duration_ms=item.duration_ms)
            )
    return events


def _conversation_event_from_tts_event(
    session_id: str,
    turn_id: str | None,
    speech_text: str,
    item: TtsEvent,
) -> ConversationEvent | None:
    if item.kind is TtsEventKind.AUDIO_CHUNK and item.audio_chunk is not None:
        return TtsChunkEvent(
            session_id,
            item.audio_chunk,
            turn_id=turn_id,
            text_segment=item.text_segment or speech_text,
        )
    if item.kind is TtsEventKind.COMPLETED:
        return TtsCompletedEvent(session_id, turn_id=turn_id, duration_ms=item.duration_ms)
    return None


async def _emit_conversation_events(
    emit: ConversationEventEmitter,
    events: list[ConversationEvent],
) -> None:
    for event in events:
        await emit(event)


def _set_generation_for_events(events: list[ConversationEvent], generation_id: str | None) -> None:
    if generation_id is None:
        return
    for event in events:
        event.generation_id = generation_id


def _session_language(state: SessionState) -> str | None:
    value = state.metadata.get("language")
    if isinstance(value, str):
        return value
    return None


def _tts_audio_format(state: SessionState) -> AudioFormat:
    return AudioFormat(sample_rate_hz=24000, channels=1, encoding=AudioEncoding.PCM_S16LE)


def _tts_voice_id(state: SessionState) -> str | None:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        tts_cfg = runtime_config.get("tts")
        if isinstance(tts_cfg, dict):
            value = tts_cfg.get("voice_id")
            if isinstance(value, str):
                return value
    value = state.metadata.get("voice_id")
    if isinstance(value, str):
        return value
    return None
