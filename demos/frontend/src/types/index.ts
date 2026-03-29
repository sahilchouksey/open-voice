import type {
  TranscriptEntry,
  SessionHistoryEntry,
  TtsChunk,
} from "@open-voice/web-sdk"

export type Mode = "detailed" | "minimal"
export type TurnPhase = "idle" | "listening" | "user_speaking" | "processing" | "agent_speaking"
export type PendingTurnPhase = "idle" | "commit_sent" | "awaiting_backend" | "slow" | "degraded" | "timeout"

export type TranscriptItem = TranscriptEntry

export interface SessionConversationHistory {
  sessionId: string
  title: string
  status: string
  updatedAt: string
  turnCount: number
  completedTurnCount: number
  lastUserText: string | null
  lastAssistantText: string | null
  transcript: TranscriptItem[]
}

export interface EngineReadiness {
  checked: boolean
  ok: boolean
  message: string
}

export interface DemoSessionState {
  sessionId: string
  sessionStatus: string
  turnPhase: TurnPhase
  isListening: boolean
  sttLiveText: string
  sttProgress: { status: string | null; waitedMs: number | null; attempt: number | null }
  sttFinalMeta: { revision: number | null; finality: string | null; deferred: boolean | null; previousText: string | null }
  llmThinkingText: string
  llmResponseText: string
  llmThinkingActive: boolean
  currentSpokenSegment: string
  ttsPlaybackActive: boolean
  ttsStreamActive: boolean
  routeName: string
  routeProvider: string | null
  routeModel: string | null
  pendingTurnPhase: PendingTurnPhase
  pendingTurnElapsedMs: number
  transcript: TranscriptItem[]
}

export type DemoSessionAction =
  | { type: "setSession"; sessionId: string; sessionStatus: string; turnPhase: TurnPhase; isListening: boolean }
  | { type: "setSttLiveText"; text: string }
  | { type: "setSttProgress"; progress: DemoSessionState["sttProgress"] }
  | { type: "setSttFinalMeta"; meta: DemoSessionState["sttFinalMeta"] }
  | { type: "setLlmThinking"; text: string; active: boolean }
  | { type: "setLlmResponse"; text: string }
  | { type: "setTts"; currentSpokenSegment: string; playbackActive: boolean; streamActive: boolean }
  | { type: "setRoute"; routeName: string; provider: string | null; model: string | null }
  | { type: "setPendingTurn"; phase: PendingTurnPhase; elapsedMs: number }
  | { type: "setTranscript"; transcript: TranscriptItem[] }
  | { type: "setIsListening"; isListening: boolean }

export const initialDemoSessionState: DemoSessionState = {
  sessionId: "-",
  sessionStatus: "disconnected",
  turnPhase: "idle",
  isListening: false,
  sttLiveText: "",
  sttProgress: { status: null, waitedMs: null, attempt: null },
  sttFinalMeta: { revision: null, finality: null, deferred: null, previousText: null },
  llmThinkingText: "",
  llmResponseText: "",
  llmThinkingActive: false,
  currentSpokenSegment: "",
  ttsPlaybackActive: false,
  ttsStreamActive: false,
  routeName: "-",
  routeProvider: null,
  routeModel: null,
  pendingTurnPhase: "idle",
  pendingTurnElapsedMs: 0,
  transcript: [],
}

export function demoSessionReducer(state: DemoSessionState, action: DemoSessionAction): DemoSessionState {
  switch (action.type) {
    case "setSession":
      return { ...state, sessionId: action.sessionId, sessionStatus: action.sessionStatus, turnPhase: action.turnPhase, isListening: action.isListening }
    case "setSttLiveText":
      return { ...state, sttLiveText: action.text }
    case "setSttProgress":
      return { ...state, sttProgress: action.progress }
    case "setSttFinalMeta":
      return { ...state, sttFinalMeta: action.meta }
    case "setLlmThinking":
      return { ...state, llmThinkingText: action.text, llmThinkingActive: action.active }
    case "setLlmResponse":
      return { ...state, llmResponseText: action.text }
    case "setTts":
      return { ...state, currentSpokenSegment: action.currentSpokenSegment, ttsPlaybackActive: action.playbackActive, ttsStreamActive: action.streamActive }
    case "setRoute":
      return { ...state, routeName: action.routeName, routeProvider: action.provider, routeModel: action.model }
    case "setPendingTurn":
      return { ...state, pendingTurnPhase: action.phase, pendingTurnElapsedMs: action.elapsedMs }
    case "setTranscript":
      return { ...state, transcript: action.transcript }
    case "setIsListening":
      return { ...state, isListening: action.isListening }
    default:
      return state
  }
}
