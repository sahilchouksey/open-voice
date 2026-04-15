import type {
  ClientMessage,
  ConversationEvent,
  SessionStatus,
  VadStateEvent,
} from "./protocol"

const PENDING_TURN_TIMEOUT_MS = 25000
export const PENDING_TURN_SLOW_MS = 2000
export const PENDING_TURN_DEGRADED_MS = 8000
export { PENDING_TURN_TIMEOUT_MS }

export type TurnPhase = "idle" | "listening" | "user_speaking" | "processing" | "agent_speaking"

export interface TranscriptEntry {
  role: "user" | "assistant"
  text: string
  turnId?: string
}

const TRANSCRIPT_LIMIT = 100

export interface VoiceSessionState {
  sessionId: string
  sessionStatus: SessionStatus | "disconnected"
  turnId?: string | null
  turnPhase: TurnPhase
  vad: {
    speaking: boolean
    probability?: number | null
    lastEventKind?: VadStateEvent["kind"]
  }
  stt: {
    finalText: string
    lastFinalTurnId?: string | null
    lastFinalText?: string | null
    status?: string | null
    waitedMs?: number | null
    attempt?: number | null
    revision?: number | null
    finality?: "stable" | "revised" | "duplicate" | null
    deferred?: boolean | null
    previousText?: string | null
  }
  route: {
    routeName?: string | null
    provider?: string | null
    model?: string | null
  }
  llm: {
    phase?: "thinking" | "generating" | "done"
    thinkingText: string
    responseText: string
  }
  tts: {
    status: "idle" | "playing" | "complete"
    durationMs?: number | null
    currentSpokenSegment?: string | null
    currentSpokenGenerationId?: string | null
    currentSpokenUpdatedAt?: string | null
  }
  queue: {
    pendingTurns: number
    policy?: string | null
    lastSource?: string | null
  }
  pendingTurn: {
    phase: "idle" | "commit_sent" | "awaiting_backend" | "slow" | "degraded" | "timeout"
    clientTurnId?: string | null
    startedAtMs?: number | null
    elapsedMs: number
  }
  metrics: {
    queueDelayMs?: number | null
    sttToRouteMs?: number | null
    routeToLlmFirstDeltaMs?: number | null
    llmFirstDeltaToTtsFirstChunkMs?: number | null
    sttToTtsFirstChunkMs?: number | null
    turnToFirstLlmDeltaMs?: number | null
    turnToCompleteMs?: number | null
    cancelled: boolean
    reason?: string | null
  }
  transcript: TranscriptEntry[]
}

export function createVoiceSessionState(sessionId: string): VoiceSessionState {
  return {
    sessionId,
    sessionStatus: "disconnected",
    turnId: null,
    turnPhase: "idle",
    vad: {
      speaking: false,
    },
    stt: {
      finalText: "",
      lastFinalTurnId: null,
      lastFinalText: null,
      status: null,
      waitedMs: null,
      attempt: null,
      revision: null,
      finality: null,
      deferred: null,
      previousText: null,
    },
    route: {},
    llm: {
      thinkingText: "",
      responseText: "",
    },
    tts: {
      status: "idle",
      currentSpokenSegment: null,
      currentSpokenGenerationId: null,
      currentSpokenUpdatedAt: null,
    },
    queue: {
      pendingTurns: 0,
    },
    pendingTurn: {
      phase: "idle",
      clientTurnId: null,
      startedAtMs: null,
      elapsedMs: 0,
    },
    metrics: {
      cancelled: false,
    },
    transcript: [],
  }
}

