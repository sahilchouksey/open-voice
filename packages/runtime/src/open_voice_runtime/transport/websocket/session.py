from __future__ import annotations

import asyncio
import base64
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Awaitable, Callable
from uuid import uuid4

from open_voice_runtime.audio.types import AudioChunk, AudioEncoding, AudioFormat
from open_voice_runtime.conversation.events import (
    ConversationEvent,
    ConversationInterruptedEvent,
    ErrorEvent,
    LlmCompletedEvent,
    LlmErrorEvent,
    LlmPhaseEvent,
    LlmReasoningDeltaEvent,
    LlmResponseDeltaEvent,
    LlmSummaryEvent,
    LlmToolUpdateEvent,
    LlmUsageEvent,
    RouteSelectedEvent,
    SessionClosedEvent,
    SessionCreatedEvent,
    SessionReadyEvent,
    SessionStatusEvent,
    SttFinalEvent,
    TurnAcceptedEvent,
    TurnMetricsEvent,
    TurnQueuedEvent,
    TtsChunkEvent,
    TtsCompletedEvent,
    VadStateEvent,
)
from open_voice_runtime.core.errors import ErrorCode, OpenVoiceError, TransportProtocolError
from open_voice_runtime.llm.contracts import (
    LlmEvent,
    LlmEventKind,
    LlmMessage,
    LlmOutputLane,
    LlmPhase,
    LlmRequest,
    LlmRole,
)
from open_voice_runtime.llm.prompting import strip_tts_symbols
from open_voice_runtime.llm.service import LlmService
from open_voice_runtime.session.manager import SessionManager
from open_voice_runtime.session.models import (
    EngineSelection,
    SessionCreateRequest,
    SessionState,
    SessionStatus,
    SessionTransition,
)
from open_voice_runtime.session.turns import TurnRecognition, TurnRecognitionResult
from open_voice_runtime.session.turns import TurnDetectionConfig, TurnDetectionMode
from open_voice_runtime.app.config import RuntimeConfig
from open_voice_runtime.router.contracts import RouteDecision, RouteRequest, RouteTarget
from open_voice_runtime.router.policy import select_route_target
from open_voice_runtime.router.service import RouterService
from open_voice_runtime.stt.contracts import SttConfig, SttEvent, SttEventKind
from open_voice_runtime.stt.engine import BaseSttStream
from open_voice_runtime.stt.service import SttService
from open_voice_runtime.session.state_machine import can_transition
from open_voice_runtime.transport.websocket.codec import (
    parse_client_message,
    serialize_conversation_event,
)
from open_voice_runtime.session_worker.host import WorkerHost
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
from open_voice_runtime.tts.contracts import TtsEvent, TtsEventKind, TtsRequest
from open_voice_runtime.tts.service import TtsService
from open_voice_runtime.vad.service import VadService
from open_voice_runtime.vad.contracts import VadConfig, VadEvent, VadEventKind
from open_voice_runtime.vad.engine import BaseVadStream

logger = logging.getLogger(__name__)


THINKING_PROGRESS_HINT = "Got it - I am working on your request now."
FAST_ACK_TEXT = "Got it."
FAST_ACK_HOLDOFF_SECONDS = 0.35
TOOL_SPEECH_DEDUP_WINDOW_SECONDS = 6.0
TURN_QUEUE_POLICY_SEND_NOW = "send_now"
TURN_QUEUE_POLICY_ENQUEUE = "enqueue"
TURN_QUEUE_POLICY_INJECT_NEXT_LOOP = "inject_next_loop"


DEFAULT_STT_FINAL_TIMEOUT_MS = 1200
DEFAULT_LLM_FIRST_DELTA_TIMEOUT_MS = 45000
DEFAULT_LLM_TOTAL_TIMEOUT_MS = 90000
DEFAULT_STT_STABILIZATION_MS = 0
LLM_RAW_ERROR_PREFIX = "I hit an error: "
LLM_RAW_ERROR_MAX_CHARS = 220


def _truncate_error_for_speech(message: str) -> str:
    message = (message or "").strip()
    if not message:
        return "unknown error"
    if len(message) <= LLM_RAW_ERROR_MAX_CHARS:
        return message
    return message[:LLM_RAW_ERROR_MAX_CHARS].rstrip() + "..."


@dataclass(slots=True)
class _QueuedUserTurn:
    text: str
    enqueued_at: float
    source: str
    policy: str
    stt_final_at: float | None = None


@dataclass(slots=True)
class _TurnTrace:
    started_at: float
    queue_enqueued_at: float | None = None
    stt_final_at: float | None = None
    route_selected_at: float | None = None
    llm_start_at: float | None = None
    first_llm_delta_at: float | None = None
    first_tts_chunk_at: float | None = None
    completed_at: float | None = None
    cancelled: bool = False
    reason: str | None = None


ConversationEventEmitter = Callable[[ConversationEvent], Awaitable[None]]
SerializedEventEmitter = Callable[[dict[str, Any]], Awaitable[None]]


