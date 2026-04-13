from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from time import monotonic
from uuid import uuid4

from open_voice_runtime.conversation.events import (
    ConversationEvent,
    ConversationInterruptedEvent,
    SessionClosedEvent,
    SessionCreatedEvent,
    SessionReadyEvent,
    SessionStatusEvent,
    SttFinalEvent,
    SttStatusEvent,
    TurnAcceptedEvent,
    TurnMetricsEvent,
    TurnQueuedEvent,
)
from open_voice_runtime.session.manager import SessionManager
from open_voice_runtime.session.models import SessionState, SessionStatus, SessionTransition
from open_voice_runtime.session.state_machine import can_transition
from open_voice_runtime.session.turns import TurnDetectionConfig, TurnDetectionMode
from open_voice_runtime.session_worker.endpointing import EndpointDetector
from open_voice_runtime.session_worker.input_buffer import BufferedUtterance, InputBuffer
from open_voice_runtime.session_worker.output_streamer import OutputStreamer
from open_voice_runtime.session_worker.response_pipeline import ResponsePipeline
from open_voice_runtime.session_worker.shared import (
    audio_chunk_from_message,
    conversation_events_from_vad,
    emit_conversation_events,
    merge_engine_selection,
    merge_runtime_config_update,
    safe_str,
    set_generation_for_events,
    vad_config,
)
from open_voice_runtime.session_worker.state import (
    QueuedUtterance,
    SessionWorkerRuntimeState,
    TurnLifecycle,
    TurnTrace,
)
from open_voice_runtime.session_worker.transcription import TranscriptionCoordinator
from open_voice_runtime.llm.contracts import LlmEvent, LlmEventKind
from open_voice_runtime.transport.websocket.protocol import (
    AgentGenerateReplyMessage,
    AgentSayMessage,
    AudioAppendMessage,
    AudioCommitMessage,
    ClientMessage,
    ConfigUpdateMessage,
    ConversationInterruptMessage,
    EngineSelectMessage,
    SessionCloseMessage,
    SessionStartMessage,
    UserTurnCommitMessage,
)
from open_voice_runtime.vad.service import VadService

ConversationEventEmitter = Callable[[ConversationEvent], Awaitable[None]]


