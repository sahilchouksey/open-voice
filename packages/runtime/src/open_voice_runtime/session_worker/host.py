from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from open_voice_runtime.app.config import RuntimeConfig
from open_voice_runtime.conversation.events import ConversationEvent, ErrorEvent
from open_voice_runtime.core.errors import ErrorCode
from open_voice_runtime.core.errors import OpenVoiceError
from open_voice_runtime.session.manager import SessionManager
from open_voice_runtime.session.models import SessionCreateRequest, SessionState
from open_voice_runtime.session_worker.output_streamer import OutputStreamer
from open_voice_runtime.session_worker.response_pipeline import ResponsePipeline
from open_voice_runtime.session_worker.transcription import TranscriptionCoordinator
from open_voice_runtime.session_worker.worker import SessionWorker
from open_voice_runtime.stt.service import SttService
from open_voice_runtime.transport.websocket.codec import (
    parse_client_message,
    serialize_conversation_event,
)
from open_voice_runtime.transport.websocket.protocol import ClientMessage, SessionStartMessage
from open_voice_runtime.tts.service import TtsService
from open_voice_runtime.vad.service import VadService
from open_voice_runtime.router.service import RouterService
from open_voice_runtime.llm.service import LlmService


@dataclass(slots=True)
class WorkerHost:
    sessions: SessionManager
    config: RuntimeConfig
    stt_service: SttService | None = None
    vad_service: VadService | None = None
    router_service: RouterService | None = None
    llm_service: LlmService | None = None
    tts_service: TtsService | None = None
    _workers: dict[str, SessionWorker] = field(init=False, default_factory=dict)
    _transcription: TranscriptionCoordinator | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        self._transcription = TranscriptionCoordinator(self.stt_service)

    async def apply(
        self,
        payload: dict[str, Any],
        *,
        emit: callable | None = None,
    ) -> list[dict[str, Any]]:
        session_id = (
            payload.get("session_id") if isinstance(payload.get("session_id"), str) else None
        )

        async def emit_event(event: ConversationEvent) -> None:
            if emit is None:
                return
            await emit(serialize_conversation_event(event))

        try:
            message = parse_client_message(payload)
            worker = await self._worker_for_message(message)
            events = await worker.apply_message(
                message, emit=emit_event if emit is not None else None
            )
        except OpenVoiceError as error:
            if session_id is None:
                raise
            error_event = ErrorEvent(session_id, error)
            if emit is not None:
                await emit_event(error_event)
                return []
            events = [error_event]

        if emit is not None:
            for event in events:
                await emit_event(event)
            return []
        return [serialize_conversation_event(event) for event in events]

    async def apply_message(
        self,
        message: ClientMessage,
        *,
        emit: callable | None = None,
    ) -> list[ConversationEvent]:
        worker = await self._worker_for_message(message)
        return await worker.apply_message(message, emit=emit)

    def metrics_snapshot(self) -> dict[str, int]:
        if self._transcription is None:
            return {"workers": 0, "active_jobs": 0, "queued_jobs": 0}
        snapshot = self._transcription.metrics_snapshot()
        return {
            "workers": snapshot.configured_workers,
            "active_jobs": snapshot.active_jobs,
            "queued_jobs": snapshot.queued_jobs,
        }

    async def _worker_for_message(self, message) -> SessionWorker:
        created_pending = False
        if isinstance(message, SessionStartMessage):
            if message.session_id and message.session_id in self._workers:
                return self._workers[message.session_id]
            if message.session_id:
                state = await self.sessions.get(message.session_id)
            else:
                state = await self.sessions.create(
                    SessionCreateRequest(
                        engine_selection=message.engine_selection,
                        metadata=dict(message.metadata),
                    )
                )
                message.session_id = state.session_id
                created_pending = True
            return self._get_or_create_worker(state, created_pending=created_pending)

        session_id = getattr(message, "session_id")
        worker = self._workers.get(session_id)
        if worker is not None:
            return worker
        state = await self.sessions.get(session_id)
        return self._get_or_create_worker(state)

    def _get_or_create_worker(
        self,
        state: SessionState,
        *,
        created_pending: bool = False,
    ) -> SessionWorker:
        existing = self._workers.get(state.session_id)
        if existing is not None:
            return existing
        pipeline = ResponsePipeline(
            config=self.config,
            router_service=self.router_service,
            llm_service=self.llm_service,
        )
        worker = SessionWorker(
            state,
            sessions=self.sessions,
            vad_service=self.vad_service,
            transcription=self._transcription,
            response_pipeline=pipeline,
            output_streamer=OutputStreamer(self.tts_service),
            created_pending=created_pending,
        )
        self._workers[state.session_id] = worker
        return worker