class _LegacyRealtimeConversationSession:
    def __init__(
        self,
        sessions: SessionManager,
        *,
        config: RuntimeConfig | None = None,
        stt_service: SttService | None = None,
        vad_service: VadService | None = None,
        router_service: RouterService | None = None,
        llm_service: LlmService | None = None,
        tts_service: TtsService | None = None,
    ) -> None:
        self._sessions = sessions
        self._config = config or RuntimeConfig()
        self._stt_service = stt_service
        self._vad_service = vad_service
        self._router_service = router_service
        self._llm_service = llm_service
        self._tts_service = tts_service
        self._turns = TurnRecognition()
        self._stt_streams: dict[str, BaseSttStream] = {}
        self._vad_streams: dict[str, BaseVadStream] = {}
        self._response_tasks: dict[str, asyncio.Task[None]] = {}
        self._response_generation_ids: dict[str, str] = {}
        self._preempted_generation_ids: dict[str, str] = {}
        self._response_turn_ids: dict[str, str] = {}
        self._turn_queue: dict[str, list[_QueuedUserTurn]] = {}
        self._active_turn_traces: dict[str, _TurnTrace] = {}
        self._last_interrupt_at: dict[str, float] = {}
        self._post_interrupt_until: dict[str, float] = {}  # Track post-interrupt window
        self._post_interrupt_collecting: dict[
            str, bool
        ] = {}  # True = collecting new speech after interrupt
        self._post_interrupt_turns: dict[
            str, tuple[str, float]
        ] = {}  # session_id -> (turn_id, created_at_timestamp) for turns created after interrupt
        self._turn_start_times: dict[
            str, float
        ] = {}  # session_id -> timestamp when current turn started
        self._speech_after_interrupt: dict[
            str, float
        ] = {}  # session_id -> timestamp when speech started after last interrupt
        self._last_stt_final_at: dict[
            str, float
        ] = {}  # session_id -> timestamp of last stt.final event
        self._last_stt_final_text: dict[
            str, str
        ] = {}  # session_id -> most recent stt.final text snapshot
        self._stt_commit_started_at: dict[
            str, float
        ] = {}  # session_id -> monotonic timestamp of current commit start
        self._last_user_activity_at: dict[
            str, float
        ] = {}  # session_id -> most recent speech/STT activity
        self._barge_in_speech_started_at: dict[
            str, float
        ] = {}  # session_id -> monotonic timestamp of sustained barge-in speech detection
        self._turn_entered_processing_at: dict[
            str, float
        ] = {}  # session_id -> timestamp when turn entered THINKING/PROCESSING
        self._session_speech_turn_count: dict[str, int] = {}
        self._recent_stt_partial_text: dict[str, str] = {}
        self._client_turn_attempts: dict[str, dict[str, int]] = {}
        self._tool_speech_announcements: dict[
            str, dict[tuple[str, str], float]
        ] = {}  # session_id -> {(call_id,status): announced_at}
        self._tool_search_statuses: dict[str, dict[str, str]] = {}
        self._tool_search_start_announced: dict[str, bool] = {}
        self._tool_search_end_announced: dict[str, bool] = {}
        self._worker_host = WorkerHost(
            sessions,
            config=self._config,
            stt_service=stt_service,
            vad_service=vad_service,
            router_service=router_service,
            llm_service=llm_service,
            tts_service=tts_service,
        )

    async def apply(
        self,
        payload: dict[str, Any],
        *,
        emit: SerializedEventEmitter | None = None,
    ) -> list[dict[str, Any]]:
        return await self._worker_host.apply(payload, emit=emit)

    async def apply_message(
        self,
        message: ClientMessage,
        *,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        return await self._worker_host.apply_message(message, emit=emit)

    async def _start_session(self, message: SessionStartMessage) -> list[ConversationEvent]:
        created = False
        if message.session_id is None:
            state = await self._sessions.create(
                SessionCreateRequest(
                    engine_selection=message.engine_selection,
                    metadata=_session_start_metadata(message.metadata, message.config),
                )
            )
            created = True
        else:
            state = await self._sessions.get(message.session_id)
            state.engine_selection = _merge_engine_selection(
                state.engine_selection, message.engine_selection
            )
            state.metadata.update(message.metadata)
            _merge_runtime_config_update(state.metadata, message.config)
            state.touch()

        self._turns.buffer_for(state.session_id)

        events: list[ConversationEvent] = []
        if created:
            events.append(SessionCreatedEvent(state.session_id))

        if can_transition(state.status, SessionStatus.LOADING):
            events.extend(
                await self._transition_session(state, SessionStatus.LOADING, "session.start")
            )
            events.extend(
                await self._transition_session(state, SessionStatus.READY, "session.loaded")
            )

        if can_transition(state.status, SessionStatus.LISTENING):
            events.extend(
                await self._transition_session(
                    state,
                    SessionStatus.LISTENING,
                    "session.awaiting_audio",
                )
            )
        events.append(SessionReadyEvent(state.session_id))
        return events

    async def _append_audio(
        self,
        message: AudioAppendMessage,
        *,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        state = await self._sessions.get(message.session_id)
        status_events: list[ConversationEvent] = []
        chunk = _audio_chunk_from_message(message)
        policy = _turn_queue_policy(state)
        pre_commit_vad_events: list[VadEvent] | None = None
        interrupted_on_this_append = False
        preserved_final_text: str | None = None
        seeded_final_segments_count = 0
        interrupt_cfg = _interruption_config(state)
        recent_send_now_interrupt = False

        if state.status is SessionStatus.INTERRUPTED:
            status_events.extend(
                await self._transition_session(state, SessionStatus.LISTENING, "audio.append")
            )
        elif state.status is SessionStatus.READY:
            status_events.extend(
                await self._transition_session(state, SessionStatus.LISTENING, "audio.append")
            )

        # UNIFIED INTERRUPTION HANDLER
        # Handle interruption at ANY point during response generation (THINKING, SPEAKING, LOADING)
        # or when there's an active generation in progress (e.g., during routing phase)
        if self._should_handle_interruption(state):
            policy = _turn_queue_policy(state)
            strict_speaking_barge_in = (
                policy == TURN_QUEUE_POLICY_SEND_NOW and state.status is SessionStatus.SPEAKING
            )

            # Check interruption mode - if disabled, don't allow interrupts
            if interrupt_cfg["mode"] == "disabled":
                return status_events

            # CRITICAL FIX: Don't allow interrupts during post-interrupt collecting mode
            # When user speaks continuously after an interrupt, we're collecting their speech
            # and waiting for VAD end. We should NOT interrupt the new turn during this time.
            if (
                self._post_interrupt_collecting.get(state.session_id, False)
                and policy != TURN_QUEUE_POLICY_SEND_NOW
            ):
                logger.debug(
                    "Skipping interruption - in post-interrupt collecting mode session=%s",
                    state.session_id,
                )
                return status_events

            if state.status is SessionStatus.TRANSCRIBING:
                if policy == TURN_QUEUE_POLICY_SEND_NOW:
                    latest_final_text = self._turns.buffered_final_text(message.session_id)
                    if latest_final_text:
                        self._turns.seed_final_text(message.session_id, latest_final_text)
                return status_events

            # NOISE GUARD: In send_now mode, if there's an active generation but we
            # have NO confirmed STT text, the "generation" is likely from a noise-triggered
            # auto-commit that is still being transcribed. Block the interrupt to prevent
            # a cascade of noise → commit → send_now interrupt → stt.empty.
            if policy == TURN_QUEUE_POLICY_SEND_NOW and state.status is SessionStatus.LISTENING:
                has_confirmed_stt = bool(
                    self._turns.buffered_final_text(message.session_id)
                    or self._turns.final_segment_count(message.session_id) > 0
                )
                if not has_confirmed_stt:
                    return status_events

            # Don't allow interrupting a turn that was created after an interrupt
            # This prevents the chain reaction where continuous speech after an interrupt
            # causes the new turn to be immediately interrupted when it enters THINKING
            post_interrupt_data = self._post_interrupt_turns.get(state.session_id)
            if post_interrupt_data is not None:
                post_interrupt_turn, post_interrupt_time = post_interrupt_data
                current_time = asyncio.get_running_loop().time()
                time_since_created = current_time - post_interrupt_time
                # Block interruption if this is the post-interrupt turn AND it was created recently
                if (
                    state.active_turn_id == post_interrupt_turn
                    and time_since_created < 5.0
                    and policy != TURN_QUEUE_POLICY_SEND_NOW
                ):
                    logger.debug(
                        "Skipping interruption - turn was created after previous interrupt "
                        "session=%s turn=%s age=%.2fs",
                        state.session_id,
                        state.active_turn_id,
                        time_since_created,
                    )
                    return status_events

            # Check if we recently interrupted (cooldown to prevent rapid-fire interrupts)
            last_interrupt = self._last_interrupt_at.get(state.session_id, 0)
            time_since_interrupt = asyncio.get_running_loop().time() - last_interrupt
            cooldown_seconds = interrupt_cfg["cooldown_ms"] / 1000.0
            recent_send_now_interrupt = (
                policy == TURN_QUEUE_POLICY_SEND_NOW and time_since_interrupt < cooldown_seconds
            )
            if time_since_interrupt < cooldown_seconds and policy != TURN_QUEUE_POLICY_SEND_NOW:
                logger.debug(
                    "Interrupt cooldown active: %.2fs < %.2fs",
                    time_since_interrupt,
                    cooldown_seconds,
                )
                return status_events

            vad_stream = await self._ensure_vad_stream(state)
            if pre_commit_vad_events is None:
                pre_commit_vad_events = await self._push_vad_audio(vad_stream, chunk)
            # CRITICAL FIX: Use same threshold for THINKING and SPEAKING
            # The previous 0.85 threshold during THINKING was too high and missed speech
            # User should be able to interrupt the LLM by speaking at any time
            min_probability = 0.7
            speech_detected = _contains_vad_speech(
                pre_commit_vad_events, min_probability=min_probability
            )

            # Require explicit START_OF_SPEECH for barge-in to avoid
            # false interrupts from noisy inference-only VAD spikes.
            min_duration_seconds = max(0.0, float(interrupt_cfg.get("min_duration", 0.0)))
            require_explicit_barge_start = strict_speaking_barge_in
            if require_explicit_barge_start and not _contains_vad_barge_in_start(
                pre_commit_vad_events
            ):
                speech_detected = False

            # NOISE GUARD: In send_now mode, require at least one confirmed STT final
            # before allowing a VAD-only interrupt. Without any STT text, VAD-only
            # triggers are almost certainly background noise cancelling real work.
            if speech_detected and policy == TURN_QUEUE_POLICY_SEND_NOW:
                has_confirmed_stt = bool(
                    self._turns.buffered_final_text(message.session_id)
                    or self._turns.final_segment_count(message.session_id) > 0
                )
                if not has_confirmed_stt:
                    speech_detected = False
                    logger.debug(
                        "send_now interrupt blocked - no confirmed STT text session=%s",
                        message.session_id,
                    )

            if vad_stream is None:
                speech_detected = True

            has_speech = speech_detected
            now = asyncio.get_running_loop().time()
            if speech_detected and min_duration_seconds > 0.0 and vad_stream is not None:
                first_detected_at = self._barge_in_speech_started_at.get(state.session_id)
                if first_detected_at is None:
                    self._barge_in_speech_started_at[state.session_id] = now
                    has_speech = False
                elif now - first_detected_at < min_duration_seconds:
                    has_speech = False
            elif not speech_detected:
                self._barge_in_speech_started_at.pop(state.session_id, None)

            if not has_speech and policy != TURN_QUEUE_POLICY_SEND_NOW:
                return status_events

            if not has_speech and policy == TURN_QUEUE_POLICY_SEND_NOW:
                logger.debug(
                    "Deferring send_now interrupt until sustained speech is detected "
                    "session=%s min_duration=%.3fs",
                    state.session_id,
                    min_duration_seconds,
                )

            has_live_generation = self._has_active_generation(state.session_id)
            if (
                has_speech
                and policy != TURN_QUEUE_POLICY_ENQUEUE
                and not recent_send_now_interrupt
                and (policy != TURN_QUEUE_POLICY_SEND_NOW or has_live_generation)
            ):
                interrupt_reason = (
                    "send_now" if policy == TURN_QUEUE_POLICY_SEND_NOW else "barge_in"
                )
                if interrupt_reason == "send_now":
                    self._preempt_active_generation(state.session_id)
                self._barge_in_speech_started_at.pop(state.session_id, None)
                preserved_final_text = self._turns.buffered_final_text(message.session_id)
                interrupt_events = await self._interrupt(
                    ConversationInterruptMessage(
                        session_id=message.session_id,
                        reason=interrupt_reason,
                    )
                )
                status_events.extend(interrupt_events)
                # Clear the audio buffer for this session to start fresh after interrupt.
                # IMPORTANT: do not drop the current chunk; continue and treat it as
                # the first chunk of the replacement user turn.
                self._turns.clear_buffer(message.session_id)
                if preserved_final_text:
                    self._turns.seed_final_text(message.session_id, preserved_final_text)
                    seeded_final_segments_count = self._turns.final_segment_count(
                        message.session_id
                    )
                state = await self._sessions.get(message.session_id)
                pre_commit_vad_events = None
                interrupted_on_this_append = True
            elif recent_send_now_interrupt:
                logger.debug(
                    "Skipping rapid send_now re-interrupt before STT final session=%s dt=%.3fs",
                    state.session_id,
                    time_since_interrupt,
                )

        self._turns.append_audio(state.session_id, chunk)

        stream = await self._ensure_stt_stream(state)
        vad_stream = await self._ensure_vad_stream(state)
        if stream is None:
            return []

        await stream.push_audio(chunk)
        if state.active_turn_id is None:
            state.begin_turn()
            self._turn_start_times[state.session_id] = asyncio.get_running_loop().time()

        stt_wait_seconds = 0.02
        stt_events = await self._drain_stt_events(
            state.session_id,
            wait_seconds=stt_wait_seconds,
        )
        has_new_stt_final = any(
            event.kind is SttEventKind.FINAL and bool((event.text or "").strip())
            for event in stt_events
        )

        latest_partial_text = _latest_partial_text_from_stt_events(stt_events)
        if latest_partial_text:
            self._recent_stt_partial_text[state.session_id] = latest_partial_text
        else:
            self._recent_stt_partial_text.pop(state.session_id, None)

        if (
            policy == TURN_QUEUE_POLICY_SEND_NOW
            and self._has_active_generation(state.session_id)
            and not recent_send_now_interrupt
            and not interrupted_on_this_append
        ):
            min_words_for_partial_interrupt = max(2, int(interrupt_cfg.get("min_words", 1)))
            partial_words = _count_meaningful_words(
                self._recent_stt_partial_text.get(state.session_id)
            )
            if partial_words >= min_words_for_partial_interrupt:
                self._preempt_active_generation(state.session_id)
                interrupt_events = await self._interrupt(
                    ConversationInterruptMessage(
                        session_id=message.session_id,
                        reason="send_now",
                    )
                )
                status_events.extend(interrupt_events)
                self._turns.clear_buffer(message.session_id)
                state = await self._sessions.get(message.session_id)
                pre_commit_vad_events = None
                interrupted_on_this_append = True

        if stt_events:
            self._last_user_activity_at[state.session_id] = asyncio.get_running_loop().time()

            # Track when stt.final occurs to prevent interrupts from late audio chunks
        if has_new_stt_final:
            now = asyncio.get_running_loop().time()
            self._last_stt_final_at[state.session_id] = now
            latest_final_text = _final_text_from_stt_events(stt_events)
            if latest_final_text:
                previous_text = self._last_stt_final_text.get(state.session_id)
                if previous_text != latest_final_text:
                    self._last_stt_final_text[state.session_id] = latest_final_text

        if (
            has_new_stt_final
            and not interrupted_on_this_append
            and self._has_active_generation(state.session_id)
        ):
            latest_final_text = _final_text_from_stt_events(stt_events)
            policy = _turn_queue_policy(state)
            interrupt_cfg = _interruption_config(state)
            if policy == TURN_QUEUE_POLICY_SEND_NOW:
                min_duration_seconds = max(0.0, float(interrupt_cfg.get("min_duration", 0.0)))
                sustained_ready = True
                if min_duration_seconds > 0.0:
                    first_detected_at = self._barge_in_speech_started_at.get(state.session_id)
                    sustained_ready = (
                        first_detected_at is not None
                        and (asyncio.get_running_loop().time() - first_detected_at)
                        >= min_duration_seconds
                    )
                # send_now is realtime-first: never suppress valid new STT finals
                # with cooldown/min-word heuristics.
                if not sustained_ready:
                    logger.debug(
                        "Deferring send_now STT-final interrupt until sustained speech session=%s min_duration=%.3fs",
                        state.session_id,
                        min_duration_seconds,
                    )
                elif recent_send_now_interrupt:
                    latest_words = _count_meaningful_words(latest_final_text)
                    min_words_cfg = max(0, int(interrupt_cfg.get("min_words", 0)))
                    rapid_window_min_words = min_words_cfg if min_words_cfg > 0 else 2
                    if latest_words < rapid_window_min_words:
                        logger.debug(
                            "Skipping rapid send_now re-interrupt before STT final "
                            "session=%s dt=%.3fs words=%s required=%s text=%r",
                            state.session_id,
                            time_since_interrupt,
                            latest_words,
                            rapid_window_min_words,
                            latest_final_text,
                        )
                        if not self._has_active_generation(state.session_id):
                            interrupted_on_this_append = True
                            self._turns.clear_buffer(message.session_id)
                            if latest_final_text:
                                self._turns.seed_final_text(message.session_id, latest_final_text)
                                seeded_final_segments_count = self._turns.final_segment_count(
                                    message.session_id
                                )
                    else:
                        logger.debug(
                            "Processing rapid send_now STT final session=%s dt=%.3fs words=%s required=%s text=%r",
                            state.session_id,
                            time_since_interrupt,
                            latest_words,
                            rapid_window_min_words,
                            latest_final_text,
                        )
                        self._preempt_active_generation(state.session_id)
                        interrupt_events = await self._interrupt(
                            ConversationInterruptMessage(
                                session_id=message.session_id,
                                reason="send_now",
                            )
                        )
                        status_events.extend(interrupt_events)
                        self._turns.clear_buffer(message.session_id)
                        if latest_final_text:
                            seeded_final_segments_count = 0
                        state = await self._sessions.get(message.session_id)
                        pre_commit_vad_events = None
                        interrupted_on_this_append = True
                else:
                    self._preempt_active_generation(state.session_id)
                    interrupt_events = await self._interrupt(
                        ConversationInterruptMessage(
                            session_id=message.session_id,
                            reason="send_now",
                        )
                    )
                    status_events.extend(interrupt_events)
                    self._turns.clear_buffer(message.session_id)
                    if latest_final_text:
                        seeded_final_segments_count = 0
                    state = await self._sessions.get(message.session_id)
                    pre_commit_vad_events = None
                    interrupted_on_this_append = True
            elif policy != TURN_QUEUE_POLICY_ENQUEUE:
                interrupt_reason = "barge_in"
                preserved_final_text = self._turns.buffered_final_text(message.session_id)
                interrupt_events = await self._interrupt(
                    ConversationInterruptMessage(
                        session_id=message.session_id,
                        reason=interrupt_reason,
                    )
                )
                status_events.extend(interrupt_events)
                self._turns.clear_buffer(message.session_id)
                if preserved_final_text:
                    self._turns.seed_final_text(message.session_id, preserved_final_text)
                    seeded_final_segments_count = self._turns.final_segment_count(
                        message.session_id
                    )
                if latest_final_text:
                    self._turns.seed_final_text(message.session_id, latest_final_text)
                state = await self._sessions.get(message.session_id)
                pre_commit_vad_events = None
                interrupted_on_this_append = True

        if pre_commit_vad_events is not None:
            vad_events = pre_commit_vad_events
        else:
            vad_events = await self._push_vad_audio(vad_stream, chunk)

        if _contains_vad_speech(vad_events, min_probability=0.5):
            self._last_user_activity_at[state.session_id] = asyncio.get_running_loop().time()

        result = self._turns.evaluate_auto_commit(
            state.session_id,
            config=_turn_detection_config(state),
            stt_events=stt_events,
            vad_events=vad_events,
        )

        # Handle post-interrupt collecting mode.
        # After an interrupt, we buffer audio but don't commit until VAD shows END_OF_SPEECH.
        in_post_interrupt_collecting = self._post_interrupt_collecting.get(state.session_id, False)
        send_now_policy = _turn_queue_policy(state) == TURN_QUEUE_POLICY_SEND_NOW
        turn_count = self._session_speech_turn_count.get(state.session_id, 0)
        if in_post_interrupt_collecting and send_now_policy:
            # send_now must stay low-latency after barge-in; do not gate on VAD end.
            self._post_interrupt_collecting.pop(state.session_id, None)
            in_post_interrupt_collecting = False

        # If this append triggered send_now interruption and we already have
        # recognized final text, force replacement commit so the latest context
        # is not lost waiting for a future VAD-end chunk.
        can_auto_commit = state.status in {
            SessionStatus.LISTENING,
            SessionStatus.READY,
        }
        has_final_in_batch = any(event.kind is SttEventKind.FINAL for event in stt_events)
        has_preserved_final_text = bool(preserved_final_text and preserved_final_text.strip())
        final_segments_now = self._turns.final_segment_count(state.session_id)
        added_new_final_after_interrupt = (
            seeded_final_segments_count > 0 and final_segments_now > seeded_final_segments_count
        )
        no_active_generation = not self._has_active_generation(state.session_id)
        recent_send_now_interrupt_window = False
        if send_now_policy:
            now_for_interrupt_window = asyncio.get_running_loop().time()
            interrupt_cfg = _interruption_config(state)
            cooldown_seconds = max(0.0, interrupt_cfg.get("cooldown_ms", 1000) / 1000.0)
            time_since_interrupt = now_for_interrupt_window - self._last_interrupt_at.get(
                state.session_id, 0
            )
            # Keep a small grace window even when cooldown is configured tiny.
            recent_send_now_interrupt_window = time_since_interrupt < max(0.20, cooldown_seconds)
        force_auto_commit_after_interrupt = (
            can_auto_commit
            and bool(result.final_text and result.final_text.strip())
            and (
                (send_now_policy and interrupted_on_this_append and has_final_in_batch)
                or (
                    send_now_policy
                    and no_active_generation
                    and has_final_in_batch
                    and recent_send_now_interrupt_window
                )
                or (
                    interrupted_on_this_append
                    and has_preserved_final_text
                    and (has_final_in_batch or added_new_final_after_interrupt)
                )
                or (in_post_interrupt_collecting and send_now_policy and no_active_generation)
            )
        )
        if force_auto_commit_after_interrupt and not result.should_auto_commit:
            result = TurnRecognitionResult(
                stt_events=stt_events,
                vad_events=vad_events,
                final_text=result.final_text,
                should_auto_commit=True,
            )

        # send_now: if a new STT final arrives while generation is active,
        # force immediate cutover commit for the replacement turn.
        if (
            send_now_policy
            and has_final_in_batch
            and result.final_text
            and result.final_text.strip()
            and self._has_active_generation(state.session_id)
            and not force_auto_commit_after_interrupt
            and interrupted_on_this_append
        ):
            self._preempt_active_generation(state.session_id)
            force_auto_commit_after_interrupt = True
            if not result.should_auto_commit:
                result = TurnRecognitionResult(
                    stt_events=stt_events,
                    vad_events=vad_events,
                    final_text=result.final_text,
                    should_auto_commit=True,
                )

        # Track when speech starts after an interrupt - do this BEFORE auto-commit check
        # This ensures we track continuous speech even if auto-commit hasn't triggered yet
        if in_post_interrupt_collecting:
            vad_started = any(e.kind is VadEventKind.START_OF_SPEECH for e in vad_events)
            if vad_started and state.session_id not in self._speech_after_interrupt:
                self._speech_after_interrupt[state.session_id] = asyncio.get_running_loop().time()
                logger.info("TRACKING SPEECH START after interrupt session=%s", state.session_id)

        if (
            in_post_interrupt_collecting
            and result.should_auto_commit
            and not force_auto_commit_after_interrupt
        ):
            # Check if VAD signaled end of speech. Some engines only emit
            # INFERENCE speaking=False instead of an explicit END_OF_SPEECH.
            vad_ended = _contains_vad_end_of_speech(vad_events)
            now = asyncio.get_running_loop().time()
            stt_idle_ready, stt_idle_seconds, stt_idle_threshold = _stt_idle_ready_for_commit(
                state,
                final_text=result.final_text,
                last_stt_final_at=self._last_stt_final_at.get(state.session_id),
                now=now,
            )
            force_commit_without_vad_end = stt_idle_ready
            non_first_turn_fast_commit = (
                turn_count > 1
                and bool(result.final_text and result.final_text.strip())
                and len(result.final_text.strip()) >= 4
            )

            if (
                not vad_ended
                and not force_commit_without_vad_end
                and not non_first_turn_fast_commit
            ):
                # Don't auto-commit yet, wait for VAD end
                logger.info(
                    "AUTO-COMMIT: Deferred - waiting for VAD end in post-interrupt mode",
                    extra={
                        "session_id": state.session_id,
                        "turn_id": state.active_turn_id,
                        "final_text": result.final_text,
                        "vad_events_count": len(vad_events),
                        "stt_idle_seconds": stt_idle_seconds,
                        "stt_idle_threshold": stt_idle_threshold,
                    },
                )
                result = TurnRecognitionResult(
                    stt_events=stt_events,
                    final_text=None,
                    should_auto_commit=False,
                )
            else:
                # VAD ended - we can exit collecting mode and commit
                logger.info(
                    "AUTO-COMMIT: Proceeding in post-interrupt mode",
                    extra={
                        "session_id": state.session_id,
                        "turn_id": state.active_turn_id,
                        "final_text": result.final_text,
                        "stt_events_count": len(stt_events),
                        "commit_reason": (
                            "stt_idle_fallback"
                            if force_commit_without_vad_end
                            else "non_first_turn_fast_commit"
                            if non_first_turn_fast_commit
                            else "vad_end"
                        ),
                        "stt_idle_seconds": stt_idle_seconds,
                        "stt_idle_threshold": stt_idle_threshold,
                    },
                )
                self._post_interrupt_collecting.pop(state.session_id, None)

        stt_generation_id = self._response_generation_ids.get(state.session_id)
        if send_now_policy and has_new_stt_final and not interrupted_on_this_append:
            has_active_generation = self._has_active_generation(state.session_id)
            no_active_generation = not has_active_generation
            recent_interrupt = recent_send_now_interrupt_window
            if has_active_generation or (no_active_generation and recent_interrupt):
                # Route fresh user final (barge-in replacement) without pinning it to
                # the old generation id, so client-side stale-generation filters
                # don't drop the new user text.
                stt_generation_id = None

        stt_conversation_events = _conversation_events_from_stt(
            state.session_id,
            state.active_turn_id,
            stt_events,
            generation_id=stt_generation_id,
        )
        vad_conversation_events = _conversation_events_from_vad(
            state.session_id,
            state.active_turn_id,
            vad_events,
        )

        # Only auto-commit if session is in a state that accepts new turns.
        # Prevent committing while already processing (THINKING/SPEAKING/LOADING).

        # For send_now, commit immediately once turn detection marks auto-commit.
        # Hybrid/VAD stabilization remains for non-send_now policies.
        if (
            emit is not None
            and can_auto_commit
            and (result.should_auto_commit or force_auto_commit_after_interrupt)
        ):
            if send_now_policy:
                self._post_interrupt_collecting.pop(state.session_id, None)
            elif force_auto_commit_after_interrupt:
                self._post_interrupt_collecting.pop(state.session_id, None)
                logger.info(
                    "AUTO-COMMIT: Forced after send_now interruption with recognized text",
                    extra={
                        "session_id": state.session_id,
                        "turn_id": state.active_turn_id,
                        "final_text": result.final_text[:120] if result.final_text else None,
                    },
                )
            else:
                # Check if VAD has signaled end of speech. Some engines only emit
                # INFERENCE speaking=False instead of an explicit END_OF_SPEECH.
                vad_ended = _contains_vad_end_of_speech(vad_events)
                now = asyncio.get_running_loop().time()
                stt_idle_ready, stt_idle_seconds, stt_idle_threshold = _stt_idle_ready_for_commit(
                    state,
                    final_text=result.final_text,
                    last_stt_final_at=self._last_stt_final_at.get(state.session_id),
                    now=now,
                )
                force_commit_without_vad_end = stt_idle_ready

                if not vad_ended and not force_commit_without_vad_end:
                    # Don't auto-commit yet - user may still be speaking
                    # Wait for VAD to show END_OF_SPEECH before committing
                    logger.debug(
                        "AUTO-COMMIT: Deferred - waiting for VAD end_of_speech",
                        extra={
                            "session_id": state.session_id,
                            "turn_id": state.active_turn_id,
                            "final_text": result.final_text[:100] if result.final_text else None,
                            "vad_events_count": len(vad_events),
                            "stt_idle_seconds": stt_idle_seconds,
                            "stt_idle_threshold": stt_idle_threshold,
                        },
                    )
                    result = TurnRecognitionResult(
                        stt_events=stt_events,
                        final_text=None,
                        should_auto_commit=False,
                    )
                else:
                    logger.debug(
                        "AUTO-COMMIT: Proceeding",
                        extra={
                            "session_id": state.session_id,
                            "turn_id": state.active_turn_id,
                            "final_text": result.final_text[:100] if result.final_text else None,
                            "commit_reason": (
                                "stt_idle_fallback" if force_commit_without_vad_end else "vad_end"
                            ),
                            "stt_idle_seconds": stt_idle_seconds,
                            "stt_idle_threshold": stt_idle_threshold,
                        },
                    )

        if (
            emit is not None
            and can_auto_commit
            and (result.should_auto_commit or force_auto_commit_after_interrupt)
        ):
            # Clear post-interrupt flag once we commit
            self._post_interrupt_until.pop(state.session_id, None)
            commit_events = await self._commit_audio(
                AudioCommitMessage(session_id=message.session_id),
                emit=emit,
            )
            return [
                *status_events,
                *vad_conversation_events,
                *stt_conversation_events,
                *commit_events,
            ]

        if can_auto_commit and (result.should_auto_commit or force_auto_commit_after_interrupt):
            # Clear post-interrupt flag once we commit
            self._post_interrupt_until.pop(state.session_id, None)
            commit_events = await self._commit_audio(
                AudioCommitMessage(session_id=message.session_id),
            )
            return [
                *status_events,
                *vad_conversation_events,
                *stt_conversation_events,
                *commit_events,
            ]

        # If we can't auto-commit due to status, just return the events without committing
        if result.should_auto_commit and not can_auto_commit:
            logger.debug(
                "Auto-commit deferred - session busy with status=%s",
                state.status.value,
            )

        return [*status_events, *vad_conversation_events, *stt_conversation_events]

    async def _commit_user_turn(
        self,
        message: UserTurnCommitMessage,
        *,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        return await self._commit_audio(
            AudioCommitMessage(
                session_id=message.session_id,
                sequence=message.sequence,
                client_turn_id=message.client_turn_id,
            ),
            emit=emit,
        )

    async def _commit_audio(
        self,
        message: AudioCommitMessage,
        *,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        state = await self._sessions.get(message.session_id)
        retry_enabled = False
        retry_after_ms = 0
        client_turn_id = _safe_str(message.client_turn_id)
        retry_attempt = 0
        if client_turn_id is not None:
            runtime_config = state.metadata.get("runtime_config", {})
            if isinstance(runtime_config, dict):
                retry_cfg = runtime_config.get("retry")
                if isinstance(retry_cfg, dict):
                    retry_enabled = bool(retry_cfg.get("enabled", False))
                    retry_after_ms = max(0, _safe_int(retry_cfg.get("after_ms"), 0))
            retry_attempt = self._increment_client_turn_attempt(state.session_id, client_turn_id)
            if retry_attempt > 1:
                logger.info(
                    "Skipping duplicate commit retry session=%s client_turn_id=%s attempt=%s",
                    state.session_id,
                    client_turn_id,
                    retry_attempt,
                )
                return []

        send_now_policy = _turn_queue_policy(state) == TURN_QUEUE_POLICY_SEND_NOW

        buffer = self._turns.buffer_for(state.session_id)
        logger.info(
            "Commit audio session=%s buffered_chunks=%s stt=%s router=%s status=%s",
            state.session_id,
            len(buffer.chunks),
            state.engine_selection.stt,
            state.engine_selection.router,
            state.status.value,
        )
        buffered_final_text = self._turns.buffered_final_text(state.session_id)
        has_buffered_final_text = bool(buffered_final_text and buffered_final_text.strip())
        if not buffer.chunks and not has_buffered_final_text:
            return []

        if state.active_turn_id is None:
            turn_id = state.begin_turn()
            self._turn_start_times[state.session_id] = asyncio.get_running_loop().time()
            # Track if this turn was created during post-interrupt collecting mode
            post_interrupt_window_until = self._post_interrupt_until.get(state.session_id)
            in_post_interrupt_window = (
                post_interrupt_window_until is not None
                and asyncio.get_running_loop().time() <= post_interrupt_window_until
            )
            if (
                self._post_interrupt_collecting.get(state.session_id, False)
                or in_post_interrupt_window
            ):
                self._post_interrupt_turns[state.session_id] = (
                    turn_id,
                    asyncio.get_running_loop().time(),
                )
                logger.debug(
                    "Tracking post-interrupt turn session=%s turn=%s",
                    state.session_id,
                    turn_id,
                )
        else:
            turn_id = state.active_turn_id
            if turn_id is not None:
                post_interrupt_window_until = self._post_interrupt_until.get(state.session_id)
                in_post_interrupt_window = (
                    post_interrupt_window_until is not None
                    and asyncio.get_running_loop().time() <= post_interrupt_window_until
                )
                if (
                    self._post_interrupt_collecting.get(state.session_id, False)
                    or in_post_interrupt_window
                ):
                    self._post_interrupt_turns[state.session_id] = (
                        turn_id,
                        asyncio.get_running_loop().time(),
                    )
                    logger.debug(
                        "Tracking post-interrupt active turn session=%s turn=%s",
                        state.session_id,
                        turn_id,
                    )

        accepted_events: list[ConversationEvent] = []
        if message.client_turn_id:
            accepted_events.append(
                TurnAcceptedEvent(
                    state.session_id,
                    message.client_turn_id,
                    turn_id=turn_id,
                )
            )

        if accepted_events:
            _set_generation_for_events(
                accepted_events,
                self._response_generation_ids.get(state.session_id),
            )

        emit_accepted_events = bool(emit is not None and accepted_events and message.client_turn_id)
        if emit_accepted_events:
            await _emit_conversation_events(emit, accepted_events)

        turn_count = self._session_speech_turn_count.get(state.session_id, 0) + 1
        self._session_speech_turn_count[state.session_id] = turn_count

        transcribing_status_events: list[ConversationEvent] = []
        if can_transition(state.status, SessionStatus.TRANSCRIBING):
            transcribing_status_events = await self._transition_session(
                state,
                SessionStatus.TRANSCRIBING,
                "stt.commit",
            )

        stream = await self._ensure_stt_stream(state)
        if stream is not None:
            self._stt_commit_started_at[state.session_id] = asyncio.get_running_loop().time()
            commit_wait_seconds = (
                0.2
                if has_buffered_final_text
                else _stt_final_timeout_seconds(state, turn_count=turn_count)
            )
            if turn_count > 1:
                commit_wait_seconds = min(commit_wait_seconds, 0.8)
            stabilization_seconds = _stt_stabilization_seconds(state)
            await stream.flush()
            wait_started_at = asyncio.get_running_loop().time()
            result = await self._turns.collect_commit_result(
                state.session_id,
                lambda wait_seconds: self._drain_stt_events(
                    state.session_id, wait_seconds=wait_seconds
                ),
                timeout_seconds=commit_wait_seconds,
                stabilization_seconds=stabilization_seconds,
            )
            stt_events = result.stt_events
            wait_elapsed_ms = int(
                max(0.0, asyncio.get_running_loop().time() - wait_started_at) * 1000
            )
            logger.info(
                "Drained STT commit events session=%s count=%s final_text=%s",
                state.session_id,
                len(stt_events),
                result.final_text,
            )
            if stt_events:
                self._last_user_activity_at[state.session_id] = asyncio.get_running_loop().time()
            if result.final_text is not None and result.final_text.strip():
                now = asyncio.get_running_loop().time()
                self._last_stt_final_at[state.session_id] = now
                self._last_stt_final_text[state.session_id] = result.final_text
                self._trace_mark_stt_final(state)

            if (
                emit is not None
                and not has_buffered_final_text
                and not (result.final_text and result.final_text.strip())
                and not stt_events
            ):
                timeout_event = await self._emit_stt_timeout_feedback(
                    state,
                    turn_id,
                    emit,
                    details={
                        "timeout_ms": int(commit_wait_seconds * 1000),
                        "stage": "stt.commit",
                        "stabilization_ms": int(stabilization_seconds * 1000),
                    },
                )

                if retry_enabled and client_turn_id and retry_attempt == 1 and retry_after_ms > 0:
                    await asyncio.sleep(retry_after_ms / 1000.0)
                    if self._has_active_generation(state.session_id):
                        return [] if emit is not None else [*accepted_events]
                    return await self._commit_audio(
                        AudioCommitMessage(
                            session_id=message.session_id,
                            sequence=message.sequence,
                            client_turn_id=client_turn_id,
                        ),
                        emit=emit,
                    )

                self._turns.complete_turn(state.session_id)
                if client_turn_id:
                    self._clear_client_turn_attempt(state.session_id, client_turn_id)
                await self._reset_stt_stream(state.session_id)
                await self._reset_vad_stream(state.session_id)
                self._stt_commit_started_at.pop(state.session_id, None)
                if emit is None:
                    return [*accepted_events, *timeout_event]
                return []

            stt_conversation_events = _conversation_events_from_stt(
                state.session_id,
                turn_id,
                stt_events,
                generation_id=self._response_generation_ids.get(state.session_id),
            )
            final_text = result.final_text
            pre_route_events: list[ConversationEvent] = []

            # Handle barge-in: User spoke during active response generation
            # Only trigger when explicitly committing (not auto-commit from delayed chunks)
            # Auto-commit is blocked at _append_audio level when status is THINKING/SPEAKING
            if (
                emit is not None
                and final_text
                and final_text.strip()
                and state.status
                in {
                    SessionStatus.TRANSCRIBING,
                    SessionStatus.THINKING,
                    SessionStatus.SPEAKING,
                    SessionStatus.LOADING,
                }
            ):
                policy = _turn_queue_policy(state)
                has_live_generation = self._has_active_generation(state.session_id)
                # During TRANSCRIBING, only interrupt if there's a live generation
                # to interrupt. Otherwise this is a noise-triggered commit that
                # should just be routed normally without interrupting anything.
                if state.status is SessionStatus.TRANSCRIBING and not has_live_generation:
                    pass
                elif policy == TURN_QUEUE_POLICY_SEND_NOW and not has_live_generation:
                    pass
                else:
                    # CRITICAL FIX: Always interrupt during THINKING state
                    # When user corrects themselves while LLM is thinking, we must interrupt
                    # and process the new input, not queue it.
                    # Queue policy should only apply during SPEAKING (TTS playback).
                    should_interrupt = (
                        state.status in {SessionStatus.TRANSCRIBING, SessionStatus.THINKING}
                        or policy != TURN_QUEUE_POLICY_ENQUEUE
                    )

                    if should_interrupt:
                        interrupt_reason = (
                            "send_now"
                            if policy == TURN_QUEUE_POLICY_SEND_NOW
                            else "user_correction"
                            if state.status
                            in {
                                SessionStatus.TRANSCRIBING,
                                SessionStatus.THINKING,
                            }
                            else "barge_in"
                        )
                        interrupt_events = await self._interrupt(
                            ConversationInterruptMessage(
                                session_id=message.session_id,
                                reason=interrupt_reason,
                            )
                        )
                        pre_route_events.extend(interrupt_events)
                        state = await self._sessions.get(message.session_id)
                    else:
                        # Only queue during SPEAKING with ENQUEUE policy
                        queue_event = self._queue_turn(
                            state,
                            text=final_text,
                            source="audio.commit",
                            stt_final_at=asyncio.get_running_loop().time(),
                        )
                        queue_event.generation_id = self._response_generation_ids.get(
                            state.session_id
                        )
                        await emit(queue_event)
                        self._turns.complete_turn(state.session_id)
                        await self._reset_stt_stream(state.session_id)
                        await self._reset_vad_stream(state.session_id)
                        return []

            # Clear post-interrupt flag when committing a new turn
            self._post_interrupt_until.pop(state.session_id, None)

            route_events, decision = await self._route_text(state, turn_id, final_text)
            thinking_status_events = await self._transition_session(
                state,
                SessionStatus.THINKING,
                "llm.generating",
            )
            # Track when turn entered processing - used for protection against immediate interrupts
            self._turn_entered_processing_at[state.session_id] = asyncio.get_running_loop().time()

            if emit is not None:
                self._trace_start(state)
                generation_id = self._new_generation_id()
                self._response_generation_ids[state.session_id] = generation_id
                _set_generation_for_events(accepted_events, generation_id)
                _set_generation_for_events(stt_conversation_events, generation_id)
                _set_generation_for_events(route_events, generation_id)
                _set_generation_for_events(thinking_status_events, generation_id)
                if not emit_accepted_events:
                    await _emit_conversation_events(emit, accepted_events)
                await _emit_conversation_events(emit, pre_route_events)
                await _emit_conversation_events(emit, transcribing_status_events)
                await _emit_conversation_events(emit, stt_conversation_events)
                self._schedule_fast_ack_if_enabled(
                    state,
                    turn_id,
                    final_text,
                    generation_id,
                    emit,
                )
                await _emit_conversation_events(emit, route_events)
                await _emit_conversation_events(emit, thinking_status_events)
                await self._start_response_task(
                    state,
                    turn_id,
                    final_text,
                    decision,
                    emit,
                    generation_id=generation_id,
                )
                self._turns.complete_turn(state.session_id)
                await self._reset_stt_stream(state.session_id)
                await self._reset_vad_stream(state.session_id)
                self._stt_commit_started_at.pop(state.session_id, None)
                return []

            if accepted_events:
                _set_generation_for_events(
                    accepted_events,
                    self._response_generation_ids.get(state.session_id),
                )

            llm_raw: list[LlmEvent]
            llm_events: list[ConversationEvent]
            assistant_text: str | None
            llm_raw, llm_events, assistant_text = await self._generate_llm_response(
                state,
                turn_id,
                final_text,
                decision,
            )
            tts_events = await self._generate_tts_response(
                state,
                turn_id,
                llm_raw,
                assistant_text,
            )
            listening_status_events = await self._transition_session(
                state,
                SessionStatus.LISTENING,
                "response.complete",
            )
            if final_text is not None:
                state.complete_turn(user_text=final_text, assistant_text=assistant_text)
            else:
                state.complete_turn()
            self._turns.complete_turn(state.session_id)

            # Clear post-interrupt turn tracking when turn completes successfully
            # This allows future interrupts after the post-interrupt turn is done
            completed_turn_data = self._post_interrupt_turns.pop(state.session_id, None)
            if completed_turn_data is not None:
                completed_turn_id, _ = completed_turn_data
                logger.debug(
                    "Cleared post-interrupt turn tracking on completion session=%s turn=%s",
                    state.session_id,
                    completed_turn_id,
                )

            # Clear turn start time when turn completes
            self._turn_start_times.pop(state.session_id, None)

            # Clear speech tracking when turn completes (speech flow ended)
            self._speech_after_interrupt.pop(state.session_id, None)

            # Clear processing tracking when turn completes
            self._turn_entered_processing_at.pop(state.session_id, None)

            # Reset streams for next turn
            await self._reset_stt_stream(state.session_id)
            await self._reset_vad_stream(state.session_id)
            self._stt_commit_started_at.pop(state.session_id, None)

            return [
                *accepted_events,
                *pre_route_events,
                *transcribing_status_events,
                *stt_conversation_events,
                *route_events,
                *thinking_status_events,
                *llm_events,
                *tts_events,
                *listening_status_events,
            ]

        result = self._turns.fake_commit_result(state.session_id)
        pre_route_events: list[ConversationEvent] = []
        transcribing_status_events: list[ConversationEvent] = []
        stt_conversation_events = _conversation_events_from_stt(
            state.session_id,
            turn_id,
            result.stt_events,
            generation_id=self._response_generation_ids.get(state.session_id),
        )

        if (
            emit is not None
            and result.final_text
            and state.status
            in {
                SessionStatus.TRANSCRIBING,
                SessionStatus.THINKING,
                SessionStatus.SPEAKING,
            }
        ):
            policy = _turn_queue_policy(state)
            has_live_generation = self._has_active_generation(state.session_id)
            # send_now should only use this replacement/queue branch when an
            # existing generation is truly active. Otherwise this is a normal
            # commit path for the current user turn.
            if policy == TURN_QUEUE_POLICY_SEND_NOW and not has_live_generation:
                pass
            else:
                # CRITICAL FIX: Always interrupt during THINKING state
                # When user corrects themselves while LLM is thinking, we must interrupt
                # and process the new input, not queue it.
                should_interrupt = (
                    state.status in {SessionStatus.TRANSCRIBING, SessionStatus.THINKING}
                    or policy != TURN_QUEUE_POLICY_ENQUEUE
                )

                if should_interrupt:
                    interrupt_reason = (
                        "send_now"
                        if policy == TURN_QUEUE_POLICY_SEND_NOW
                        else "user_correction"
                        if state.status in {SessionStatus.TRANSCRIBING, SessionStatus.THINKING}
                        else "barge_in"
                    )
                    interrupt_events = await self._interrupt(
                        ConversationInterruptMessage(
                            session_id=message.session_id,
                            reason=interrupt_reason,
                        )
                    )
                    pre_route_events.extend(interrupt_events)
                    state = await self._sessions.get(message.session_id)
                else:
                    # Only queue during SPEAKING with ENQUEUE policy
                    queue_event = self._queue_turn(
                        state,
                        text=result.final_text,
                        source="audio.commit",
                        stt_final_at=asyncio.get_running_loop().time(),
                    )
                    queue_event.generation_id = self._response_generation_ids.get(state.session_id)
                    await emit(queue_event)
                    self._turns.complete_turn(state.session_id)
                    await self._reset_stt_stream(state.session_id)
                    await self._reset_vad_stream(state.session_id)
                    return []

        route_events, decision = await self._route_text(state, turn_id, result.final_text)
        thinking_status_events = await self._transition_session(
            state,
            SessionStatus.THINKING,
            "llm.generating",
        )

        if accepted_events:
            _set_generation_for_events(
                accepted_events,
                self._response_generation_ids.get(state.session_id),
            )

        if emit is not None:
            self._trace_start(state)
            generation_id = self._new_generation_id()
            self._response_generation_ids[state.session_id] = generation_id
            _set_generation_for_events(accepted_events, generation_id)
            _set_generation_for_events(stt_conversation_events, generation_id)
            _set_generation_for_events(route_events, generation_id)
            _set_generation_for_events(thinking_status_events, generation_id)
            if not emit_accepted_events:
                await _emit_conversation_events(emit, accepted_events)
            await _emit_conversation_events(emit, pre_route_events)
            await _emit_conversation_events(emit, transcribing_status_events)
            await _emit_conversation_events(emit, stt_conversation_events)
            self._schedule_fast_ack_if_enabled(
                state,
                turn_id,
                result.final_text,
                generation_id,
                emit,
            )
            await _emit_conversation_events(emit, route_events)
            await _emit_conversation_events(emit, thinking_status_events)
            await self._start_response_task(
                state,
                turn_id,
                result.final_text,
                decision,
                emit,
                generation_id=generation_id,
            )
            if client_turn_id:
                self._clear_client_turn_attempt(state.session_id, client_turn_id)
            self._turns.complete_turn(state.session_id)
            await self._reset_stt_stream(state.session_id)
            await self._reset_vad_stream(state.session_id)
            self._stt_commit_started_at.pop(state.session_id, None)
            return []

        llm_raw: list[LlmEvent]
        llm_events: list[ConversationEvent]
        assistant_text: str | None
        llm_raw, llm_events, assistant_text = await self._generate_llm_response(
            state,
            turn_id,
            result.final_text,
            decision,
        )
        tts_events = await self._generate_tts_response(
            state,
            turn_id,
            llm_raw,
            assistant_text,
        )
        listening_status_events = await self._transition_session(
            state,
            SessionStatus.LISTENING,
            "response.complete",
        )
        state.complete_turn(user_text=result.final_text, assistant_text=assistant_text)
        if client_turn_id:
            self._clear_client_turn_attempt(state.session_id, client_turn_id)
        self._turns.complete_turn(state.session_id)
        await self._reset_stt_stream(state.session_id)
        await self._reset_vad_stream(state.session_id)
        self._stt_commit_started_at.pop(state.session_id, None)
        return [
            *accepted_events,
            *pre_route_events,
            *transcribing_status_events,
            *stt_conversation_events,
            *route_events,
            *thinking_status_events,
            *llm_events,
            *tts_events,
            *listening_status_events,
        ]

    async def _interrupt(self, message: ConversationInterruptMessage) -> list[ConversationEvent]:
        state = await self._sessions.get(message.session_id)
        interrupted_turn_id = state.active_turn_id
        generation_id = self._response_generation_ids.get(state.session_id)

        # Check cooldown for manual interrupts
        current_time = asyncio.get_running_loop().time()
        last_interrupt = self._last_interrupt_at.get(state.session_id, 0)
        time_since_interrupt = current_time - last_interrupt
        runtime_preemption = message.reason in {"send_now", "barge_in", "user_correction"}

        # Get cooldown from config (default 1 second)
        interrupt_cfg = _interruption_config(state)
        cooldown_seconds = interrupt_cfg.get("cooldown_ms", 1000) / 1000.0

        if time_since_interrupt < cooldown_seconds and not runtime_preemption:
            logger.info(
                "Manual interrupt blocked - cooldown active: %.2fs < %.2fs",
                time_since_interrupt,
                cooldown_seconds,
            )
            return []

        # Log interruption initiation with full context
        interrupt_context = {
            "session_id": state.session_id,
            "interrupted_turn_id": interrupted_turn_id,
            "reason": message.reason or "client",
            "previous_status": state.status.value,
            "generation_id": generation_id,
            "has_active_generation": generation_id is not None,
        }
        logger.info("INTERRUPTION STARTED", extra=interrupt_context)

        # Clear the current turn buffer (similar to LiveKit's session.interrupt())
        self._turns.interrupt(state.session_id)
        # Explicitly clear user turn to discard any buffered user audio/transcripts
        # (similar to LiveKit's session.clear_user_turn())
        self._turns.clear_user_turn(state.session_id)

        # Preserve generation id for interruption telemetry and stale-frame guards.
        # We only clear these identifiers after the replacement path finalizes.
        self._response_turn_ids.pop(state.session_id, None)
        self._tool_search_statuses.pop(state.session_id, None)
        self._tool_search_start_announced.pop(state.session_id, None)
        self._tool_search_end_announced.pop(state.session_id, None)

        cancel_timeout = 0.01 if runtime_preemption else 0.25
        await self._cancel_response_task(state.session_id, await_timeout_s=cancel_timeout)

        if runtime_preemption:
            # Keep the active STT stream for runtime barge-ins so pending
            # interrupting speech can still finalize into the replacement turn.
            await self._reset_vad_stream(state.session_id)
            # Also reset STT stream to avoid carrying stale decoder state across
            # multiple barge-ins, which can delay/merge subsequent turns.
            await self._reset_stt_stream(state.session_id)
        else:
            # For manual interrupts, clear pending STT data to avoid stale turns.
            await self._drain_and_discard_stt_events(state.session_id)
            await self._reset_stt_stream(state.session_id)
            await self._reset_vad_stream(state.session_id)

        status_events: list[ConversationEvent] = []

        if can_transition(state.status, SessionStatus.INTERRUPTED):
            status_events.extend(
                await self._transition_session(
                    state,
                    SessionStatus.INTERRUPTED,
                    message.reason or "client",
                )
            )
            if can_transition(SessionStatus.INTERRUPTED, SessionStatus.LISTENING):
                status_events.extend(
                    await self._transition_session(
                        state,
                        SessionStatus.LISTENING,
                        "resume_after_interrupt",
                    )
                )

        state.complete_turn()

        self._active_turn_traces.pop(state.session_id, None)
        self._turn_queue.pop(state.session_id, None)
        self._last_interrupt_at[state.session_id] = current_time
        # Mark short post-interrupt guard window to avoid stale-chunk thrash,
        # but keep barge-in response realtime.
        self._post_interrupt_until[state.session_id] = current_time + 0.25

        # Clear speech tracking after interrupt - will be set when speech starts
        self._speech_after_interrupt.pop(state.session_id, None)
        self._last_user_activity_at.pop(state.session_id, None)
        self._barge_in_speech_started_at.pop(state.session_id, None)

        # Clear STT final tracking after interrupt - CRITICAL: prevents old STT final from blocking new turns
        self._last_stt_final_at.pop(state.session_id, None)
        self._last_stt_final_text.pop(state.session_id, None)
        self._recent_stt_partial_text.pop(state.session_id, None)
        self._stt_commit_started_at.pop(state.session_id, None)
        self._client_turn_attempts.pop(state.session_id, None)

        # Clear processing tracking after interrupt
        self._turn_entered_processing_at.pop(state.session_id, None)

        # Enter post-interrupt collecting mode only for non-send_now flows.
        # send_now should remain realtime-first for interruption cutover.
        if _turn_queue_policy(state) != TURN_QUEUE_POLICY_SEND_NOW:
            self._post_interrupt_collecting[state.session_id] = True
        else:
            self._post_interrupt_collecting.pop(state.session_id, None)

        logger.info(
            "INTERRUPTION COMPLETED - protection activated",
            extra={
                **interrupt_context,
                "post_interrupt_until": current_time + 0.25,
                "protection_duration": 0.25,
                "status_transitioned": len(status_events) > 0,
            },
        )

        # Clear any previous post-interrupt turn tracking when a new interrupt happens
        # This ensures we don't prevent legitimate interrupts after the chain is broken
        cleared_turn_data = self._post_interrupt_turns.pop(state.session_id, None)
        if cleared_turn_data is not None:
            cleared_turn, _ = cleared_turn_data
            logger.debug(
                "Cleared previous post-interrupt turn tracking session=%s turn=%s",
                state.session_id,
                cleared_turn,
            )

        # Clear turn start time when turn is interrupted
        self._turn_start_times.pop(state.session_id, None)

        interrupted_event = ConversationInterruptedEvent(
            state.session_id,
            turn_id=interrupted_turn_id,
            reason=message.reason,
        )
        interrupted_event.generation_id = generation_id
        return [
            *status_events,
            interrupted_event,
        ]

    async def _agent_say(
        self,
        message: AgentSayMessage,
        *,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        state = await self._sessions.get(message.session_id)
        if state.active_turn_id is None:
            turn_id = state.begin_turn()
            self._turn_start_times[state.session_id] = asyncio.get_running_loop().time()
        else:
            turn_id = state.active_turn_id

        speaking_status_events = await self._transition_session(
            state,
            SessionStatus.SPEAKING,
            "agent.say",
        )
        events = await self._generate_tts_response(state, turn_id, [], message.text)
        listening_status_events = await self._transition_session(
            state,
            SessionStatus.LISTENING,
            "agent.say.complete",
        )
        state.complete_turn(assistant_text=message.text)
        return [*speaking_status_events, *events, *listening_status_events]

    async def _agent_generate_reply(
        self,
        message: AgentGenerateReplyMessage,
        *,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        state = await self._sessions.get(message.session_id)

        queue_policy = _turn_queue_policy(state)
        if state.status in {
            SessionStatus.TRANSCRIBING,
            SessionStatus.THINKING,
            SessionStatus.SPEAKING,
        }:
            if queue_policy == TURN_QUEUE_POLICY_SEND_NOW:
                interrupt_events = await self._interrupt(
                    ConversationInterruptMessage(
                        session_id=message.session_id,
                        reason="send_now",
                    )
                )
                state = await self._sessions.get(message.session_id)
                if state.active_turn_id is None:
                    turn_id = state.begin_turn()
                    self._turn_start_times[state.session_id] = asyncio.get_running_loop().time()
                else:
                    turn_id = state.active_turn_id

                route_events, decision = await self._route_text(state, turn_id, message.user_text)
                thinking_status_events = await self._transition_session(
                    state,
                    SessionStatus.THINKING,
                    "agent.generate_reply",
                )

                if emit is not None:
                    self._trace_start(state)
                    generation_id = self._new_generation_id()
                    _set_generation_for_events(interrupt_events, generation_id)
                    _set_generation_for_events(route_events, generation_id)
                    _set_generation_for_events(thinking_status_events, generation_id)
                    await _emit_conversation_events(emit, interrupt_events)
                    await _emit_conversation_events(emit, route_events)
                    await _emit_conversation_events(emit, thinking_status_events)
                    await self._start_response_task(
                        state,
                        turn_id,
                        message.user_text,
                        decision,
                        emit,
                        generation_id=generation_id,
                    )
                    return []

                llm_raw, llm_events, assistant_text = await self._generate_llm_response(
                    state,
                    turn_id,
                    message.user_text,
                    decision,
                )
                tts_events = await self._generate_tts_response(
                    state,
                    turn_id,
                    llm_raw,
                    assistant_text,
                )
                listening_status_events = await self._transition_session(
                    state, SessionStatus.LISTENING, "agent.generate_reply.complete"
                )
                state.complete_turn(user_text=message.user_text, assistant_text=assistant_text)
                return [
                    *interrupt_events,
                    *route_events,
                    *thinking_status_events,
                    *llm_events,
                    *tts_events,
                    *listening_status_events,
                ]

            queue_event = self._queue_turn(
                state,
                text=message.user_text,
                source="agent.generate_reply",
                stt_final_at=asyncio.get_running_loop().time(),
            )
            queue_event.generation_id = self._response_generation_ids.get(state.session_id)
            return [queue_event]

        if state.active_turn_id is None:
            turn_id = state.begin_turn()
            self._turn_start_times[state.session_id] = asyncio.get_running_loop().time()
        else:
            turn_id = state.active_turn_id

        route_events, decision = await self._route_text(state, turn_id, message.user_text)
        thinking_status_events = await self._transition_session(
            state,
            SessionStatus.THINKING,
            "agent.generate_reply",
        )

        if emit is not None:
            self._trace_start(state)
            generation_id = self._new_generation_id()
            _set_generation_for_events(route_events, generation_id)
            _set_generation_for_events(thinking_status_events, generation_id)
            await _emit_conversation_events(emit, route_events)
            await _emit_conversation_events(emit, thinking_status_events)
            await self._start_response_task(
                state,
                turn_id,
                message.user_text,
                decision,
                emit,
                generation_id=generation_id,
            )
            return []

        llm_raw, llm_events, assistant_text = await self._generate_llm_response(
            state,
            turn_id,
            message.user_text,
            decision,
        )
        tts_events = await self._generate_tts_response(
            state,
            turn_id,
            llm_raw,
            assistant_text,
        )
        listening_status_events = await self._transition_session(
            state, SessionStatus.LISTENING, "agent.generate_reply.complete"
        )
        state.complete_turn(user_text=message.user_text, assistant_text=assistant_text)
        return [
            *route_events,
            *thinking_status_events,
            *llm_events,
            *tts_events,
            *listening_status_events,
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
        _merge_runtime_config_update(state.metadata, message.config)
        state.touch()
        return []

    async def _transition_session(
        self,
        state: SessionState,
        status: SessionStatus,
        reason: str,
    ) -> list[ConversationEvent]:
        if not can_transition(state.status, status):
            return []
        updated = await self._sessions.update(
            state.session_id,
            SessionTransition(to_status=status, reason=reason),
        )
        return [
            SessionStatusEvent(
                updated.session_id,
                updated.status,
                turn_id=updated.active_turn_id,
                reason=reason,
            )
        ]

    def _schedule_fast_ack_if_enabled(
        self,
        state: SessionState,
        turn_id: str | None,
        user_text: str | None,
        generation_id: str,
        emit: ConversationEventEmitter,
    ) -> None:
        activity_marker = self._last_user_activity_at.get(
            state.session_id,
            asyncio.get_running_loop().time(),
        )

        async def run() -> None:
            try:
                await self._emit_fast_ack_if_enabled(
                    state,
                    turn_id,
                    user_text,
                    generation_id,
                    emit,
                    activity_marker=activity_marker,
                )
            except Exception:
                logger.debug(
                    "Fast ack task failed session=%s turn=%s generation=%s",
                    state.session_id,
                    turn_id,
                    generation_id,
                    exc_info=True,
                )

        asyncio.create_task(
            run(),
            name=f"fast-ack:{state.session_id}:{turn_id or 'none'}:{generation_id}",
        )

    async def _emit_fast_ack_if_enabled(
        self,
        state: SessionState,
        turn_id: str | None,
        user_text: str | None,
        generation_id: str,
        emit: ConversationEventEmitter,
        *,
        activity_marker: float,
    ) -> None:
        if self._tts_service is None or not user_text or not user_text.strip():
            return

        runtime_config = _effective_runtime_config(state, self._config)
        if bool(runtime_config.llm.enable_fast_ack) is not True:
            return

        await asyncio.sleep(FAST_ACK_HOLDOFF_SECONDS)

        latest_activity = self._last_user_activity_at.get(state.session_id, activity_marker)
        if latest_activity > activity_marker:
            return
        if not self._is_active_generation(state.session_id, generation_id):
            return

        engine_id = state.engine_selection.tts
        if not self._tts_service.is_available(engine_id):
            return

        request = TtsRequest(
            session_id=state.session_id,
            turn_id=turn_id or "",
            text=FAST_ACK_TEXT,
            audio_format=_tts_audio_format(state),
            voice_id=_tts_voice_id(state),
            language=_session_language(state),
            metadata={"text_segment": FAST_ACK_TEXT},
        )
        try:
            stream = await self._tts_service.stream(request, engine_id=engine_id)
        except OpenVoiceError as error:
            logger.warning(
                "Fast ack TTS stream failed session=%s turn=%s error=%s",
                state.session_id,
                turn_id,
                error.message,
            )
            return

        async for item in stream:
            if not self._is_active_generation(state.session_id, generation_id):
                return
            if item.kind is TtsEventKind.AUDIO_CHUNK and item.audio_chunk is not None:
                event = TtsChunkEvent(
                    state.session_id,
                    item.audio_chunk,
                    turn_id=turn_id,
                    text_segment=FAST_ACK_TEXT,
                )
                event.generation_id = generation_id
                await emit(event)
            if item.kind is TtsEventKind.COMPLETED:
                return

    async def _build_feedback_tts_events(
        self,
        state: SessionState,
        turn_id: str | None,
        *,
        text: str,
        reason: str,
        generation_id: str | None = None,
    ) -> list[ConversationEvent]:
        if self._tts_service is None or not self._tts_service.is_available(
            state.engine_selection.tts
        ):
            listening_only_events = await self._transition_session(
                state,
                SessionStatus.LISTENING,
                f"{reason}.no_tts",
            )
            if not listening_only_events and state.status is SessionStatus.LISTENING:
                listening_only_events = [
                    SessionStatusEvent(
                        state.session_id,
                        SessionStatus.LISTENING,
                        turn_id=state.active_turn_id,
                        reason=f"{reason}.no_tts",
                    )
                ]
            _set_generation_for_events(listening_only_events, generation_id)
            return listening_only_events

        engine_id = state.engine_selection.tts

        request = TtsRequest(
            session_id=state.session_id,
            turn_id=turn_id or "",
            text=text,
            audio_format=_tts_audio_format(state),
            voice_id=_tts_voice_id(state),
            language=_session_language(state),
            metadata={"text_segment": text},
        )

        try:
            stream = await self._tts_service.stream(request, engine_id=engine_id)
        except OpenVoiceError:
            return []

        events: list[ConversationEvent] = []
        speaking_status_events = await self._transition_session(
            state,
            SessionStatus.SPEAKING,
            reason,
        )
        _set_generation_for_events(speaking_status_events, generation_id)
        events.extend(speaking_status_events)

        total_duration_ms = 0.0
        saw_duration = False
        emitted_audio = False
        async for item in stream:
            if generation_id is not None and not self._is_active_generation(
                state.session_id, generation_id
            ):
                return events
            if item.kind is TtsEventKind.AUDIO_CHUNK and item.audio_chunk is not None:
                tts_chunk_event = TtsChunkEvent(
                    state.session_id,
                    item.audio_chunk,
                    turn_id=turn_id,
                    text_segment=item.text_segment or text,
                )
                tts_chunk_event.generation_id = generation_id
                events.append(tts_chunk_event)
                emitted_audio = True
                continue
            if item.kind is TtsEventKind.COMPLETED and item.duration_ms is not None:
                total_duration_ms += item.duration_ms
                saw_duration = True

        if emitted_audio:
            tts_done_event = TtsCompletedEvent(
                state.session_id,
                turn_id=turn_id,
                duration_ms=total_duration_ms if saw_duration else None,
            )
            tts_done_event.generation_id = generation_id
            events.append(tts_done_event)

        listening_status_events = await self._transition_session(
            state,
            SessionStatus.LISTENING,
            f"{reason}.complete",
        )
        if not listening_status_events and state.status is SessionStatus.LISTENING:
            listening_status_events = [
                SessionStatusEvent(
                    state.session_id,
                    SessionStatus.LISTENING,
                    turn_id=state.active_turn_id,
                    reason=f"{reason}.complete",
                )
            ]
        _set_generation_for_events(listening_status_events, generation_id)
        events.extend(listening_status_events)
        return events

    async def _emit_stt_timeout_feedback(
        self,
        state: SessionState,
        turn_id: str,
        emit: ConversationEventEmitter | None,
        *,
        details: dict[str, Any],
    ) -> list[ConversationEvent]:
        timeout_ms = _safe_int(
            details.get("timeout_ms"), int(_stt_final_timeout_seconds(state) * 1000)
        )
        error = OpenVoiceError(
            code=ErrorCode.PROVIDER_ERROR,
            message="Timed out waiting for STT final transcript.",
            retryable=True,
            details={
                **details,
                "timeout_kind": "stt_final_timeout",
                "timeout_ms": timeout_ms,
            },
        )
        error_event = ErrorEvent(state.session_id, error, turn_id=turn_id)
        error_event.generation_id = self._response_generation_ids.get(state.session_id)

        listening_events = await self._transition_session(
            state,
            SessionStatus.LISTENING,
            "stt.timeout.complete",
        )
        if not listening_events and state.status is SessionStatus.LISTENING:
            listening_events = [
                SessionStatusEvent(
                    state.session_id,
                    SessionStatus.LISTENING,
                    turn_id=state.active_turn_id,
                    reason="stt.timeout.complete",
                )
            ]
        events = [error_event, *listening_events]
        if emit is not None:
            await _emit_conversation_events(emit, events)
            return []
        return events

    def _increment_client_turn_attempt(self, session_id: str, client_turn_id: str) -> int:
        attempts = self._client_turn_attempts.setdefault(session_id, {})
        next_attempt = attempts.get(client_turn_id, 0) + 1
        attempts[client_turn_id] = next_attempt
        return next_attempt

    def _clear_client_turn_attempt(self, session_id: str, client_turn_id: str) -> None:
        attempts = self._client_turn_attempts.get(session_id)
        if attempts is None:
            return
        attempts.pop(client_turn_id, None)
        if not attempts:
            self._client_turn_attempts.pop(session_id, None)

    async def _close_session(self, message: SessionCloseMessage) -> list[ConversationEvent]:
        await self._cancel_response_task(message.session_id)
        await self._sessions.close(message.session_id)
        self._turns.close(message.session_id)
        await self._reset_stt_stream(message.session_id)
        await self._reset_vad_stream(message.session_id)
        self._turn_queue.pop(message.session_id, None)
        self._active_turn_traces.pop(message.session_id, None)
        self._response_generation_ids.pop(message.session_id, None)
        self._preempted_generation_ids.pop(message.session_id, None)
        self._response_turn_ids.pop(message.session_id, None)
        self._last_interrupt_at.pop(message.session_id, None)
        self._post_interrupt_until.pop(message.session_id, None)
        self._post_interrupt_collecting.pop(message.session_id, None)
        self._post_interrupt_turns.pop(message.session_id, None)
        self._turn_start_times.pop(message.session_id, None)
        self._speech_after_interrupt.pop(message.session_id, None)
        self._last_user_activity_at.pop(message.session_id, None)
        self._tool_speech_announcements.pop(message.session_id, None)
        self._tool_search_statuses.pop(message.session_id, None)
        self._tool_search_start_announced.pop(message.session_id, None)
        self._tool_search_end_announced.pop(message.session_id, None)
        self._last_stt_final_at.pop(message.session_id, None)
        self._last_stt_final_text.pop(message.session_id, None)
        self._stt_commit_started_at.pop(message.session_id, None)
        self._barge_in_speech_started_at.pop(message.session_id, None)
        self._turn_entered_processing_at.pop(message.session_id, None)
        self._session_speech_turn_count.pop(message.session_id, None)
        self._recent_stt_partial_text.pop(message.session_id, None)
        self._client_turn_attempts.pop(message.session_id, None)
        return [SessionClosedEvent(message.session_id)]

    def _tool_progress_speech_hint(self, session_id: str, event: LlmEvent) -> str | None:
        if event.kind is not LlmEventKind.TOOL_UPDATE:
            return None

        status_raw = event.metadata.get("status") if isinstance(event.metadata, dict) else None
        status = status_raw.lower() if isinstance(status_raw, str) else ""
        if status in {"pending", "running"}:
            status_bucket = "start"
        elif status in {"completed", "done"}:
            status_bucket = "end"
        elif status in {"error", "failed"}:
            status_bucket = "error"
        else:
            return None

        call_id = event.call_id or event.tool_name or "tool"
        dedup_key = (call_id, status_bucket)
        now = asyncio.get_running_loop().time()
        session_cache = self._tool_speech_announcements.setdefault(session_id, {})

        stale = [
            key
            for key, seen_at in session_cache.items()
            if now - seen_at > TOOL_SPEECH_DEDUP_WINDOW_SECONDS
        ]
        for key in stale:
            session_cache.pop(key, None)

        if dedup_key in session_cache:
            return None
        session_cache[dedup_key] = now

        tool_name = (event.tool_name or "").lower()
        is_search = "search" in tool_name or "web" in tool_name

        if is_search:
            call_key = event.call_id or f"{tool_name or 'tool'}:{len(session_cache)}"
            search_status = self._tool_search_statuses.setdefault(session_id, {})
            search_status[call_key] = status_bucket

            if status_bucket == "start":
                if not self._tool_search_start_announced.get(session_id, False):
                    self._tool_search_start_announced[session_id] = True
                    self._tool_search_end_announced[session_id] = False
                    return "I am checking a few web sources now."
                return None

            if status_bucket in {"end", "error"}:
                if not self._tool_search_start_announced.get(session_id, False):
                    return None
                terminal = {"end", "error"}
                all_terminal = bool(search_status) and all(
                    value in terminal for value in search_status.values()
                )
                if all_terminal and not self._tool_search_end_announced.get(session_id, False):
                    self._tool_search_end_announced[session_id] = True
                    source_count = len(search_status)
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

    async def _cancel_response_task(
        self, session_id: str, *, await_timeout_s: float = 0.25
    ) -> None:
        task = self._response_tasks.pop(session_id, None)
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError, asyncio.TimeoutError):
            await asyncio.wait_for(task, timeout=max(0.01, await_timeout_s))

    def _is_active_generation(self, session_id: str, generation_id: str) -> bool:
        active_generation_id = self._response_generation_ids.get(session_id)
        if active_generation_id != generation_id:
            return False
        preempted_generation_id = self._preempted_generation_ids.get(session_id)
        if preempted_generation_id == generation_id:
            return False
        return True

    def _preempt_active_generation(self, session_id: str) -> str | None:
        generation_id = self._response_generation_ids.get(session_id)
        if generation_id is None:
            return None
        self._preempted_generation_ids[session_id] = generation_id
        return generation_id

    def _has_active_generation(self, session_id: str) -> bool:
        """Check if there's any active response generation in progress.

        This allows interruption detection at any point during response generation,
        regardless of the current session status (THINKING, SPEAKING, or even LISTENING
        during the routing phase).
        """
        generation_id = self._response_generation_ids.get(session_id)
        if generation_id is None:
            return False
        return self._preempted_generation_ids.get(session_id) != generation_id

    def _should_handle_interruption(self, state: SessionState) -> bool:
        """Determine if we should handle interruption for the current session state.

        Interruption should be handled at ANY point during active response generation:
        - THINKING: LLM is generating a response
        - SPEAKING: TTS is streaming audio
        - LOADING: Model/engine is loading
        - LISTENING (with active generation): During routing phase

        This ensures users can interrupt the SDK at any point, consistent with
        LiveKit Agents' interruption model.
        """
        session_id = state.session_id
        current_time = asyncio.get_running_loop().time()

        # Gather all decision factors for structured logging
        decision_context = {
            "session_id": session_id,
            "status": state.status.value,
            "active_turn_id": state.active_turn_id,
            "has_active_generation": self._has_active_generation(session_id),
        }

        policy = _turn_queue_policy(state)
        send_now_policy = policy == TURN_QUEUE_POLICY_SEND_NOW
        cooldown_cfg = _interruption_config(state)
        cooldown_seconds = cooldown_cfg.get("cooldown_ms", 1000) / 1000.0

        # Check 1: Continuous speech protection (2 second window after interrupt)
        last_interrupt = self._last_interrupt_at.get(session_id, 0)
        time_since_interrupt = current_time - last_interrupt
        decision_context["time_since_interrupt"] = time_since_interrupt
        decision_context["in_continuous_speech_window"] = time_since_interrupt < 2.0

        if time_since_interrupt < 2.0 and not send_now_policy:
            logger.info(
                "Interruption decision: BLOCKED - continuous speech protection",
                extra={
                    **decision_context,
                    "reason": "continuous_speech_protection",
                    "time_remaining": 2.0 - time_since_interrupt,
                },
            )
            return False

        # Check 2: Minimum turn duration (1 second)
        turn_start_time = self._turn_start_times.get(session_id)
        if turn_start_time is not None:
            turn_duration = current_time - turn_start_time
            decision_context["turn_duration"] = turn_duration
            decision_context["turn_too_young"] = turn_duration < 1.0

            if (
                turn_duration < 1.0
                and state.status is not SessionStatus.THINKING
                and not send_now_policy
            ):
                logger.info(
                    "Interruption decision: BLOCKED - turn too young",
                    extra={
                        **decision_context,
                        "reason": "minimum_turn_duration",
                        "time_remaining": 1.0 - turn_duration,
                    },
                )
                return False

        # Check 3: Post-STT-final protection (3 second window after speech ends)
        # Prevents late audio chunks from triggering immediate interrupt when turn enters SPEAKING
        # CRITICAL: Do NOT apply this protection during THINKING - user must always be able
        # to interrupt the LLM by speaking. The LLM thinking should be interruptible at any time.
        last_stt_final = self._last_stt_final_at.get(session_id, 0)
        time_since_stt_final = current_time - last_stt_final
        decision_context["time_since_stt_final"] = time_since_stt_final
        decision_context["in_post_stt_final_window"] = time_since_stt_final < 3.0

        if (
            time_since_stt_final < 3.0
            and state.status is not SessionStatus.THINKING
            and not send_now_policy
        ):
            logger.info(
                "Interruption decision: BLOCKED - post-STT-final protection",
                extra={
                    **decision_context,
                    "reason": "post_stt_final_protection",
                    "time_remaining": 3.0 - time_since_stt_final,
                },
            )
            return False

        # Check 4: Cooldown period
        in_cooldown = time_since_interrupt < cooldown_seconds
        decision_context["cooldown_seconds"] = cooldown_seconds
        decision_context["in_cooldown"] = in_cooldown

        if in_cooldown and not send_now_policy:
            logger.info(
                "Interruption decision: BLOCKED - cooldown active",
                extra={
                    **decision_context,
                    "reason": "cooldown_active",
                    "time_remaining": cooldown_seconds - time_since_interrupt,
                },
            )
            return False

        # Check 5: Post-interrupt turn tracking
        post_interrupt_data = self._post_interrupt_turns.get(session_id)
        if post_interrupt_data is not None:
            post_turn_id, post_turn_time = post_interrupt_data
            time_since_post_turn = current_time - post_turn_time
            is_post_interrupt_turn = state.active_turn_id == post_turn_id
            decision_context["is_post_interrupt_turn"] = is_post_interrupt_turn
            decision_context["time_since_post_turn"] = time_since_post_turn

            if is_post_interrupt_turn and time_since_post_turn < 5.0 and not send_now_policy:
                logger.info(
                    "Interruption decision: BLOCKED - post-interrupt turn protection",
                    extra={
                        **decision_context,
                        "reason": "post_interrupt_turn_protection",
                        "post_interrupt_turn_id": post_turn_id,
                        "time_remaining": 5.0 - time_since_post_turn,
                    },
                )
                return False

        # Check 6: Continuous speech from interrupt tracking
        # If speech started shortly after an interrupt and is still flowing through the pipeline,
        # don't interrupt - it's continuous speech from the previous turn
        speech_start_after_interrupt = self._speech_after_interrupt.get(session_id)
        if speech_start_after_interrupt is not None:
            time_since_speech_started = current_time - speech_start_after_interrupt
            decision_context["time_since_speech_started"] = time_since_speech_started
            decision_context["in_continuous_speech_flow"] = time_since_speech_started < 10.0

            if time_since_speech_started < 10.0 and not send_now_policy:
                logger.info(
                    "Interruption decision: BLOCKED - continuous speech from interrupt",
                    extra={
                        **decision_context,
                        "reason": "continuous_speech_flow",
                        "speech_start_after_interrupt": speech_start_after_interrupt,
                        "time_remaining": 10.0 - time_since_speech_started,
                    },
                )
                return False

        # Check 7: Recently entered processing protection
        # Block interrupts for 3 seconds after turn enters SPEAKING to prevent
        # false interrupts from late audio chunks that arrive right after TTS starts
        # CRITICAL FIX: Don't block during THINKING - user should be able to interrupt LLM by speaking
        turn_entered_processing = self._turn_entered_processing_at.get(session_id)
        if (
            turn_entered_processing is not None
            and state.status is SessionStatus.SPEAKING
            and not send_now_policy
        ):
            time_in_processing = current_time - turn_entered_processing
            decision_context["time_in_processing"] = time_in_processing
            decision_context["recently_entered_processing"] = time_in_processing < 3.0

            if time_in_processing < 3.0:
                logger.info(
                    "Interruption decision: BLOCKED - recently entered SPEAKING",
                    extra={
                        **decision_context,
                        "reason": "recently_entered_speaking",
                        "time_remaining": 3.0 - time_in_processing,
                    },
                )
                return False

        # Check 8: Status-based interruption eligibility
        can_interrupt_by_status = state.status in {
            SessionStatus.THINKING,
            SessionStatus.SPEAKING,
            SessionStatus.LOADING,
        }
        decision_context["can_interrupt_by_status"] = can_interrupt_by_status

        if can_interrupt_by_status:
            logger.info(
                "Interruption decision: ALLOWED - eligible status",
                extra={
                    **decision_context,
                    "reason": "eligible_status",
                    "status": state.status.value,
                },
            )
            return True

        # Check 9: Active generation override (for routing phase)
        can_interrupt_by_generation = (
            state.status is SessionStatus.LISTENING and self._has_active_generation(session_id)
        )
        decision_context["can_interrupt_by_generation"] = can_interrupt_by_generation

        if can_interrupt_by_generation:
            logger.info(
                "Interruption decision: ALLOWED - active generation during routing",
                extra={
                    **decision_context,
                    "reason": "active_generation_routing",
                    "generation_id": self._response_generation_ids.get(session_id),
                },
            )
            return True

        # Default: not eligible for interruption
        logger.info(
            "Interruption decision: BLOCKED - not eligible",
            extra={
                **decision_context,
                "reason": "not_eligible",
            },
        )
        return False

    def _new_generation_id(self) -> str:
        return f"gen_{uuid4().hex}"

    def _trace_start(
        self,
        state: SessionState,
        *,
        queued: _QueuedUserTurn | None = None,
    ) -> None:
        trace = _TurnTrace(started_at=asyncio.get_running_loop().time())
        if queued is not None:
            trace.queue_enqueued_at = queued.enqueued_at
            trace.stt_final_at = queued.stt_final_at
        self._active_turn_traces[state.session_id] = trace

    def _trace_mark_stt_final(self, state: SessionState) -> None:
        trace = self._active_turn_traces.get(state.session_id)
        if trace is None:
            self._trace_start(state)
            trace = self._active_turn_traces.get(state.session_id)
        if trace is None:
            return
        if trace.stt_final_at is None:
            trace.stt_final_at = asyncio.get_running_loop().time()

    def _trace_mark_route_selected(self, state: SessionState) -> None:
        trace = self._active_turn_traces.get(state.session_id)
        if trace is None:
            self._trace_start(state)
            trace = self._active_turn_traces.get(state.session_id)
        if trace is None:
            return
        if trace.route_selected_at is None:
            trace.route_selected_at = asyncio.get_running_loop().time()

    async def _trace_mark_completed(
        self,
        state: SessionState,
        *,
        turn_id: str | None,
        cancelled: bool,
        reason: str | None,
        llm_first_delta_at: float | None,
        tts_first_chunk_at: float | None,
        completed_at: float | None = None,
    ) -> TurnMetricsEvent | None:
        trace = self._active_turn_traces.pop(state.session_id, None)
        if trace is None:
            return None

        now = completed_at if completed_at is not None else asyncio.get_running_loop().time()
        trace.completed_at = now
        if cancelled:
            trace.cancelled = True
        if reason is not None:
            trace.reason = reason

        first_llm = llm_first_delta_at or trace.first_llm_delta_at
        first_tts = tts_first_chunk_at or trace.first_tts_chunk_at

        return TurnMetricsEvent(
            state.session_id,
            turn_id=turn_id,
            queue_delay_ms=_duration_ms(trace.queue_enqueued_at, trace.started_at),
            stt_to_route_ms=_duration_ms(trace.stt_final_at, trace.route_selected_at),
            route_to_llm_first_delta_ms=_duration_ms(trace.route_selected_at, first_llm),
            llm_first_delta_to_tts_first_chunk_ms=_duration_ms(first_llm, first_tts),
            stt_to_tts_first_chunk_ms=_duration_ms(trace.stt_final_at, first_tts),
            turn_to_first_llm_delta_ms=_duration_ms(trace.started_at, first_llm),
            turn_to_complete_ms=_duration_ms(trace.started_at, trace.completed_at),
            cancelled=trace.cancelled,
            reason=trace.reason,
        )

    def _queue_turn(
        self,
        state: SessionState,
        *,
        text: str,
        source: str,
        stt_final_at: float | None,
    ) -> TurnQueuedEvent:
        policy = _turn_queue_policy(state)
        queued = _QueuedUserTurn(
            text=text,
            enqueued_at=asyncio.get_running_loop().time(),
            source=source,
            policy=policy,
            stt_final_at=stt_final_at,
        )
        queue = self._turn_queue.setdefault(state.session_id, [])
        queue.append(queued)
        logger.info(
            "Queued turn session=%s source=%s policy=%s queue_size=%s",
            state.session_id,
            source,
            policy,
            len(queue),
        )
        return TurnQueuedEvent(
            state.session_id,
            queue_size=len(queue),
            turn_id=state.active_turn_id,
            source=source,
            policy=policy,
        )

    def _dequeue_turn(self, session_id: str) -> _QueuedUserTurn | None:
        queue = self._turn_queue.get(session_id)
        if not queue:
            return None
        item = queue.pop(0)
        if not queue:
            self._turn_queue.pop(session_id, None)
        return item

    async def _process_queued_turns(
        self,
        state: SessionState,
        *,
        emit: ConversationEventEmitter,
    ) -> None:
        while True:
            queued = self._dequeue_turn(state.session_id)
            if queued is None:
                return
            if state.status not in {
                SessionStatus.LISTENING,
                SessionStatus.READY,
                SessionStatus.TRANSCRIBING,
            }:
                queue = self._turn_queue.setdefault(state.session_id, [])
                queue.insert(0, queued)
                return

            if queued.text.strip() == "":
                continue

            if state.active_turn_id is None:
                turn_id = state.begin_turn()
                self._turn_start_times[state.session_id] = asyncio.get_running_loop().time()
            else:
                turn_id = state.active_turn_id

            self._trace_start(state, queued=queued)
            route_events, decision = await self._route_text(state, turn_id, queued.text)
            generation_id = self._new_generation_id()
            _set_generation_for_events(route_events, generation_id)
            await _emit_conversation_events(emit, route_events)
            thinking_status_events = await self._transition_session(
                state,
                SessionStatus.THINKING,
                "llm.generating.queued",
            )
            _set_generation_for_events(thinking_status_events, generation_id)
            await _emit_conversation_events(emit, thinking_status_events)
            await self._start_response_task(
                state,
                turn_id,
                queued.text,
                decision,
                emit,
                generation_id=generation_id,
            )
            return

    async def _start_response_task(
        self,
        state: SessionState,
        turn_id: str,
        user_text: str | None,
        decision: RouteDecision | None,
        emit: ConversationEventEmitter,
        *,
        generation_id: str | None = None,
    ) -> None:
        await self._cancel_response_task(state.session_id, await_timeout_s=0.2)
        generation_id = generation_id or self._new_generation_id()
        self._response_generation_ids[state.session_id] = generation_id
        self._preempted_generation_ids.pop(state.session_id, None)
        self._response_turn_ids[state.session_id] = turn_id

        task: asyncio.Task[None]

        async def runner() -> None:
            assistant_text: str | None = None
            first_llm_delta_at: float | None = None
            first_tts_chunk_at: float | None = None
            was_cancelled = False
            completion_reason = "completed"
            try:
                logger.info(
                    "Starting realtime response task session=%s turn=%s generation=%s",
                    state.session_id,
                    turn_id,
                    generation_id,
                )
                (
                    assistant_text,
                    first_llm_delta_at,
                    first_tts_chunk_at,
                ) = await self._stream_llm_and_tts_response(
                    state,
                    turn_id,
                    user_text,
                    decision,
                    emit,
                    generation_id=generation_id,
                )
                if self._is_active_generation(state.session_id, generation_id):
                    listening_status_events = await self._transition_session(
                        state,
                        SessionStatus.LISTENING,
                        "response.complete",
                    )
                    _set_generation_for_events(listening_status_events, generation_id)
                    await _emit_conversation_events(emit, listening_status_events)
                    logger.info(
                        "Completed realtime response task session=%s turn=%s generation=%s",
                        state.session_id,
                        turn_id,
                        generation_id,
                    )
            except asyncio.CancelledError:
                was_cancelled = True
                completion_reason = "cancelled"
                logger.info(
                    "Cancelled realtime response task session=%s turn=%s generation=%s",
                    state.session_id,
                    turn_id,
                    generation_id,
                )
                raise
            except Exception as exc:
                if isinstance(exc, TimeoutError):
                    completion_reason = str(exc).strip() or "llm_timeout"
                error = OpenVoiceError(
                    code=ErrorCode.PROVIDER_ERROR,
                    message=f"Realtime response task failed: {exc}",
                    retryable=False,
                    details={"session_id": state.session_id, "turn_id": turn_id},
                )
                logger.exception(
                    "Realtime response task failed session=%s turn=%s generation=%s",
                    state.session_id,
                    turn_id,
                    generation_id,
                )
                if self._is_active_generation(state.session_id, generation_id):
                    timeout_kind = str(exc).strip() if isinstance(exc, TimeoutError) else None
                    raw_message = timeout_kind or str(exc)
                    error_text = _truncate_error_for_speech(raw_message)
                    details: dict[str, Any] = {
                        "session_id": state.session_id,
                        "turn_id": turn_id,
                    }
                    if timeout_kind is not None:
                        details["timeout_kind"] = timeout_kind
                        details["first_delta_timeout_ms"] = int(
                            _llm_first_delta_timeout_seconds(state) * 1000
                        )
                        details["total_timeout_ms"] = int(_llm_total_timeout_seconds(state) * 1000)

                    llm_error_event = LlmErrorEvent(
                        state.session_id,
                        turn_id=turn_id,
                        code=ErrorCode.PROVIDER_ERROR.value,
                        message=error_text,
                        retryable=True,
                        details=details,
                    )
                    llm_error_event.generation_id = generation_id
                    await emit(llm_error_event)

                    error_event = ErrorEvent(state.session_id, error, turn_id=turn_id)
                    error_event.generation_id = generation_id
                    await emit(error_event)

                    # TODO: Replace raw exception speech with sanitized user-safe mapped messages.
                    feedback_events = await self._build_feedback_tts_events(
                        state,
                        turn_id,
                        text=f"{LLM_RAW_ERROR_PREFIX}{error_text}",
                        reason="llm.error",
                        generation_id=generation_id,
                    )
                    await _emit_conversation_events(emit, feedback_events)

                    llm_done_event = LlmPhaseEvent(
                        state.session_id,
                        phase=LlmPhase.DONE,
                        turn_id=turn_id,
                    )
                    llm_done_event.generation_id = generation_id
                    await emit(llm_done_event)

                    listening_status_events = await self._transition_session(
                        state,
                        SessionStatus.LISTENING,
                        "llm.error.recovery",
                    )
                    _set_generation_for_events(listening_status_events, generation_id)
                    await _emit_conversation_events(emit, listening_status_events)
            finally:
                active_generation = self._is_active_generation(state.session_id, generation_id)
                completion_time = asyncio.get_running_loop().time()
                if state.active_turn_id == turn_id:
                    state.complete_turn(user_text=user_text, assistant_text=assistant_text)
                current = self._response_tasks.get(state.session_id)
                if current is task:
                    self._response_tasks.pop(state.session_id, None)
                if active_generation:
                    self._response_generation_ids.pop(state.session_id, None)
                    self._preempted_generation_ids.pop(state.session_id, None)
                    self._response_turn_ids.pop(state.session_id, None)
                if active_generation and was_cancelled:
                    self._active_turn_traces.pop(state.session_id, None)
                    self._turn_queue.pop(state.session_id, None)
                    self._tool_search_statuses.pop(state.session_id, None)
                    self._tool_search_start_announced.pop(state.session_id, None)
                    self._tool_search_end_announced.pop(state.session_id, None)
                if (
                    was_cancelled
                    and active_generation
                    and state.status
                    in {
                        SessionStatus.TRANSCRIBING,
                        SessionStatus.THINKING,
                        SessionStatus.SPEAKING,
                    }
                ):
                    self._response_generation_ids.pop(state.session_id, None)
                    self._preempted_generation_ids.pop(state.session_id, None)
                    self._response_turn_ids.pop(state.session_id, None)
                    cancelled_status_events = await self._transition_session(
                        state,
                        SessionStatus.LISTENING,
                        "response.cancelled",
                    )
                    _set_generation_for_events(cancelled_status_events, generation_id)
                    await _emit_conversation_events(emit, cancelled_status_events)

                    cancelled_metrics = TurnMetricsEvent(
                        state.session_id,
                        turn_id=turn_id,
                        turn_to_complete_ms=None,
                        cancelled=True,
                        reason="cancelled",
                    )
                    cancelled_metrics.generation_id = generation_id
                    await emit(cancelled_metrics)
                    self._active_turn_traces.pop(state.session_id, None)
                    self._turn_queue.pop(state.session_id, None)
                    return

                metrics_event = await self._trace_mark_completed(
                    state,
                    turn_id=turn_id,
                    cancelled=was_cancelled,
                    reason=completion_reason,
                    llm_first_delta_at=first_llm_delta_at,
                    tts_first_chunk_at=first_tts_chunk_at,
                    completed_at=completion_time,
                )
                if metrics_event is not None:
                    logger.info(
                        "Turn metrics session=%s turn=%s generation=%s queue_delay_ms=%s stt_to_route_ms=%s route_to_llm_first_delta_ms=%s llm_first_delta_to_tts_first_chunk_ms=%s stt_to_tts_first_chunk_ms=%s turn_to_first_llm_delta_ms=%s turn_to_complete_ms=%s cancelled=%s reason=%s",
                        state.session_id,
                        turn_id,
                        generation_id,
                        metrics_event.queue_delay_ms,
                        metrics_event.stt_to_route_ms,
                        metrics_event.route_to_llm_first_delta_ms,
                        metrics_event.llm_first_delta_to_tts_first_chunk_ms,
                        metrics_event.stt_to_tts_first_chunk_ms,
                        metrics_event.turn_to_first_llm_delta_ms,
                        metrics_event.turn_to_complete_ms,
                        metrics_event.cancelled,
                        metrics_event.reason,
                    )
                    metrics_event.generation_id = generation_id
                    await emit(metrics_event)

                if not was_cancelled and active_generation:
                    await self._process_queued_turns(state, emit=emit)

                if active_generation:
                    self._active_turn_traces.pop(state.session_id, None)

        task = asyncio.create_task(
            runner(),
            name=f"realtime-response:{state.session_id}:{turn_id}:{generation_id}",
        )
        self._response_tasks[state.session_id] = task
        logger.info(
            "Spawned realtime response task session=%s turn=%s generation=%s",
            state.session_id,
            turn_id,
            generation_id,
        )

    async def _ensure_stt_stream(self, state: SessionState) -> BaseSttStream | None:
        existing = self._stt_streams.get(state.session_id)
        if existing is not None:
            return existing
        if self._stt_service is None:
            return None
        if not self._stt_service.is_available(state.engine_selection.stt):
            if state.engine_selection.stt is None:
                return None
        stream = await self._stt_service.create_stream(
            SttConfig(language=_session_language(state)),
            engine_id=state.engine_selection.stt,
        )
        self._stt_streams[state.session_id] = stream
        return stream

    async def _ensure_vad_stream(self, state: SessionState) -> BaseVadStream | None:
        existing = self._vad_streams.get(state.session_id)
        if existing is not None:
            return existing
        if self._vad_service is None:
            return None
        if not self._vad_service.is_available():
            return None
        stream = await self._vad_service.create_stream(_vad_config(state))
        self._vad_streams[state.session_id] = stream
        return stream

    async def _reset_stt_stream(self, session_id: str) -> None:
        stream = self._stt_streams.pop(session_id, None)
        if stream is not None:
            await stream.close()

    async def _reset_vad_stream(self, session_id: str) -> None:
        stream = self._vad_streams.pop(session_id, None)
        if stream is not None:
            await stream.close()

    async def _drain_stt_events(self, session_id: str, *, wait_seconds: float) -> list[SttEvent]:
        stream = self._stt_streams.get(session_id)
        if stream is None:
            return []
        return await stream.drain(wait_seconds=wait_seconds)

    async def _drain_and_discard_stt_events(self, session_id: str) -> None:
        """Drain any pending STT events and discard them. Used after interrupt to prevent stale transcripts."""
        stream = self._stt_streams.get(session_id)
        if stream is None:
            return
        # Drain with a short timeout to clear any pending events
        try:
            await stream.drain(wait_seconds=0.02)
        except Exception:
            # Ignore errors during cleanup
            pass

    async def _push_vad_audio(
        self,
        stream: BaseVadStream | None,
        chunk: AudioChunk,
    ) -> list[VadEvent]:
        if stream is None:
            return []
        result = await stream.push_audio(chunk)
        return result.events
        result = await stream.push_audio(chunk)
        events = getattr(result, "events", None)
        logger.debug(
            "_push_vad_audio: result type=%s, events type=%s, events=%s",
            type(result).__name__,
            type(events).__name__ if events is not None else "None",
            events if isinstance(events, list) else str(events)[:100],
        )
        if isinstance(events, list):
            return events
        logger.error("_push_vad_audio got non-list events: type=%s value=%s", type(events), events)
        return []

    async def _drain_stt_commit_events(
        self,
        session_id: str,
        *,
        timeout_seconds: float,
    ) -> list[SttEvent]:
        events: list[SttEvent] = []
        saw_final = False
        deadline = asyncio.get_running_loop().time() + timeout_seconds

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return events

            batch = await self._drain_stt_events(
                session_id,
                wait_seconds=min(0.25, remaining),
            )
            if batch:
                events.extend(batch)
                saw_final = saw_final or any(item.kind is SttEventKind.FINAL for item in batch)
                if saw_final:
                    self._trace_mark_stt_final(state)
                continue

            if saw_final:
                return events

    async def _route_text(
        self,
        state: SessionState,
        turn_id: str | None,
        text: str | None,
    ) -> tuple[list[ConversationEvent], RouteDecision | None]:
        turn_matches = state.active_turn_id == turn_id
        logger.info(
            "Route request session=%s active_turn=%s request_turn=%s turn_matches=%s text_present=%s",
            state.session_id,
            state.active_turn_id,
            turn_id,
            turn_matches,
            bool(text and text.strip()),
        )
        router_mode = _router_mode(state)

        if text is None or not text.strip() or self._router_service is None:
            target = _fallback_route_target(state, self._config)
            if target is None:
                return [], None
            logger.info(
                "Using fallback route session=%s reason=no_text_or_router", state.session_id
            )
            events, decision = _fallback_route_selection(
                state.session_id,
                turn_id,
                target,
                reason="Router unavailable for this turn; using configured fallback route target.",
            )
            self._trace_mark_route_selected(state)
            return events, decision

        if router_mode in {"disabled", "fallback_only"}:
            target = _fallback_route_target(state, self._config)
            if target is None:
                return [], None
            logger.info(
                "Using fallback route session=%s reason=router_mode_%s",
                state.session_id,
                router_mode,
            )
            events, decision = _fallback_route_selection(
                state.session_id,
                turn_id,
                target,
                reason=f"Router mode '{router_mode}' active; using configured fallback route target.",
            )
            self._trace_mark_route_selected(state)
            return events, decision
        if not self._router_service.is_available(state.engine_selection.router):
            target = _fallback_route_target(state, self._config)
            if target is None:
                return [], None
            logger.info(
                "Using fallback route session=%s reason=router_unavailable", state.session_id
            )
            events, decision = _fallback_route_selection(
                state.session_id,
                turn_id,
                target,
                reason="Router backend unavailable; using configured fallback route target.",
            )
            self._trace_mark_route_selected(state)
            return events, decision

        targets = _route_targets(state, self._config)
        logger.info(
            "Routing session=%s router=%s target_count=%s text=%s",
            state.session_id,
            state.engine_selection.router,
            len(targets),
            text[:120],
        )
        timeout_seconds = _router_timeout_seconds(state)
        request = RouteRequest(
            session_id=state.session_id,
            turn_id=turn_id or "",
            user_text=text,
            available_targets=targets,
            metadata=dict(state.metadata),
        )

        try:
            decision = await asyncio.wait_for(
                self._router_service.route(
                    request,
                    engine_id=state.engine_selection.router,
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            target = _fallback_route_target(state, self._config)
            if target is None:
                return [], None
            timeout_ms = int(timeout_seconds * 1000)
            logger.warning(
                "Router timeout session=%s timeout_ms=%s; using fallback route",
                state.session_id,
                timeout_ms,
            )
            events, decision = _fallback_route_selection(
                state.session_id,
                turn_id,
                target,
                reason=f"Router timed out after {timeout_ms} ms; using configured fallback route target.",
            )
            self._trace_mark_route_selected(state)
            return events, decision
        except Exception as exc:
            target = _fallback_route_target(state, self._config)
            if target is None:
                return [], None
            logger.warning(
                "Router failed session=%s error=%s; using fallback route",
                state.session_id,
                exc,
            )
            events, decision = _fallback_route_selection(
                state.session_id,
                turn_id,
                target,
                reason=f"Router error: {exc}. Using configured fallback route target.",
            )
            self._trace_mark_route_selected(state)
            return events, decision

        logger.info(
            "Route decision session=%s route=%s provider=%s model=%s confidence=%s",
            state.session_id,
            decision.route_name,
            decision.provider,
            decision.model,
            decision.confidence,
        )
        event: ConversationEvent = RouteSelectedEvent(
            state.session_id,
            decision.router_id,
            turn_id=turn_id,
            route_name=decision.route_name,
            llm_engine_id=decision.llm_engine_id,
            provider=decision.provider,
            model=decision.model,
            profile_id=decision.profile_id,
            reason=decision.reason,
            confidence=decision.confidence,
        )
        self._trace_mark_route_selected(state)
        return [event], decision

    async def _generate_llm_response(
        self,
        state: SessionState,
        turn_id: str | None,
        user_text: str | None,
        decision: RouteDecision | None,
    ) -> tuple[list[LlmEvent], list[ConversationEvent], str | None]:
        if (
            user_text is None
            or not user_text.strip()
            or decision is None
            or self._llm_service is None
        ):
            return [], [], None

        engine_id = decision.llm_engine_id or state.engine_selection.llm
        if not self._llm_service.is_available(engine_id):
            return [], [], None

        config = _effective_runtime_config(state, self._config)
        request = LlmRequest(
            session_id=state.session_id,
            turn_id=turn_id or "",
            messages=[LlmMessage(role=LlmRole.USER, content=user_text)],
            provider=decision.provider,
            model=decision.model,
            system_prompt=config.llm.system_prompt,
            tools=config.llm.tools,
            metadata={
                "additional_instructions": config.llm.additional_instructions,
                "opencode_mode": config.llm.opencode_mode,
                "opencode_force_system_override": config.llm.opencode_force_system_override,
                "route_name": decision.route_name,
                "profile_id": decision.profile_id,
            },
        )

        try:
            stream = self._llm_service.stream(request, engine_id=engine_id)
            llm_events: list[LlmEvent] = []
            async for event in stream:
                llm_events.append(event)
        except OpenVoiceError as error:
            return [], [ErrorEvent(state.session_id, error, turn_id=turn_id)], None

        return (
            llm_events,
            _conversation_events_from_llm(state.session_id, turn_id, llm_events),
            _assistant_text(llm_events),
        )

    async def _generate_tts_response(
        self,
        state: SessionState,
        turn_id: str | None,
        llm_events: list[LlmEvent],
        assistant_text: str | None,
    ) -> list[ConversationEvent]:
        speech_text = _speech_text(llm_events) or assistant_text
        if speech_text is None or not speech_text.strip() or self._tts_service is None:
            return []

        # Strip markdown/symbols that TTS would read aloud as words
        speech_text = strip_tts_symbols(speech_text)

        engine_id = state.engine_selection.tts
        if not self._tts_service.is_available(engine_id):
            return []

        speaking_status_events = await self._transition_session(
            state,
            SessionStatus.SPEAKING,
            "tts.generating",
        )
        request = TtsRequest(
            session_id=state.session_id,
            turn_id=turn_id or "",
            text=speech_text,
            audio_format=_tts_audio_format(state),
            voice_id=_tts_voice_id(state),
            language=_session_language(state),
            metadata={
                "text_segment": speech_text,
            },
        )

        try:
            stream = await self._tts_service.stream(request, engine_id=engine_id)
            tts_events: list[TtsEvent] = []
            async for event in stream:
                tts_events.append(event)
        except OpenVoiceError as error:
            return [ErrorEvent(state.session_id, error, turn_id=turn_id)]

        return [
            *speaking_status_events,
            *_conversation_events_from_tts(
                state.session_id,
                turn_id,
                speech_text,
                tts_events,
            ),
        ]

    async def _stream_llm_and_tts_response(
        self,
        state: SessionState,
        turn_id: str | None,
        user_text: str | None,
        decision: RouteDecision | None,
        emit: ConversationEventEmitter,
        *,
        generation_id: str,
    ) -> tuple[str | None, float | None, float | None]:
        logger.info(
            "LLM stream start session=%s active_turn=%s request_turn=%s decision_provider=%s decision_model=%s user_text_len=%s",
            state.session_id,
            state.active_turn_id,
            turn_id,
            decision.provider if decision else None,
            decision.model if decision else None,
            len(user_text or ""),
        )
        if (
            user_text is None
            or not user_text.strip()
            or decision is None
            or self._llm_service is None
        ):
            return None, None, None

        engine_id = decision.llm_engine_id or state.engine_selection.llm
        if not self._llm_service.is_available(engine_id):
            return None, None, None

        trace = self._active_turn_traces.get(state.session_id)
        if trace is not None and trace.llm_start_at is None:
            trace.llm_start_at = asyncio.get_running_loop().time()

        config = _effective_runtime_config(state, self._config)
        request = LlmRequest(
            session_id=state.session_id,
            turn_id=turn_id or "",
            messages=[LlmMessage(role=LlmRole.USER, content=user_text)],
            provider=decision.provider,
            model=decision.model,
            system_prompt=config.llm.system_prompt,
            tools=config.llm.tools,
            metadata={
                "additional_instructions": config.llm.additional_instructions,
                "opencode_mode": config.llm.opencode_mode,
                "opencode_force_system_override": config.llm.opencode_force_system_override,
                "route_name": decision.route_name,
                "profile_id": decision.profile_id,
            },
        )

        hint_event = LlmReasoningDeltaEvent(
            state.session_id,
            THINKING_PROGRESS_HINT,
            turn_id=turn_id,
            part_id="runtime-thinking-hint",
        )
        hint_event.generation_id = generation_id
        await emit(hint_event)

        tts_engine_id = state.engine_selection.tts
        tts_enabled = self._tts_service is not None and self._tts_service.is_available(
            tts_engine_id
        )
        speech_queue: asyncio.Queue[str | None] = asyncio.Queue()
        buffer_lock = asyncio.Lock()
        abort_tts = asyncio.Event()
        speech_buffer = ""
        llm_events: list[LlmEvent] = []
        first_llm_delta_at: float | None = None
        first_tts_chunk_at: float | None = None
        llm_completed_text: str | None = None
        tts_task: asyncio.Task[None] | None = None

        async def flush_speech_buffer(*, force: bool) -> None:
            nonlocal speech_buffer
            async with buffer_lock:
                segments, speech_buffer = _extract_stable_speech_segments(
                    speech_buffer,
                    flush_incomplete=force,
                )
            for segment in segments:
                await speech_queue.put(segment)

        async def append_speech_text(text: str) -> None:
            nonlocal speech_buffer
            if not text:
                return
            async with buffer_lock:
                speech_buffer += text
                segments, speech_buffer = _extract_stable_speech_segments(speech_buffer)
            for segment in segments:
                await speech_queue.put(segment)

        def clear_pending_speech_queue() -> None:
            while True:
                try:
                    speech_queue.get_nowait()
                except asyncio.QueueEmpty:
                    return

        async def reconcile_speech_buffer_with_completed() -> None:
            nonlocal abort_tts, speech_buffer, tts_task
            if not tts_enabled:
                return
            if llm_completed_text is None:
                return

            completed_text = strip_tts_symbols(llm_completed_text).strip()
            if not completed_text:
                return

            streamed_text = strip_tts_symbols(_speech_text(llm_events) or "").strip()
            if _same_normalized_text(streamed_text, completed_text):
                return

            # If audio has already started we cannot safely replace spoken text.
            if first_tts_chunk_at is not None:
                logger.info(
                    "Skipping completed-text speech reconciliation after audio start session=%s turn=%s",
                    state.session_id,
                    turn_id,
                )
                return

            if tts_task is not None:
                abort_tts.set()
                tts_task.cancel()
                with suppress(asyncio.CancelledError):
                    await tts_task

            clear_pending_speech_queue()
            async with buffer_lock:
                speech_buffer = completed_text

            abort_tts = asyncio.Event()
            tts_task = asyncio.create_task(tts_worker())

        async def tts_worker() -> None:
            nonlocal first_tts_chunk_at
            if self._tts_service is None or not tts_enabled:
                return
            total_duration_ms = 0.0
            saw_duration = False
            emitted_audio = False
            try:
                while True:
                    segment = await speech_queue.get()
                    if segment is None or abort_tts.is_set():
                        if emitted_audio and not abort_tts.is_set():
                            tts_done_event = TtsCompletedEvent(
                                state.session_id,
                                turn_id=turn_id,
                                duration_ms=total_duration_ms if saw_duration else None,
                            )
                            tts_done_event.generation_id = generation_id
                            if self._is_active_generation(state.session_id, generation_id):
                                await emit(tts_done_event)
                        return

                    await _emit_conversation_events(
                        emit,
                        await self._transition_session(
                            state,
                            SessionStatus.SPEAKING,
                            "tts.generating",
                        ),
                    )
                    if not self._is_active_generation(state.session_id, generation_id):
                        return
                    request = TtsRequest(
                        session_id=state.session_id,
                        turn_id=turn_id or "",
                        text=segment,
                        audio_format=_tts_audio_format(state),
                        voice_id=_tts_voice_id(state),
                        language=_session_language(state),
                        metadata={"text_segment": segment},
                    )
                    try:
                        stream = await self._tts_service.stream(request, engine_id=tts_engine_id)
                        async for item in stream:
                            if abort_tts.is_set():
                                return
                            if (
                                item.kind is TtsEventKind.AUDIO_CHUNK
                                and item.audio_chunk is not None
                            ):
                                if first_tts_chunk_at is None:
                                    first_tts_chunk_at = asyncio.get_running_loop().time()
                                    trace = self._active_turn_traces.get(state.session_id)
                                    if trace is not None and trace.first_tts_chunk_at is None:
                                        trace.first_tts_chunk_at = first_tts_chunk_at
                                emitted_audio = True
                                tts_chunk_event = TtsChunkEvent(
                                    state.session_id,
                                    item.audio_chunk,
                                    turn_id=turn_id,
                                    text_segment=item.text_segment or segment,
                                )
                                tts_chunk_event.generation_id = generation_id
                                if self._is_active_generation(state.session_id, generation_id):
                                    await emit(tts_chunk_event)
                                continue
                            if item.kind is TtsEventKind.COMPLETED and item.duration_ms is not None:
                                total_duration_ms += item.duration_ms
                                saw_duration = True
                    except OpenVoiceError as error:
                        logger.warning(
                            "TTS stream failed session=%s turn=%s error=%s",
                            state.session_id,
                            turn_id,
                            error.message,
                        )
                        error_event = ErrorEvent(state.session_id, error, turn_id=turn_id)
                        error_event.generation_id = generation_id
                        if self._is_active_generation(state.session_id, generation_id):
                            await emit(error_event)
                        return
            except asyncio.CancelledError:
                logger.info(
                    "Cancelled TTS worker session=%s turn=%s",
                    state.session_id,
                    turn_id,
                )
                raise

        tts_task = asyncio.create_task(tts_worker()) if tts_enabled else None

        try:
            first_delta_timeout = _llm_first_delta_timeout_seconds(state)
            total_timeout = _llm_total_timeout_seconds(state)
            stream = self._llm_service.stream(request, engine_id=engine_id)
            stream_iter = stream.__aiter__()
            loop = asyncio.get_running_loop()
            started_at = loop.time()
            first_delta_deadline = started_at + first_delta_timeout
            total_deadline = started_at + total_timeout

            async def next_llm_event() -> LlmEvent:
                now = loop.time()
                if now >= total_deadline:
                    raise TimeoutError("llm_total_timeout")

                wait_timeout = max(0.01, total_deadline - now)
                if first_llm_delta_at is None:
                    wait_timeout = min(wait_timeout, max(0.01, first_delta_deadline - now))

                try:
                    return await asyncio.wait_for(stream_iter.__anext__(), timeout=wait_timeout)
                except asyncio.TimeoutError as exc:
                    if first_llm_delta_at is None and loop.time() >= first_delta_deadline:
                        raise TimeoutError("llm_first_delta_timeout") from exc
                    raise TimeoutError("llm_total_timeout") from exc

            while True:
                try:
                    item = await next_llm_event()
                except StopAsyncIteration:
                    break
                if not self._is_active_generation(state.session_id, generation_id):
                    abort_tts.set()
                    break
                logger.info(
                    "LLM stream event session=%s turn=%s kind=%s lane=%s text_len=%s",
                    state.session_id,
                    turn_id,
                    item.kind.value,
                    item.lane.value if item.lane is not None else None,
                    len(item.text or ""),
                )
                llm_events.append(item)
                if item.kind is LlmEventKind.COMPLETED and item.text:
                    llm_completed_text = item.text
                if (
                    item.kind in {LlmEventKind.RESPONSE_DELTA, LlmEventKind.REASONING_DELTA}
                    and item.text
                    and first_llm_delta_at is None
                ):
                    first_llm_delta_at = asyncio.get_running_loop().time()
                    trace = self._active_turn_traces.get(state.session_id)
                    if trace is not None and trace.first_llm_delta_at is None:
                        trace.first_llm_delta_at = first_llm_delta_at
                elif first_llm_delta_at is None and item.kind in {
                    LlmEventKind.PHASE,
                    LlmEventKind.TOOL_UPDATE,
                    LlmEventKind.USAGE,
                    LlmEventKind.SUMMARY,
                }:
                    first_delta_deadline = max(
                        first_delta_deadline,
                        asyncio.get_running_loop().time() + first_delta_timeout,
                    )

                llm_conversation_events = _conversation_events_from_llm(
                    state.session_id, turn_id, [item]
                )
                _set_generation_for_events(llm_conversation_events, generation_id)
                if self._is_active_generation(state.session_id, generation_id):
                    await _emit_conversation_events(
                        emit,
                        llm_conversation_events,
                    )
                if (
                    tts_enabled
                    and item.kind is LlmEventKind.TOOL_UPDATE
                    and self._is_active_generation(state.session_id, generation_id)
                ):
                    tool_speech_hint = self._tool_progress_speech_hint(state.session_id, item)
                    if tool_speech_hint:
                        await append_speech_text(tool_speech_hint)
                if (
                    tts_enabled
                    and item.kind is LlmEventKind.RESPONSE_DELTA
                    and item.lane is LlmOutputLane.SPEECH
                    and item.text
                ):
                    await append_speech_text(strip_tts_symbols(item.text))
        except OpenVoiceError as error:
            logger.warning(
                "LLM stream failed session=%s turn=%s error=%s",
                state.session_id,
                turn_id,
                error.message,
            )
            abort_tts.set()
            if tts_task is not None:
                tts_task.cancel()
                with suppress(asyncio.CancelledError):
                    await tts_task
                tts_task = None
            error_event = ErrorEvent(state.session_id, error, turn_id=turn_id)
            error_event.generation_id = generation_id
            if self._is_active_generation(state.session_id, generation_id):
                await emit(error_event)
            return None, first_llm_delta_at, first_tts_chunk_at
        finally:
            if tts_enabled:
                if not abort_tts.is_set():
                    await reconcile_speech_buffer_with_completed()
                    await flush_speech_buffer(force=True)
                await speech_queue.put(None)
                if tts_task is not None:
                    await tts_task
        logger.info(
            "LLM stream finished session=%s turn=%s events=%s assistant_text_len=%s",
            state.session_id,
            turn_id,
            len(llm_events),
            len(_assistant_text(llm_events) or ""),
        )

        return _assistant_text(llm_events), first_llm_delta_at, first_tts_chunk_at


class RealtimeConversationSession:
    """Thin compatibility wrapper around the worker-centered runtime path.

    The previous monolithic realtime session implementation has been retired
    from the active execution path. The active runtime now delegates to
    ``WorkerHost`` / ``SessionWorker`` for all message handling.

    The legacy implementation remains in this module only as historical
    reference during migration and should not be used for new behavior.
    """

    def __init__(
        self,
        sessions: SessionManager,
        *,
        config: RuntimeConfig | None = None,
        stt_service: SttService | None = None,
        vad_service: VadService | None = None,
        router_service: RouterService | None = None,
        llm_service: LlmService | None = None,
        tts_service: TtsService | None = None,
    ) -> None:
        self._worker_host = WorkerHost(
            sessions,
            config=config or RuntimeConfig(),
            stt_service=stt_service,
            vad_service=vad_service,
            router_service=router_service,
            llm_service=llm_service,
            tts_service=tts_service,
        )

    async def apply(
        self,
        payload: dict[str, Any],
        *,
        emit: SerializedEventEmitter | None = None,
    ) -> list[dict[str, Any]]:
        return await self._worker_host.apply(payload, emit=emit)

    async def apply_message(
        self,
        message: ClientMessage,
        *,
        emit: ConversationEventEmitter | None = None,
    ) -> list[ConversationEvent]:
        return await self._worker_host.apply_message(message, emit=emit)


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


def _session_id_from_payload(payload: dict[str, Any]) -> str | None:
    value = payload.get("session_id")
    if isinstance(value, str):
        return value
    return None


def _session_language(state: SessionState) -> str | None:
    value = state.metadata.get("language")
    if isinstance(value, str):
        return value
    return None


def _route_targets(state: SessionState, config: RuntimeConfig) -> tuple[RouteTarget, ...]:
    runtime_config = _effective_runtime_config(state, config)
    return runtime_config.effective_route_targets(state.engine_selection.llm)


def _fallback_route_target(state: SessionState, config: RuntimeConfig) -> RouteTarget | None:
    targets = _route_targets(state, config)
    if targets:
        preferred = select_route_target("moderate_route", targets)
        if preferred is not None:
            return preferred
        return targets[0]

    if state.engine_selection.llm in {None, "opencode"}:
        return RouteTarget(
            llm_engine_id="opencode",
            provider="local-openai",
            model="gpt-5.4-mini",
            profile_id="moderate_route",
        )
    return None


def _fallback_route_selection(
    session_id: str,
    turn_id: str | None,
    target: RouteTarget,
    *,
    reason: str,
) -> tuple[list[ConversationEvent], RouteDecision]:
    decision = RouteDecision(
        router_id="fallback-router",
        route_name=target.profile_id or "fallback_route",
        llm_engine_id=target.llm_engine_id,
        provider=target.provider,
        model=target.model,
        profile_id=target.profile_id,
        reason=reason,
        confidence=0.0,
    )
    event: ConversationEvent = RouteSelectedEvent(
        session_id,
        decision.router_id,
        turn_id=turn_id,
        route_name=decision.route_name,
        llm_engine_id=decision.llm_engine_id,
        provider=decision.provider,
        model=decision.model,
        profile_id=decision.profile_id,
        reason=decision.reason,
        confidence=decision.confidence,
    )
    return [event], decision


def _session_start_metadata(metadata: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    merged = dict(metadata)
    _merge_runtime_config_update(merged, config)
    return merged


def _merge_runtime_config_update(metadata: dict[str, Any], config: dict[str, Any]) -> None:
    if not config:
        return
    existing = metadata.get("runtime_config")
    runtime_config = dict(existing) if isinstance(existing, dict) else {}
    _merge_nested_mapping(runtime_config, config)
    metadata["runtime_config"] = runtime_config


def _effective_runtime_config(state: SessionState, config: RuntimeConfig) -> RuntimeConfig:
    runtime_config = state.metadata.get("runtime_config")
    if not isinstance(runtime_config, dict):
        return config
    try:
        return RuntimeConfig.from_mapping(runtime_config, fallback=config)
    except TypeError:
        return config


def _merge_nested_mapping(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            nested_target = target[key]
            if isinstance(nested_target, dict):
                _merge_nested_mapping(nested_target, value)
                continue
        target[key] = value


def _conversation_events_from_stt(
    session_id: str,
    turn_id: str | None,
    stt_events: list[SttEvent],
    *,
    generation_id: str | None = None,
) -> list[ConversationEvent]:
    events: list[ConversationEvent] = []
    final_by_sequence: dict[int, str] = {}
    for item in stt_events:
        if item.kind is SttEventKind.FINAL:
            text = item.text.strip()
            if text:
                final_by_sequence[item.sequence] = text

    if final_by_sequence:
        concatenated_text = " ".join(
            text for _, text in sorted(final_by_sequence.items()) if text
        ).strip()
        final_event = SttFinalEvent(
            session_id,
            concatenated_text,
            turn_id=turn_id,
            confidence=None,
        )
        final_event.generation_id = generation_id
        events.append(final_event)

    return events


def _conversation_events_from_vad(
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


def _conversation_events_from_llm(
    session_id: str,
    turn_id: str | None,
    llm_events: list[LlmEvent],
) -> list[ConversationEvent]:
    events: list[ConversationEvent] = []
    for item in llm_events:
        if item.kind is LlmEventKind.PHASE:
            events.append(
                LlmPhaseEvent(
                    session_id,
                    item.phase.value if item.phase else "",
                    turn_id=turn_id,
                )
            )
        elif item.kind is LlmEventKind.REASONING_DELTA:
            events.append(
                LlmReasoningDeltaEvent(
                    session_id,
                    item.text,
                    turn_id=turn_id,
                    part_id=item.part_id,
                )
            )
        elif item.kind is LlmEventKind.RESPONSE_DELTA:
            events.append(
                LlmResponseDeltaEvent(
                    session_id,
                    strip_tts_symbols(item.text) if item.text else item.text,
                    turn_id=turn_id,
                    lane=item.lane.value if item.lane else None,
                    part_id=item.part_id,
                )
            )
        elif item.kind is LlmEventKind.TOOL_UPDATE:
            status = item.metadata.get("status") if isinstance(item.metadata, dict) else None
            is_mcp = (
                item.metadata.get("is_mcp") is True if isinstance(item.metadata, dict) else False
            )
            events.append(
                LlmToolUpdateEvent(
                    session_id,
                    tool_name=item.tool_name or "unknown",
                    turn_id=turn_id,
                    call_id=item.call_id,
                    status=status if isinstance(status, str) else None,
                    tool_input=item.tool_input,
                    tool_metadata=item.tool_metadata,
                    tool_output=item.tool_output,
                    tool_error=item.tool_error,
                    is_mcp=is_mcp,
                )
            )
        elif item.kind is LlmEventKind.COMPLETED:
            events.append(
                LlmCompletedEvent(
                    session_id,
                    text=strip_tts_symbols(item.text) if item.text else item.text,
                    finish_reason=item.finish_reason,
                    provider=item.provider,
                    model=item.model,
                    turn_id=turn_id,
                )
            )
        elif item.kind is LlmEventKind.USAGE:
            events.append(
                LlmUsageEvent(
                    session_id,
                    turn_id=turn_id,
                    usage=item.usage,
                    cost=item.cost,
                )
            )
        elif item.kind is LlmEventKind.SUMMARY:
            events.append(
                LlmSummaryEvent(
                    session_id,
                    turn_id=turn_id,
                    provider=item.provider,
                    model=item.model,
                    usage=item.usage,
                    cost=item.cost,
                    metadata=item.metadata if isinstance(item.metadata, dict) else None,
                )
            )
    return events


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
                    text_segment=strip_tts_symbols(item.text_segment or speech_text),
                )
            )
        elif item.kind is TtsEventKind.COMPLETED:
            events.append(
                TtsCompletedEvent(
                    session_id,
                    turn_id=turn_id,
                    duration_ms=item.duration_ms,
                )
            )
    return events


def _set_generation_for_events(events: list[ConversationEvent], generation_id: str | None) -> None:
    if generation_id is None:
        return
    for event in events:
        event.generation_id = generation_id


async def _emit_conversation_events(
    emit: ConversationEventEmitter,
    events: list[ConversationEvent],
) -> None:
    for event in events:
        await emit(event)


def _assistant_text(llm_events: list[LlmEvent]) -> str | None:
    for item in reversed(llm_events):
        if item.kind is LlmEventKind.COMPLETED:
            text = item.text.strip()
            return text or None
    return None


def _final_text_from_stt_events(stt_events: list[SttEvent]) -> str | None:
    parts = [
        event.text.strip()
        for event in stt_events
        if event.kind is SttEventKind.FINAL and event.text.strip()
    ]
    if not parts:
        return None
    return " ".join(parts)


def _latest_partial_text_from_stt_events(stt_events: list[SttEvent]) -> str | None:
    for event in reversed(stt_events):
        if event.kind is SttEventKind.PARTIAL and event.text.strip():
            return event.text
    return None


def _count_meaningful_words(text: str | None) -> int:
    if text is None:
        return 0
    count = 0
    for raw_token in text.split():
        token = "".join(ch for ch in raw_token if ch.isalnum())
        if len(token) >= 2:
            count += 1
    return count


def _speech_text(llm_events: list[LlmEvent]) -> str | None:
    parts = [
        item.text
        for item in llm_events
        if item.kind is LlmEventKind.RESPONSE_DELTA
        and item.lane is LlmOutputLane.SPEECH
        and item.text
    ]
    return "".join(parts) if parts else None


def _extract_stable_speech_segments(
    text: str,
    *,
    flush_incomplete: bool = False,
) -> tuple[list[str], str]:
    segments: list[str] = []
    remaining = text
    for delimiter in ".!?;":
        while delimiter in remaining:
            idx = remaining.index(delimiter)
            # Absorb trailing closing quotes/brackets after delimiter
            end_idx = idx + 1
            while end_idx < len(remaining) and remaining[end_idx] in "\"')]}":
                end_idx += 1
            segment = remaining[:end_idx].strip()
            if segment:
                segments.append(segment)
            remaining = remaining[end_idx:]
    if flush_incomplete:
        remaining = remaining.strip()
        if remaining:
            segments.append(remaining)
            remaining = ""
    return segments, remaining


def _same_normalized_text(left: str, right: str) -> bool:
    if left == right:
        return True
    return " ".join(left.split()) == " ".join(right.split())


def _turn_queue_policy(state: SessionState) -> str:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        turn_queue = runtime_config.get("turn_queue", {})
        if isinstance(turn_queue, dict):
            policy = turn_queue.get("policy")
            if policy in {TURN_QUEUE_POLICY_SEND_NOW, TURN_QUEUE_POLICY_ENQUEUE}:
                return policy
    return TURN_QUEUE_POLICY_SEND_NOW


def _interruption_config(state: SessionState) -> dict[str, Any]:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        interruption = runtime_config.get("interruption", {})
        if isinstance(interruption, dict):
            return {
                "mode": interruption.get("mode", "immediate"),
                "min_duration": _safe_float(interruption.get("min_duration"), 0.0),
                "min_words": _safe_int(interruption.get("min_words"), 0),
                "cooldown_ms": _safe_int(interruption.get("cooldown_ms"), 1000),
            }
    return {
        "mode": "immediate",
        "min_duration": 0.0,
        "min_words": 0,
        "cooldown_ms": 1000,
    }


def _safe_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_str(value: Any, default: str | None = None) -> str | None:
    if isinstance(value, str):
        return value
    return default


def _safe_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _stt_idle_threshold_seconds(state: SessionState) -> float:
    runtime_config = state.metadata.get("runtime_config", {})
    turn = runtime_config.get("turn_detection", {}) if isinstance(runtime_config, dict) else {}
    timeout_ms = 900
    if isinstance(turn, dict):
        timeout_ms = _safe_int(turn.get("transcript_timeout_ms"), 900)
    stabilization_ms = 0
    if isinstance(turn, dict):
        stabilization_ms = _safe_int(turn.get("stabilization_ms"), DEFAULT_STT_STABILIZATION_MS)
    if stabilization_ms > 0:
        timeout_ms = max(timeout_ms, stabilization_ms * 2)
    timeout_ms = max(0, timeout_ms)
    return max(0.20, min(timeout_ms / 1000.0, 1.20))


def _stt_idle_ready_for_commit(
    state: SessionState,
    *,
    final_text: str | None,
    last_stt_final_at: float | None,
    now: float,
) -> tuple[bool, float | None, float]:
    threshold_seconds = _stt_idle_threshold_seconds(state)
    if final_text is None or not final_text.strip():
        return False, None, threshold_seconds
    if last_stt_final_at is None:
        return False, None, threshold_seconds
    idle_seconds = max(0.0, now - last_stt_final_at)
    return idle_seconds >= threshold_seconds, idle_seconds, threshold_seconds


def _contains_vad_speech(
    vad_events: list[VadEvent],
    min_probability: float = 0.5,
) -> bool:
    for event in vad_events:
        if event.kind is VadEventKind.START_OF_SPEECH:
            return True
        if event.kind is VadEventKind.INFERENCE:
            if event.speaking is True:
                if event.probability is None or event.probability >= min_probability:
                    return True
                continue
            if event.speaking is False:
                continue
            if event.probability is not None and event.probability >= min_probability:
                return True
    return False


def _contains_vad_barge_in_start(vad_events: list[VadEvent]) -> bool:
    for event in vad_events:
        if event.kind is VadEventKind.START_OF_SPEECH:
            return True
    return False


def _contains_vad_end_of_speech(vad_events: list[VadEvent]) -> bool:
    # Prioritize explicit end events. Inference-based silence can be noisy and
    # should only be used as a fallback when no explicit boundary is available.
    if any(event.kind is VadEventKind.END_OF_SPEECH for event in vad_events):
        return True

    for event in vad_events:
        if (
            event.kind is VadEventKind.INFERENCE
            and event.speaking is False
            and event.probability is not None
            and event.probability < 0.25
        ):
            return True
    return False


def _duration_ms(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start) * 1000.0


def _router_timeout_seconds(state: SessionState) -> float:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        router_cfg = runtime_config.get("router", {})
        if isinstance(router_cfg, dict):
            return float(router_cfg.get("timeout_ms", 1500)) / 1000.0
    return 1.5


def _router_mode(state: SessionState) -> str:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        router_cfg = runtime_config.get("router", {})
        if isinstance(router_cfg, dict):
            mode = _safe_str(router_cfg.get("mode"))
            if mode in {"disabled", "fallback_only", "enabled"}:
                return mode
    return "enabled"


def _stt_final_timeout_seconds(state: SessionState, *, turn_count: int | None = None) -> float:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        stt_cfg = runtime_config.get("stt", {})
        if isinstance(stt_cfg, dict):
            timeout_ms = _safe_int(stt_cfg.get("final_timeout_ms"), DEFAULT_STT_FINAL_TIMEOUT_MS)
            timeout_ms = max(200, timeout_ms)
            if turn_count is not None and turn_count > 1:
                timeout_ms = max(350, int(timeout_ms * 0.6))
            return timeout_ms / 1000.0
    timeout_ms = DEFAULT_STT_FINAL_TIMEOUT_MS
    if turn_count is not None and turn_count > 1:
        timeout_ms = max(350, int(timeout_ms * 0.6))
    return timeout_ms / 1000.0


def _stt_stabilization_seconds(state: SessionState) -> float:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        turn_cfg = runtime_config.get("turn_detection", {})
        if isinstance(turn_cfg, dict):
            stabilization_ms = _safe_int(
                turn_cfg.get("stabilization_ms"), DEFAULT_STT_STABILIZATION_MS
            )
            stabilization_ms = max(0, min(stabilization_ms, 2000))
            return stabilization_ms / 1000.0
    return DEFAULT_STT_STABILIZATION_MS / 1000.0


def _llm_first_delta_timeout_seconds(state: SessionState) -> float:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        llm_cfg = runtime_config.get("llm", {})
        if isinstance(llm_cfg, dict):
            timeout_ms = _safe_int(
                llm_cfg.get("first_delta_timeout_ms"),
                DEFAULT_LLM_FIRST_DELTA_TIMEOUT_MS,
            )
            timeout_ms = max(200, timeout_ms)
            return timeout_ms / 1000.0
    return DEFAULT_LLM_FIRST_DELTA_TIMEOUT_MS / 1000.0


def _llm_total_timeout_seconds(state: SessionState) -> float:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        llm_cfg = runtime_config.get("llm", {})
        if isinstance(llm_cfg, dict):
            timeout_ms = _safe_int(llm_cfg.get("total_timeout_ms"), DEFAULT_LLM_TOTAL_TIMEOUT_MS)
            timeout_ms = max(500, timeout_ms)
            return timeout_ms / 1000.0
    return DEFAULT_LLM_TOTAL_TIMEOUT_MS / 1000.0


def _tts_audio_format(state: SessionState) -> AudioFormat:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        tts_cfg = runtime_config.get("tts", {})
        if isinstance(tts_cfg, dict):
            return AudioFormat(
                sample_rate_hz=int(tts_cfg.get("sample_rate_hz", 24000)),
                channels=int(tts_cfg.get("channels", 1)),
                encoding=AudioEncoding(tts_cfg.get("encoding", "pcm_s16le")),
            )
    return AudioFormat(sample_rate_hz=24000, channels=1, encoding=AudioEncoding.PCM_S16LE)


def _tts_voice_id(state: SessionState) -> str | None:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        tts_cfg = runtime_config.get("tts", {})
        if isinstance(tts_cfg, dict):
            return tts_cfg.get("voice_id")
    return None


def _endpointing_config(state: SessionState) -> dict[str, Any]:
    runtime_config = state.metadata.get("runtime_config", {})
    if isinstance(runtime_config, dict):
        endpointing = runtime_config.get("endpointing", {})
        if isinstance(endpointing, dict):
            return {
                "mode": endpointing.get("mode", "fixed"),
                "min_delay": float(endpointing.get("min_delay", 0.5)),
                "max_delay": float(endpointing.get("max_delay", 3.0)),
            }
    return {"mode": "fixed", "min_delay": 0.5, "max_delay": 3.0}


def _fallback_route_target(state: SessionState, config: RuntimeConfig) -> RouteTarget | None:
    targets = _route_targets(state, config)
    if targets:
        preferred = select_route_target("moderate_route", targets)
        if preferred is not None:
            return preferred
        return targets[0]
    if state.engine_selection.llm in {None, "opencode"}:
        return RouteTarget(
            llm_engine_id="opencode",
            provider="local-openai",
            model="gpt-5.4-mini",
            profile_id="moderate_route",
        )
    return None


def _route_targets(state: SessionState, config: RuntimeConfig) -> tuple[RouteTarget, ...]:
    runtime_config = _effective_runtime_config(state, config)
    return runtime_config.effective_route_targets(state.engine_selection.llm)


def _effective_runtime_config(state: SessionState, config: RuntimeConfig) -> RuntimeConfig:
    runtime_config = state.metadata.get("runtime_config")
    if not isinstance(runtime_config, dict):
        return config
    try:
        return RuntimeConfig.from_mapping(runtime_config, fallback=config)
    except TypeError:
        return config


def _vad_config(state: SessionState) -> VadConfig:
    runtime_config = state.metadata.get("runtime_config", {})
    turn = runtime_config.get("turn_detection", {}) if isinstance(runtime_config, dict) else {}
    return VadConfig(
        min_speech_duration_ms=int(turn.get("min_speech_duration_ms", 100)),
        min_silence_duration_ms=int(turn.get("min_silence_duration_ms", 600)),
        activation_threshold=float(turn.get("activation_threshold", 0.5)),
        chunk_size=int(turn.get("vad_chunk_size", 512)),
    )


def _turn_detection_config(state: SessionState) -> TurnDetectionConfig:
    runtime_config = state.metadata.get("runtime_config", {})
    turn = runtime_config.get("turn_detection", {}) if isinstance(runtime_config, dict) else {}
    mode = turn.get("mode", "hybrid")
    try:
        parsed_mode = TurnDetectionMode(mode)
    except ValueError:
        parsed_mode = TurnDetectionMode.HYBRID

    endpointing_cfg = _endpointing_config(state)

    return TurnDetectionConfig(
        mode=parsed_mode,
        transcript_timeout_ms=int(turn.get("transcript_timeout_ms", 900)),
        stabilization_ms=int(turn.get("stabilization_ms", DEFAULT_STT_STABILIZATION_MS)),
        min_silence_duration_ms=int(turn.get("min_silence_duration_ms", 600)),
        endpointing_mode=endpointing_cfg["mode"],
        endpointing_min_delay=endpointing_cfg["min_delay"],
        endpointing_max_delay=endpointing_cfg["max_delay"],
    )