class SessionWorker:
    def __init__(
        self,
        state: SessionState,
        *,
        sessions: SessionManager,
        vad_service: VadService | None,
        transcription: TranscriptionCoordinator,
        response_pipeline: ResponsePipeline,
        output_streamer: OutputStreamer,
        created_pending: bool = False,
    ) -> None:
        self._state = state
        self._sessions = sessions
        self._vad_service = vad_service
        self._transcription = transcription
        self._response_pipeline = response_pipeline
        self._output_streamer = output_streamer
        self._runtime = SessionWorkerRuntimeState()
        self._runtime.turn_detection = _turn_detection_config(state)
        self._input_buffer = InputBuffer(state.session_id)
        self._endpoint_detector: EndpointDetector | None = None
        self._created_pending = created_pending
        self._emit: ConversationEventEmitter | None = None

    @property
    def session_id(self) -> str:
        return self._state.session_id

    async def apply_message(
        self,
        message: ClientMessage,
        *,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        if emit is not None:
            self._emit = emit
        if isinstance(message, SessionStartMessage):
            return await self._start_session(message)
        if isinstance(message, AudioAppendMessage):
            return await self._append_audio(message, emit=emit)
        if isinstance(message, AudioCommitMessage):
            return await self._commit_audio(message, explicit_commit=True, emit=emit)
        if isinstance(message, UserTurnCommitMessage):
            return await self._commit_audio(
                AudioCommitMessage(
                    session_id=message.session_id,
                    sequence=message.sequence,
                    client_turn_id=message.client_turn_id,
                ),
                explicit_commit=True,
                emit=emit,
            )
        if isinstance(message, AgentSayMessage):
            return await self._agent_say(message, emit=emit)
        if isinstance(message, AgentGenerateReplyMessage):
            return await self._agent_generate_reply(message, emit=emit)
        if isinstance(message, ConversationInterruptMessage):
            return await self._interrupt(message)
        if isinstance(message, EngineSelectMessage):
            self._state.engine_selection = merge_engine_selection(
                self._state.engine_selection, message.engine_selection
            )
            self._state.touch()
            return []
        if isinstance(message, ConfigUpdateMessage):
            merge_runtime_config_update(self._state.metadata, message.config)
            self._runtime.turn_detection = _turn_detection_config(self._state)
            self._state.touch()
            return []
        if isinstance(message, SessionCloseMessage):
            return await self._close_session(message)
        return []

    async def _start_session(self, message: SessionStartMessage) -> list[ConversationEvent]:
        self._state.engine_selection = merge_engine_selection(
            self._state.engine_selection, message.engine_selection
        )
        self._state.metadata.update(dict(message.metadata))
        if message.config:
            merge_runtime_config_update(self._state.metadata, message.config)
        self._runtime.turn_detection = _turn_detection_config(self._state)

        events: list[ConversationEvent] = []
        if self._created_pending:
            events.append(SessionCreatedEvent(self._state.session_id))
            self._created_pending = False
        if can_transition(self._state.status, SessionStatus.LOADING):
            events.extend(await self._transition_session(SessionStatus.LOADING, "session.start"))
        if can_transition(self._state.status, SessionStatus.READY):
            events.extend(await self._transition_session(SessionStatus.READY, "session.loaded"))
        if can_transition(self._state.status, SessionStatus.LISTENING):
            events.extend(
                await self._transition_session(SessionStatus.LISTENING, "session.awaiting_audio")
            )
        events.append(SessionReadyEvent(self._state.session_id))
        return events

    async def _append_audio(
        self,
        message: AudioAppendMessage,
        *,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        chunk = audio_chunk_from_message(message)
        self._input_buffer.append(chunk)
        detector = await self._ensure_endpoint_detector()
        if detector is None:
            return []
        decision = await detector.push_audio(chunk)
        vad_events = conversation_events_from_vad(
            self._state.session_id,
            self._state.active_turn_id,
            decision.vad_events,
        )
        if decision.speech_started and self._runtime.lifecycle is TurnLifecycle.IDLE:
            self._runtime.lifecycle = TurnLifecycle.SPEECH_DETECTED
        if decision.speech_ended:
            self._runtime.lifecycle = TurnLifecycle.ENDPOINT_PENDING
        if decision.endpoint_ready:
            commit_events = await self._commit_audio(
                AudioCommitMessage(session_id=message.session_id),
                explicit_commit=False,
                emit=emit,
            )
            return [*vad_events, *commit_events]
        return vad_events

    async def _commit_audio(
        self,
        message: AudioCommitMessage,
        *,
        explicit_commit: bool,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        utterance = self._seal_input_buffer(explicit_commit=explicit_commit)
        if utterance is None:
            return []
        client_turn_id = safe_str(message.client_turn_id)
        if self._has_active_response():
            policy = _turn_queue_policy(self._state)
            if policy == "enqueue":
                self._runtime.queued_utterances.append(
                    QueuedUtterance(client_turn_id=client_turn_id, utterance=utterance)
                )
                return [
                    TurnQueuedEvent(
                        self._state.session_id,
                        len(self._runtime.queued_utterances),
                        source="audio.commit",
                        policy=policy,
                    )
                ]
            interrupt_events = await self._interrupt(
                ConversationInterruptMessage(session_id=message.session_id, reason="send_now")
            )
            next_events = await self._start_utterance_turn(
                utterance,
                client_turn_id=client_turn_id,
                emit=emit,
            )
            return [*interrupt_events, *next_events]
        return await self._start_utterance_turn(
            utterance,
            client_turn_id=client_turn_id,
            emit=emit,
        )

    async def _start_utterance_turn(
        self,
        utterance: BufferedUtterance,
        *,
        client_turn_id: str | None,
        emit: ConversationEventEmitter | None,
    ) -> list[ConversationEvent]:
        self._reset_tool_feedback_state()
        turn_id = self._state.begin_turn()
        self._runtime.active_turn_id = turn_id
        self._runtime.current_trace = TurnTrace(started_at=utterance.started_at_monotonic)
        self._runtime.lifecycle = TurnLifecycle.TRANSCRIPTION_QUEUED

        immediate_events: list[ConversationEvent] = []
        if client_turn_id:
            immediate_events.append(
                TurnAcceptedEvent(self._state.session_id, client_turn_id, turn_id=turn_id)
            )
        immediate_events.extend(
            await self._transition_session(SessionStatus.TRANSCRIBING, "stt.commit")
        )
        immediate_events.append(
            SttStatusEvent(self._state.session_id, "queued", turn_id=turn_id, attempt=1)
        )

        if emit is not None:
            generation_id = self._new_generation_id()
            self._runtime.active_generation_id = generation_id
            self._runtime.response_task = asyncio.create_task(
                self._run_turn_pipeline(
                    turn_id=turn_id,
                    utterance=utterance,
                    generation_id=generation_id,
                    emit=emit,
                ),
                name=f"session-worker:{self._state.session_id}:{turn_id}:{generation_id}",
            )
            return immediate_events

        immediate_events.append(
            SttStatusEvent(self._state.session_id, "running", turn_id=turn_id, attempt=1)
        )
        events = await self._process_utterance_turn(
            turn_id=turn_id,
            utterance=utterance,
            generation_id=None,
            emit=None,
        )
        return [*immediate_events, *events]

    async def _run_turn_pipeline(
        self,
        *,
        turn_id: str,
        utterance: BufferedUtterance,
        generation_id: str,
        emit: ConversationEventEmitter,
    ) -> None:
        try:
            await emit_conversation_events(
                emit,
                [SttStatusEvent(self._state.session_id, "running", turn_id=turn_id, attempt=1)],
            )
            await self._process_utterance_turn(
                turn_id=turn_id,
                utterance=utterance,
                generation_id=generation_id,
                emit=emit,
            )
        except asyncio.CancelledError:
            raise
        finally:
            self._runtime.response_task = None
            if self._runtime.active_generation_id == generation_id:
                self._runtime.active_generation_id = None
            if (
                self._runtime.queued_utterances
                and self._emit is not None
                and self._state.status is SessionStatus.LISTENING
            ):
                queued = self._runtime.queued_utterances.pop(0)
                await self._start_queued_utterance(queued, emit=self._emit)

    async def _start_queued_utterance(
        self,
        queued: QueuedUtterance,
        *,
        emit: ConversationEventEmitter,
    ) -> None:
        if self._has_active_response():
            self._runtime.queued_utterances.insert(0, queued)
            return
        immediate_events = await self._start_utterance_turn(
            queued.utterance,
            client_turn_id=queued.client_turn_id,
            emit=emit,
        )
        if immediate_events:
            await emit_conversation_events(emit, immediate_events)

    async def _process_utterance_turn(
        self,
        *,
        turn_id: str,
        utterance: BufferedUtterance,
        generation_id: str | None,
        emit: ConversationEventEmitter | None,
    ) -> list[ConversationEvent]:
        events: list[ConversationEvent] = []
        trace = self._runtime.current_trace
        if trace is not None:
            trace.transcription_queued_at = utterance.ended_at_monotonic
            trace.transcription_started_at = monotonic()

        transcription = await self._transcription.transcribe(
            utterance,
            engine_id=self._state.engine_selection.stt,
            language=_session_language(self._state),
        )
        if trace is not None:
            trace.transcription_completed_at = monotonic()

        stt_events: list[ConversationEvent] = [
            SttStatusEvent(
                self._state.session_id,
                "completed",
                turn_id=turn_id,
                waited_ms=transcription.queued_ms,
                attempt=1,
            )
        ]
        final_text = (transcription.text or "").strip()
        if not final_text:
            stt_events.append(
                SttStatusEvent(
                    self._state.session_id,
                    "failed",
                    turn_id=turn_id,
                    waited_ms=transcription.execution_ms,
                    attempt=1,
                )
            )
            self._state.complete_turn()
            self._runtime.active_turn_id = None
            listening = await self._transition_session(SessionStatus.LISTENING, "stt.empty")
            set_generation_for_events(stt_events, generation_id)
            set_generation_for_events(listening, generation_id)
            if emit is not None:
                await emit_conversation_events(emit, stt_events)
                await emit_conversation_events(emit, listening)
                return []
            return [*stt_events, *listening]

        stt_events.append(
            SttFinalEvent(
                self._state.session_id,
                final_text,
                turn_id=turn_id,
                confidence=transcription.confidence,
            )
        )
        set_generation_for_events(stt_events, generation_id)
        if emit is not None:
            await emit_conversation_events(emit, stt_events)
        else:
            events.extend(stt_events)

        route_events, decision = await self._response_pipeline.route_text(
            self._state,
            turn_id=turn_id,
            text=final_text,
        )
        if trace is not None:
            trace.route_selected_at = monotonic()
        set_generation_for_events(route_events, generation_id)
        thinking_events = await self._transition_session(SessionStatus.THINKING, "llm.generating")
        set_generation_for_events(thinking_events, generation_id)
        if emit is not None:
            await emit_conversation_events(emit, route_events)
            await emit_conversation_events(emit, thinking_events)
        else:
            events.extend(route_events)
            events.extend(thinking_events)

        llm_events, assistant_text, first_llm_delta_at = await self._response_pipeline.stream_llm(
            self._state,
            turn_id=turn_id,
            user_text=final_text,
            decision=decision,
            generation_id=generation_id,
            emit=emit,
            on_llm_event=(
                (
                    lambda item: self._handle_llm_event_for_feedback(
                        item, turn_id, generation_id, emit
                    )
                )
                if emit is not None and generation_id is not None
                else None
            ),
        )
        if trace is not None:
            trace.llm_started_at = trace.route_selected_at or monotonic()
            trace.first_llm_delta_at = first_llm_delta_at
        self._runtime.current_assistant_text = assistant_text or ""
        if emit is None:
            events.extend(llm_events)

        tts_events, first_tts_chunk_at = await self._output_streamer.stream_response(
            self._state,
            turn_id=turn_id,
            text=assistant_text,
            generation_id=generation_id,
            emit=emit,
        )
        if trace is not None:
            trace.tts_started_at = first_tts_chunk_at
        if emit is None:
            events.extend(tts_events)

        self._state.complete_turn(user_text=final_text, assistant_text=assistant_text)
        self._runtime.active_turn_id = None
        listening = await self._transition_session(SessionStatus.LISTENING, "response.complete")
        set_generation_for_events(listening, generation_id)
        metrics = self._build_metrics_event(turn_id=turn_id)
        if metrics is not None:
            metrics.generation_id = generation_id
        if emit is not None:
            await emit_conversation_events(emit, listening)
            if metrics is not None:
                await emit(metrics)
            return []
        events.extend(listening)
        if metrics is not None:
            events.append(metrics)
        return events

    async def _agent_generate_reply(
        self,
        message: AgentGenerateReplyMessage,
        *,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        text = message.user_text.strip()
        if not text:
            return []
        self._reset_tool_feedback_state()
        interrupt_events: list[ConversationEvent] = []
        if self._has_active_response():
            interrupt_events = await self._interrupt(
                ConversationInterruptMessage(session_id=message.session_id, reason="generate_reply")
            )

        turn_id = self._state.begin_turn()
        self._runtime.active_turn_id = turn_id
        self._runtime.current_trace = TurnTrace(started_at=monotonic())
        route_events, decision = await self._response_pipeline.route_text(
            self._state,
            turn_id=turn_id,
            text=text,
        )
        thinking = await self._transition_session(SessionStatus.THINKING, "agent.generate_reply")
        generation_id = self._new_generation_id() if emit is not None else None
        set_generation_for_events(interrupt_events, generation_id)
        set_generation_for_events(route_events, generation_id)
        set_generation_for_events(thinking, generation_id)

        if emit is not None:
            await emit_conversation_events(emit, [*interrupt_events, *route_events, *thinking])
            self._runtime.active_generation_id = generation_id
            self._runtime.response_task = asyncio.create_task(
                self._run_text_turn(
                    turn_id=turn_id,
                    user_text=text,
                    decision=decision,
                    generation_id=generation_id,
                    emit=emit,
                ),
                name=f"session-worker:{self._state.session_id}:{turn_id}:{generation_id}",
            )
            return []

        llm_events, assistant_text, _ = await self._response_pipeline.stream_llm(
            self._state,
            turn_id=turn_id,
            user_text=text,
            decision=decision,
            generation_id=None,
            emit=None,
        )
        tts_events, _ = await self._output_streamer.stream_response(
            self._state,
            turn_id=turn_id,
            text=assistant_text,
            generation_id=None,
            emit=None,
        )
        self._state.complete_turn(user_text=text, assistant_text=assistant_text)
        self._runtime.active_turn_id = None
        listening = await self._transition_session(SessionStatus.LISTENING, "response.complete")
        return [*interrupt_events, *route_events, *thinking, *llm_events, *tts_events, *listening]

    async def _run_text_turn(
        self,
        *,
        turn_id: str,
        user_text: str,
        decision,
        generation_id: str,
        emit: ConversationEventEmitter,
    ) -> None:
        try:
            _, assistant_text, first_llm_delta_at = await self._response_pipeline.stream_llm(
                self._state,
                turn_id=turn_id,
                user_text=user_text,
                decision=decision,
                generation_id=generation_id,
                emit=emit,
                on_llm_event=lambda item: self._handle_llm_event_for_feedback(
                    item,
                    turn_id,
                    generation_id,
                    emit,
                ),
            )
            trace = self._runtime.current_trace
            if trace is not None:
                trace.first_llm_delta_at = first_llm_delta_at
            self._runtime.current_assistant_text = assistant_text or ""
            _, first_tts_chunk_at = await self._output_streamer.stream_response(
                self._state,
                turn_id=turn_id,
                text=assistant_text,
                generation_id=generation_id,
                emit=emit,
            )
            if trace is not None:
                trace.tts_started_at = first_tts_chunk_at
            self._state.complete_turn(user_text=user_text, assistant_text=assistant_text)
            self._runtime.active_turn_id = None
            listening = await self._transition_session(SessionStatus.LISTENING, "response.complete")
            set_generation_for_events(listening, generation_id)
            await emit_conversation_events(emit, listening)
            metrics = self._build_metrics_event(turn_id=turn_id)
            if metrics is not None:
                metrics.generation_id = generation_id
                await emit(metrics)
        finally:
            self._runtime.response_task = None
            self._runtime.active_generation_id = None

    async def _agent_say(
        self,
        message: AgentSayMessage,
        *,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        interrupt_events: list[ConversationEvent] = []
        if self._has_active_response():
            interrupt_events = await self._interrupt(
                ConversationInterruptMessage(session_id=message.session_id, reason="agent.say")
            )
        turn_id = self._state.begin_turn()
        self._runtime.active_turn_id = turn_id
        speaking = await self._transition_session(SessionStatus.SPEAKING, "agent.say")
        generation_id = self._new_generation_id() if emit is not None else None
        set_generation_for_events(interrupt_events, generation_id)
        set_generation_for_events(speaking, generation_id)

        if emit is not None:
            await emit_conversation_events(emit, [*interrupt_events, *speaking])
            await self._output_streamer.stream_response(
                self._state,
                turn_id=turn_id,
                text=message.text,
                generation_id=generation_id,
                emit=emit,
            )
            self._state.complete_turn(assistant_text=message.text)
            self._runtime.active_turn_id = None
            listening = await self._transition_session(
                SessionStatus.LISTENING, "agent.say.complete"
            )
            set_generation_for_events(listening, generation_id)
            await emit_conversation_events(emit, listening)
            return []

        tts_events, _ = await self._output_streamer.stream_response(
            self._state,
            turn_id=turn_id,
            text=message.text,
            generation_id=None,
            emit=None,
        )
        self._state.complete_turn(assistant_text=message.text)
        self._runtime.active_turn_id = None
        listening = await self._transition_session(SessionStatus.LISTENING, "agent.say.complete")
        return [*interrupt_events, *speaking, *tts_events, *listening]

    async def _interrupt(self, message: ConversationInterruptMessage) -> list[ConversationEvent]:
        task = self._runtime.response_task
        generation_id = self._runtime.active_generation_id
        interrupted_turn_id = self._state.active_turn_id
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        trace = self._runtime.current_trace
        if trace is not None:
            trace.cancelled = True
            trace.reason = message.reason or "interrupt"

        self._runtime.response_task = None
        self._runtime.active_generation_id = None
        self._runtime.lifecycle = TurnLifecycle.CANCELLED
        self._runtime.current_assistant_text = ""
        if interrupted_turn_id is not None:
            self._state.complete_turn()
            self._runtime.active_turn_id = None

        events: list[ConversationEvent] = []
        if can_transition(self._state.status, SessionStatus.INTERRUPTED):
            events.extend(
                await self._transition_session(
                    SessionStatus.INTERRUPTED, message.reason or "interrupt"
                )
            )
        if can_transition(self._state.status, SessionStatus.LISTENING):
            events.extend(
                await self._transition_session(SessionStatus.LISTENING, "interrupt.complete")
            )
        interrupted = ConversationInterruptedEvent(
            self._state.session_id,
            turn_id=interrupted_turn_id,
            reason=message.reason,
        )
        interrupted.generation_id = generation_id
        events.append(interrupted)
        metrics = self._build_metrics_event(turn_id=interrupted_turn_id)
        if metrics is not None:
            metrics.generation_id = generation_id
            events.append(metrics)
        return events

    async def _close_session(self, message: SessionCloseMessage) -> list[ConversationEvent]:
        interrupt_events: list[ConversationEvent] = []
        if self._endpoint_detector is not None:
            await self._endpoint_detector.close()
            self._endpoint_detector = None
        if self._has_active_response():
            interrupt_events = await self._interrupt(
                ConversationInterruptMessage(session_id=message.session_id, reason="session.close")
            )
        self._input_buffer.reset()
        await self._sessions.close(message.session_id)
        return [*interrupt_events, SessionClosedEvent(message.session_id)]

    async def _ensure_endpoint_detector(self) -> EndpointDetector | None:
        if self._endpoint_detector is not None:
            return self._endpoint_detector
        if self._vad_service is None or not self._vad_service.is_available():
            return None
        stream = await self._vad_service.create_stream(vad_config(self._state))
        self._endpoint_detector = EndpointDetector(stream)
        return self._endpoint_detector

    def _seal_input_buffer(self, *, explicit_commit: bool) -> BufferedUtterance | None:
        utterance = self._input_buffer.snapshot(
            utterance_id=uuid4().hex,
            explicit_commit=explicit_commit,
        )
        self._input_buffer.reset()
        if self._endpoint_detector is not None:
            self._endpoint_detector.reset()
        return utterance

    async def _transition_session(
        self, status: SessionStatus, reason: str
    ) -> list[ConversationEvent]:
        if not can_transition(self._state.status, status):
            return []
        self._state = await self._sessions.update(
            self._state.session_id,
            SessionTransition(to_status=status, reason=reason),
        )
        return [
            SessionStatusEvent(
                self._state.session_id,
                self._state.status,
                turn_id=self._state.active_turn_id,
                reason=reason,
            )
        ]

    def _build_metrics_event(self, *, turn_id: str | None) -> TurnMetricsEvent | None:
        trace = self._runtime.current_trace
        if trace is None:
            return None
        trace.completed_at = monotonic()
        event = TurnMetricsEvent(
            self._state.session_id,
            turn_id=turn_id,
            queue_delay_ms=_ms_between(
                trace.transcription_queued_at, trace.transcription_started_at
            ),
            stt_to_route_ms=_ms_between(trace.transcription_completed_at, trace.route_selected_at),
            route_to_llm_first_delta_ms=_ms_between(
                trace.route_selected_at, trace.first_llm_delta_at
            ),
            llm_first_delta_to_tts_first_chunk_ms=_ms_between(
                trace.first_llm_delta_at, trace.tts_started_at
            ),
            stt_to_tts_first_chunk_ms=_ms_between(
                trace.transcription_completed_at, trace.tts_started_at
            ),
            turn_to_first_llm_delta_ms=_ms_between(trace.started_at, trace.first_llm_delta_at),
            turn_to_complete_ms=_ms_between(trace.started_at, trace.completed_at),
            cancelled=trace.cancelled,
            reason=trace.reason,
        )
        self._runtime.current_trace = None
        self._runtime.lifecycle = TurnLifecycle.COMPLETED
        return event

    def _new_generation_id(self) -> str:
        return f"gen_{uuid4().hex}"

    def _has_active_response(self) -> bool:
        return self._runtime.response_task is not None and not self._runtime.response_task.done()

    def _reset_tool_feedback_state(self) -> None:
        self._runtime.tool_speech_announcements.clear()
        self._runtime.tool_search_statuses.clear()
        self._runtime.tool_search_start_announced = False
        self._runtime.tool_search_end_announced = False

    async def _handle_llm_event_for_feedback(
        self,
        item: LlmEvent,
        turn_id: str | None,
        generation_id: str | None,
        emit: ConversationEventEmitter,
    ) -> None:
        hint = self._tool_progress_speech_hint(item)
        if not hint or generation_id is None:
            return
        await self._output_streamer.stream_feedback_text(
            self._state,
            turn_id=turn_id,
            text=hint,
            generation_id=generation_id,
            emit=emit,
        )

    def _tool_progress_speech_hint(self, item: LlmEvent) -> str | None:
        if item.kind is not LlmEventKind.TOOL_UPDATE:
            return None

        status_raw = item.metadata.get("status") if isinstance(item.metadata, dict) else None
        status = status_raw.lower() if isinstance(status_raw, str) else ""
        if status in {"pending", "running"}:
            status_bucket = "start"
        elif status in {"completed", "done"}:
            status_bucket = "end"
        elif status in {"error", "failed"}:
            status_bucket = "error"
        else:
            return None

        call_id = item.call_id or item.tool_name or "tool"
        dedup_key = (call_id, status_bucket)
        now = asyncio.get_running_loop().time()

        stale = [
            key
            for key, seen_at in self._runtime.tool_speech_announcements.items()
            if now - seen_at > 10.0
        ]
        for key in stale:
            self._runtime.tool_speech_announcements.pop(key, None)

        if dedup_key in self._runtime.tool_speech_announcements:
            return None
        self._runtime.tool_speech_announcements[dedup_key] = now

        tool_name = (item.tool_name or "").lower()
        is_search = "search" in tool_name or "web" in tool_name

        if is_search:
            call_key = (
                item.call_id
                or f"{tool_name or 'tool'}:{len(self._runtime.tool_speech_announcements)}"
            )
            self._runtime.tool_search_statuses[call_key] = status_bucket

            if status_bucket == "start":
                if not self._runtime.tool_search_start_announced:
                    self._runtime.tool_search_start_announced = True
                    self._runtime.tool_search_end_announced = False
                    return "I am checking a few web sources now."
                return None

            if status_bucket in {"end", "error"}:
                if not self._runtime.tool_search_start_announced:
                    return None
                terminal = {"end", "error"}
                all_terminal = bool(self._runtime.tool_search_statuses) and all(
                    value in terminal for value in self._runtime.tool_search_statuses.values()
                )
                if all_terminal and not self._runtime.tool_search_end_announced:
                    self._runtime.tool_search_end_announced = True
                    source_count = len(self._runtime.tool_search_statuses)
                    if source_count > 1:
                        return f"I finished checking {source_count} web sources."
                    return "I finished checking the web source."
                return None

        if status_bucket == "start":
            return "Searching the web now." if is_search else "I am checking that now."
        if status_bucket == "end":
            return "Web search is complete." if is_search else "That check is complete."
        if status_bucket == "error":
            return "I hit an issue while checking that."
        return None


def _turn_queue_policy(state: SessionState) -> str:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        turn_queue = runtime_config.get("turn_queue", {})
        if isinstance(turn_queue, dict):
            policy = turn_queue.get("policy")
            if policy in {"send_now", "enqueue"}:
                return policy
    return "send_now"


def _turn_detection_config(state: SessionState) -> TurnDetectionConfig:
    runtime_config = state.metadata.get("runtime_config", {})
    turn = runtime_config.get("turn_detection", {}) if isinstance(runtime_config, dict) else {}
    return TurnDetectionConfig(
        mode=TurnDetectionMode.VAD_TIMEOUT,
        min_silence_duration_ms=int(turn.get("min_silence_duration_ms", 600)),
        endpointing_mode=str(turn.get("endpointing_mode", "fixed")),
        endpointing_min_delay=float(turn.get("endpointing_min_delay", 0.5)),
        endpointing_max_delay=float(turn.get("endpointing_max_delay", 3.0)),
    )


def _session_language(state: SessionState) -> str | None:
    value = state.metadata.get("language")
    if isinstance(value, str):
        return value
    return None


def _ms_between(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, end - start) * 1000.0