export function reduceVoiceSessionEvent(
  state: VoiceSessionState,
  event: ConversationEvent,
): VoiceSessionState {
  const next: VoiceSessionState = {
    ...state,
    turnId: event.turn_id ?? state.turnId,
    vad: { ...state.vad },
    stt: { ...state.stt },
    route: { ...state.route },
    llm: { ...state.llm },
    tts: { ...state.tts },
    queue: { ...state.queue },
    pendingTurn: { ...state.pendingTurn },
    metrics: { ...state.metrics },
  }

  switch (event.type) {
    case "session.ready":
      next.sessionStatus = "ready"
      next.turnPhase = "listening"
      return next
    case "session.status":
      next.sessionStatus = event.status
      if (event.status === "transcribing" && next.pendingTurn.phase !== "idle") {
        next.pendingTurn.phase = "awaiting_backend"
      }
      if (
        (event.status === "thinking"
          || event.status === "speaking"
          || event.status === "listening"
          || event.status === "ready")
        && next.pendingTurn.phase !== "idle"
      ) {
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
      }
      if (
        (event.status === "interrupted"
          || event.status === "closed"
          || event.status === "failed")
        && next.pendingTurn.phase !== "idle"
      ) {
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
      }
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "vad.state":
      if (event.speaking !== undefined && event.speaking !== null) {
        next.vad.speaking = event.speaking
      }
      next.vad.probability = event.probability
      next.vad.lastEventKind = event.kind
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "stt.status":
      next.stt.status = event.status
      next.stt.waitedMs = event.waited_ms ?? null
      next.stt.attempt = event.attempt ?? null
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "stt.final":
      if (
        next.stt.lastFinalTurnId === (event.turn_id ?? null) &&
        next.stt.lastFinalText === event.text
      ) {
        return next
      }
      next.stt.finalText = event.text
      next.stt.lastFinalTurnId = event.turn_id ?? null
      next.stt.lastFinalText = event.text
      next.stt.revision = event.revision ?? null
      next.stt.finality = event.finality ?? null
      next.stt.deferred = event.deferred ?? null
      next.stt.previousText = event.previous_text ?? null
      next.stt.status = "completed"
      next.route = {}
      next.llm.thinkingText = ""
      next.llm.responseText = ""
      next.tts.status = "idle"
      next.tts.durationMs = undefined
      next.metrics.cancelled = false
      next.metrics.reason = undefined
      next.pendingTurn.phase = "idle"
      next.pendingTurn.clientTurnId = null
      next.pendingTurn.startedAtMs = null
      next.pendingTurn.elapsedMs = 0
      if (event.text?.trim()) {
        next.transcript = [
          ...next.transcript.slice(-TRANSCRIPT_LIMIT + 1),
          { role: "user" as const, text: event.text, turnId: event.turn_id ?? undefined },
        ]
      }
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "route.selected":
      next.route.routeName = event.route_name
      next.route.provider = event.provider
      next.route.model = event.model
      if (next.pendingTurn.phase !== "idle") {
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
      }
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "llm.phase":
      next.llm.phase = event.phase
      if (next.pendingTurn.phase !== "idle") {
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
      }
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "llm.tool.update":
      if (next.pendingTurn.phase !== "idle") {
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
      }
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "llm.reasoning.delta":
      next.llm.thinkingText += event.delta ?? ""
      if (next.pendingTurn.phase !== "idle") {
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
      }
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "llm.response.delta":
      next.llm.responseText += event.delta ?? ""
      if (next.pendingTurn.phase !== "idle") {
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
      }
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "llm.completed":
      next.llm.responseText = event.text
      if (next.pendingTurn.phase !== "idle") {
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
      }
      if (event.text?.trim()) {
        next.transcript = [
          ...next.transcript.slice(-TRANSCRIPT_LIMIT + 1),
          { role: "assistant" as const, text: event.text, turnId: event.turn_id ?? undefined },
        ]
      }
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "llm.error":
      next.llm.phase = "done"
      next.llm.thinkingText = ""
      next.llm.responseText = ""
      if (next.sessionStatus === "thinking" || next.sessionStatus === "speaking") {
        next.sessionStatus = "listening"
      }
      next.tts.status = "idle"
      next.tts.currentSpokenSegment = null
      next.tts.currentSpokenGenerationId = null
      next.tts.currentSpokenUpdatedAt = event.timestamp
      if (next.pendingTurn.phase !== "idle") {
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
      }
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "tts.chunk":
      next.tts.status = "playing"
      next.tts.currentSpokenSegment = event.text_segment ?? next.tts.currentSpokenSegment ?? null
      next.tts.currentSpokenGenerationId = event.generation_id ?? null
      next.tts.currentSpokenUpdatedAt = event.timestamp
      if (next.pendingTurn.phase !== "idle") {
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
      }
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "tts.completed":
      next.tts.status = "complete"
      next.tts.durationMs = event.duration_ms
      next.tts.currentSpokenSegment = null
      next.tts.currentSpokenGenerationId = null
      next.tts.currentSpokenUpdatedAt = event.timestamp
      if (next.pendingTurn.phase !== "idle") {
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
      }
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "conversation.interrupted":
      next.queue.pendingTurns = 0
      next.pendingTurn.phase = "idle"
      next.pendingTurn.clientTurnId = null
      next.pendingTurn.startedAtMs = null
      next.pendingTurn.elapsedMs = 0
      next.tts.currentSpokenSegment = null
      next.tts.currentSpokenGenerationId = null
      next.tts.currentSpokenUpdatedAt = event.timestamp
      next.turnPhase = "idle"
      return next
    case "turn.queued":
      next.queue.pendingTurns = event.queue_size
      next.queue.policy = event.policy ?? null
      next.queue.lastSource = event.source ?? null
      return next
    case "turn.metrics":
      next.metrics.queueDelayMs = event.queue_delay_ms ?? null
      next.metrics.sttToRouteMs = event.stt_to_route_ms ?? null
      next.metrics.routeToLlmFirstDeltaMs = event.route_to_llm_first_delta_ms ?? null
      next.metrics.llmFirstDeltaToTtsFirstChunkMs =
        event.llm_first_delta_to_tts_first_chunk_ms ?? null
      next.metrics.sttToTtsFirstChunkMs = event.stt_to_tts_first_chunk_ms ?? null
      next.metrics.turnToFirstLlmDeltaMs = event.turn_to_first_llm_delta_ms ?? null
      next.metrics.turnToCompleteMs = event.turn_to_complete_ms ?? null
      next.metrics.cancelled = event.cancelled
      next.metrics.reason = event.reason ?? null
      if (!event.cancelled && next.queue.pendingTurns > 0) {
        next.queue.pendingTurns = Math.max(0, next.queue.pendingTurns - 1)
      }
      next.pendingTurn.phase = "idle"
      next.pendingTurn.clientTurnId = null
      next.pendingTurn.startedAtMs = null
      next.pendingTurn.elapsedMs = 0
      return next
    case "session.closed":
      next.sessionStatus = "closed"
      next.turnPhase = "idle"
      return next
    case "error": {
      const timeoutKind =
        event.details && typeof event.details === "object"
          ? (event.details as { timeout_kind?: unknown }).timeout_kind
          : undefined
      if (timeoutKind === "stt_final_timeout") {
        next.sessionStatus = "listening"
        next.pendingTurn.phase = "idle"
        next.pendingTurn.clientTurnId = null
        next.pendingTurn.startedAtMs = null
        next.pendingTurn.elapsedMs = 0
        next.turnPhase = deriveTurnPhase(next)
        return next
      }
      next.llm.phase = "done"
      next.llm.thinkingText = ""
      next.llm.responseText = ""
      next.tts.status = "idle"
      next.tts.currentSpokenSegment = null
      next.tts.currentSpokenGenerationId = null
      next.tts.currentSpokenUpdatedAt = event.timestamp
      next.sessionStatus = "failed"
      next.pendingTurn.phase = "idle"
      next.pendingTurn.clientTurnId = null
      next.pendingTurn.startedAtMs = null
      next.pendingTurn.elapsedMs = 0
      next.turnPhase = "idle"
      return next
    }
    default:
      return next
  }
}

