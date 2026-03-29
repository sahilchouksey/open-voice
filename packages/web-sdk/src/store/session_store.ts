import type { ClientMessage, ConversationEvent } from "../protocol"
import {
  PENDING_TURN_DEGRADED_MS,
  PENDING_TURN_SLOW_MS,
  PENDING_TURN_TIMEOUT_MS,
  createVoiceSessionState,
  reduceVoiceSessionOutboundMessage,
  reduceVoiceSessionEvent,
  type VoiceSessionState,
  type TranscriptEntry,
} from "../state"
import { createStore, type Store } from "./core"
import type { VoiceStoreAction } from "./actions"

export type VoiceSessionStore = Store<VoiceSessionState, VoiceStoreAction>

export function createVoiceSessionStore(sessionId: string): VoiceSessionStore {
  const initialState = createVoiceSessionState(sessionId)
  return createStore(initialState, reduceVoiceStoreAction)
}

export function reduceVoiceStoreAction(
  state: VoiceSessionState,
  action: VoiceStoreAction,
): VoiceSessionState {
  switch (action.type) {
    case "event.inbound":
      return reduceVoiceSessionEvent(state, action.event)
    case "clock.tick":
      return reduceVoiceSessionClockTick(state, action.timestampMs)
    case "session.reset":
      return createVoiceSessionState(action.sessionId)
    case "message.outbound":
      return reduceVoiceSessionOutboundMessage(state, action.message, action.timestampMs)
    case "transcript.set":
      return { ...state, transcript: action.entries }
    default:
      return state
  }
}

export function toInboundEventAction(event: ConversationEvent): VoiceStoreAction {
  return { type: "event.inbound", event }
}

export function toOutboundMessageAction(message: ClientMessage): VoiceStoreAction {
  return {
    type: "message.outbound",
    message,
    timestampMs: Date.now(),
  }
}

export function toClockTickAction(timestampMs = Date.now()): VoiceStoreAction {
  return {
    type: "clock.tick",
    timestampMs,
  }
}

export function toSetTranscriptAction(entries: TranscriptEntry[]): VoiceStoreAction {
  return { type: "transcript.set", entries }
}

function reduceVoiceSessionClockTick(
  state: VoiceSessionState,
  timestampMs: number,
): VoiceSessionState {
  if (state.pendingTurn.phase === "idle" || typeof state.pendingTurn.startedAtMs !== "number") {
    return state
  }

  const elapsedMs = Math.max(0, timestampMs - state.pendingTurn.startedAtMs)
  let phase = state.pendingTurn.phase
  if (elapsedMs >= PENDING_TURN_TIMEOUT_MS) {
    phase = "timeout"
  } else if (elapsedMs >= PENDING_TURN_DEGRADED_MS) {
    phase = "degraded"
  } else if (elapsedMs >= PENDING_TURN_SLOW_MS) {
    phase = "slow"
  }

  if (elapsedMs === state.pendingTurn.elapsedMs && phase === state.pendingTurn.phase) {
    return state
  }

  return {
    ...state,
    pendingTurn: {
      ...state.pendingTurn,
      phase,
      elapsedMs,
    },
  }
}
