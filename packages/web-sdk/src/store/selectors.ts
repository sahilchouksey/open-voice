import type { VoiceSessionState } from "../state"

export const selectTurnPhase = (state: VoiceSessionState): VoiceSessionState["turnPhase"] =>
  state.turnPhase

export const selectPendingTurns = (state: VoiceSessionState): number => state.queue.pendingTurns

export const selectPendingTurnState = (
  state: VoiceSessionState,
): VoiceSessionState["pendingTurn"] => state.pendingTurn

export const selectInterruptState = (state: VoiceSessionState): {
  interrupted: boolean
  reason: string | null
} => ({
  interrupted: state.turnPhase === "idle" && state.metrics.cancelled,
  reason: state.metrics.reason ?? null,
})

export const selectCurrentSpokenSegment = (
  state: VoiceSessionState,
): {
  text: string | null
  generationId: string | null
  updatedAt: string | null
} => ({
  text: state.tts.currentSpokenSegment ?? null,
  generationId: state.tts.currentSpokenGenerationId ?? null,
  updatedAt: state.tts.currentSpokenUpdatedAt ?? null,
})

export const selectRouteState = (
  state: VoiceSessionState,
): {
  routeName: string | null
  provider: string | null
  model: string | null
} => ({
  routeName: state.route.routeName ?? null,
  provider: state.route.provider ?? null,
  model: state.route.model ?? null,
})

export const selectSttProgress = (
  state: VoiceSessionState,
): {
  status: string | null
  waitedMs: number | null
  attempt: number | null
} => ({
  status: state.stt.status ?? null,
  waitedMs: state.stt.waitedMs ?? null,
  attempt: state.stt.attempt ?? null,
})

export const selectSttFinalMeta = (
  state: VoiceSessionState,
): {
  revision: number | null
  finality: "stable" | "revised" | "duplicate" | null
  deferred: boolean | null
  previousText: string | null
} => ({
  revision: state.stt.revision ?? null,
  finality: state.stt.finality ?? null,
  deferred: state.stt.deferred ?? null,
  previousText: state.stt.previousText ?? null,
})

export const selectLatencyMetrics = (state: VoiceSessionState): VoiceSessionState["metrics"] =>
  state.metrics

export const selectTranscript = (state: VoiceSessionState): VoiceSessionState["transcript"] =>
  state.transcript