export function reduceVoiceSessionOutboundMessage(
  state: VoiceSessionState,
  message: ClientMessage,
  timestampMs: number,
): VoiceSessionState {
  const next: VoiceSessionState = {
    ...state,
    pendingTurn: { ...state.pendingTurn },
  }

  if (message.type !== "audio.commit" && message.type !== "user_turn.commit") {
    return next
  }

  const clientTurnId =
    typeof message.client_turn_id === "string" && message.client_turn_id.trim().length > 0
      ? message.client_turn_id
      : null

  if (
    next.pendingTurn.phase !== "idle"
    && typeof next.pendingTurn.startedAtMs === "number"
    && timestampMs - next.pendingTurn.startedAtMs < PENDING_TURN_TIMEOUT_MS
  ) {
    return next
  }

  next.pendingTurn.phase = "commit_sent"
  next.pendingTurn.clientTurnId = clientTurnId
  next.pendingTurn.startedAtMs = timestampMs
  next.pendingTurn.elapsedMs = 0
  return next
}

function deriveTurnPhase(state: VoiceSessionState): TurnPhase {
  if (state.sessionStatus === "speaking" || state.tts.status === "playing") {
    return "agent_speaking"
  }
  if (state.sessionStatus === "thinking") {
    return "processing"
  }
  if (state.sessionStatus === "transcribing") {
    return "processing"
  }
  if (state.vad.speaking) {
    return "user_speaking"
  }
  if (state.sessionStatus === "listening" || state.sessionStatus === "ready") {
    return "listening"
  }
  return "idle"
}
