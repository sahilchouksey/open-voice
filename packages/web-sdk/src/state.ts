import type {
  ConversationEvent,
  SessionStatus,
  VadStateEvent,
} from "./protocol"

export type TurnPhase = "idle" | "listening" | "user_speaking" | "processing" | "agent_speaking"

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
    interimText: string
    finalText: string
    lastFinalTurnId?: string | null
    lastFinalText?: string | null
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
  }
  queue: {
    pendingTurns: number
    policy?: string | null
    lastSource?: string | null
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
      interimText: "",
      finalText: "",
      lastFinalTurnId: null,
      lastFinalText: null,
    },
    route: {},
    llm: {
      thinkingText: "",
      responseText: "",
    },
    tts: {
      status: "idle",
    },
    queue: {
      pendingTurns: 0,
    },
    metrics: {
      cancelled: false,
    },
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
    metrics: { ...state.metrics },
  }

  switch (event.type) {
    case "session.ready":
      next.sessionStatus = "ready"
      next.turnPhase = "listening"
      return next
    case "session.status":
      next.sessionStatus = event.status
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
    case "stt.partial":
      next.stt.interimText = event.text
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
      next.stt.interimText = ""
      next.route = {}
      next.llm.thinkingText = ""
      next.llm.responseText = ""
      next.tts.status = "idle"
      next.tts.durationMs = undefined
      next.metrics.cancelled = false
      next.metrics.reason = undefined
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "route.selected":
      next.route.routeName = event.route_name
      next.route.provider = event.provider
      next.route.model = event.model
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "llm.phase":
      next.llm.phase = event.phase
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "llm.reasoning.delta":
      next.llm.thinkingText += event.delta ?? ""
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "llm.response.delta":
      next.llm.responseText += event.delta ?? ""
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "llm.completed":
      next.llm.responseText = event.text
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "llm.error":
      next.llm.phase = "done"
      if (next.sessionStatus === "thinking" || next.sessionStatus === "speaking") {
        next.sessionStatus = "listening"
      }
      next.tts.status = "idle"
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "error": {
      const timeoutKind =
        event.details && typeof event.details === "object"
          ? (event.details as { timeout_kind?: unknown }).timeout_kind
          : undefined
      if (timeoutKind === "stt_final_timeout") {
        next.sessionStatus = "listening"
        next.turnPhase = deriveTurnPhase(next)
        return next
      }
      next.sessionStatus = "failed"
      next.turnPhase = "idle"
      return next
    }
    case "tts.chunk":
      next.tts.status = "playing"
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "tts.completed":
      next.tts.status = "complete"
      next.tts.durationMs = event.duration_ms
      next.turnPhase = deriveTurnPhase(next)
      return next
    case "conversation.interrupted":
      next.queue.pendingTurns = 0
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
      return next
    case "session.closed":
      next.sessionStatus = "closed"
      next.turnPhase = "idle"
      return next
    default:
      return next
  }
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
