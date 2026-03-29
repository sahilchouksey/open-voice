import type { AudioInputAdapter } from "../audio/input"
import type { RuntimeSessionConfig } from "../config"
import type { FrontendTraceReporter } from "../diagnostics/trace"
import type { AudioOutputAdapter } from "../interruption/audio_output"
import type { ConversationEvent, EngineSelection, SessionState } from "../protocol"
import type { TurnPhase, VoiceSessionState } from "../state"
import type { ConversationListener } from "../transport/websocket"

export interface VoiceAgentConnectOptions {
  sessionId?: string
  engineSelection?: EngineSelection
  metadata?: Record<string, unknown>
  runtimeConfig?: RuntimeSessionConfig
  input?: AudioInputAdapter
  audioOutput?: AudioOutputAdapter
  onEvent?: ConversationListener
  traceReporter?: FrontendTraceReporter
  autoStart?: boolean
  verifyEngines?: boolean
}

export interface VoiceAgentSessionSnapshot {
  session: SessionState
}

export type VoiceAgentConnectionState =
  | "connecting"
  | "connected"
  | "disconnected"
  | "failed"
  | "closed"

export interface VoiceAgentSignalBase {
  type: string
  timestampMs: number
  sessionId: string | null
}

export interface VoiceAgentConnectionStateSignal extends VoiceAgentSignalBase {
  type: "connection.state"
  state: VoiceAgentConnectionState
  previousState: VoiceAgentConnectionState
}

export interface VoiceAgentTurnPhaseSignal extends VoiceAgentSignalBase {
  type: "turn.phase.changed"
  phase: TurnPhase
  previousPhase: TurnPhase
}

export interface VoiceAgentSessionStatusSignal extends VoiceAgentSignalBase {
  type: "session.status"
  status: string
  reason: string | null
  generationId: string | null
}

export interface VoiceAgentPendingTurnSignal extends VoiceAgentSignalBase {
  type: "pending_turn.state"
  phase: VoiceSessionState["pendingTurn"]["phase"]
  elapsedMs: number
  clientTurnId: string | null
}

export interface VoiceAgentSttPartialSignal extends VoiceAgentSignalBase {
  type: "stt.partial"
  text: string
  turnId: string | null
  generationId: string | null
}

export interface VoiceAgentVadSignal extends VoiceAgentSignalBase {
  type: "vad.state"
  kind: "start_of_speech" | "end_of_speech" | "inference"
  speaking: boolean | null
  probability: number | null
  turnId: string | null
  generationId: string | null
}

export interface VoiceAgentSttFinalSignal extends VoiceAgentSignalBase {
  type: "stt.final"
  text: string
  turnId: string | null
  generationId: string | null
  revision: number | null
  finality: "stable" | "revised" | "duplicate" | null
  deferred: boolean | null
}

export interface VoiceAgentSttProgressSignal extends VoiceAgentSignalBase {
  type: "stt.progress"
  status: string | null
  waitedMs: number | null
  attempt: number | null
}

export interface VoiceAgentRouteSignal extends VoiceAgentSignalBase {
  type: "route.updated"
  routeName: string | null
  provider: string | null
  model: string | null
}

export interface VoiceAgentQueueSignal extends VoiceAgentSignalBase {
  type: "queue.updated"
  pendingTurns: number
  policy: string | null
  source: string | null
}

export interface VoiceAgentThinkingSignal extends VoiceAgentSignalBase {
  type: "assistant.thinking"
  delta: string
}

export interface VoiceAgentLlmPhaseSignal extends VoiceAgentSignalBase {
  type: "assistant.phase"
  phase: "thinking" | "generating" | "done"
  turnId: string | null
  generationId: string | null
}

export interface VoiceAgentResponseDeltaSignal extends VoiceAgentSignalBase {
  type: "assistant.response.delta"
  delta: string
}

export interface VoiceAgentResponseFinalSignal extends VoiceAgentSignalBase {
  type: "assistant.response.final"
  text: string
  provider: string | null
  model: string | null
}

export interface VoiceAgentLlmErrorSignal extends VoiceAgentSignalBase {
  type: "assistant.error"
  message: string
  code: string | null
  turnId: string | null
  generationId: string | null
}

export interface VoiceAgentSpeakingSegmentSignal extends VoiceAgentSignalBase {
  type: "assistant.speaking.segment"
  text: string | null
  generationId: string | null
}

export interface VoiceAgentSpeakingStateSignal extends VoiceAgentSignalBase {
  type: "assistant.speaking.state"
  state: VoiceSessionState["tts"]["status"]
  durationMs: number | null
}

export interface VoiceAgentInterruptSignal extends VoiceAgentSignalBase {
  type: "interrupt.lifecycle"
  stage: "requested" | "acknowledged"
  reason: string | null
}

export interface VoiceAgentMetricsSignal extends VoiceAgentSignalBase {
  type: "metrics.turn"
  queueDelayMs: number | null
  turnToFirstLlmDeltaMs: number | null
  turnToCompleteMs: number | null
  cancelled: boolean
  reason: string | null
}

export interface VoiceAgentErrorSignal extends VoiceAgentSignalBase {
  type: "sdk.error"
  message: string
  code: string | null
  details?: Record<string, unknown> | null
}

export type VoiceAgentSignal =
  | VoiceAgentConnectionStateSignal
  | VoiceAgentSessionStatusSignal
  | VoiceAgentTurnPhaseSignal
  | VoiceAgentPendingTurnSignal
  | VoiceAgentVadSignal
  | VoiceAgentSttPartialSignal
  | VoiceAgentSttFinalSignal
  | VoiceAgentSttProgressSignal
  | VoiceAgentLlmPhaseSignal
  | VoiceAgentRouteSignal
  | VoiceAgentQueueSignal
  | VoiceAgentThinkingSignal
  | VoiceAgentResponseDeltaSignal
  | VoiceAgentResponseFinalSignal
  | VoiceAgentLlmErrorSignal
  | VoiceAgentSpeakingSegmentSignal
  | VoiceAgentSpeakingStateSignal
  | VoiceAgentInterruptSignal
  | VoiceAgentMetricsSignal
  | VoiceAgentErrorSignal

export type VoiceAgentSignalListener = (signal: VoiceAgentSignal) => void

export interface VoiceAgentEventContext {
  previousState: VoiceSessionState | null
  state: VoiceSessionState
  event: ConversationEvent
}
