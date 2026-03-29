import { useCallback, useEffect, useRef, useState } from "react"
import {
  FrontendTraceReporter,
  OpenVoiceWebClient,
  InterruptionPolicy,
  selectPendingTurnState,
  selectRouteState,
  selectSttFinalMeta,
  selectSttProgress,
  VoiceAgent,
  selectCurrentSpokenSegment,
  selectTranscript,
  selectTurnPhase,
  SttTrackStateManager,
  toRuntimeConfigPayload,
} from "@open-voice/web-sdk"
import type { WebVoiceSession, RuntimeSessionConfig } from "@open-voice/web-sdk"
import type { DemoSessionState, DemoSessionAction, TurnPhase, PendingTurnPhase } from "../types"
import { initialDemoSessionState, demoSessionReducer } from "../types"
import {
  DEMO_INTERRUPT_MIN_WORDS,
  DEMO_INTERRUPT_COOLDOWN_MS,
  DEMO_LLM_FIRST_DELTA_TIMEOUT_MS,
  DEMO_LLM_TOTAL_TIMEOUT_MS,
  DEMO_GENERATION_WATCHDOG_TIMEOUT_MS,
  VOICE_LLM_TOOLS,
  OPEN_VOICE_SYSTEM_PROMPT,
  OPENCODE_MODE,
} from "../constants/config"
import { VisualizedPcmPlayer, DemoMicInput, ThinkingAudioPlayer } from "../services/audio"
import { zeroBands, FRONTEND_DIAGNOSTICS_ENABLED, resolveStoredFlag } from "../utils"
import thinkingCueUrl from "../../../packages/web-sdk/examples/assets/sfx/achievement-fx.wav?url"
import {
  MINIMAL_CAPTIONS_STORAGE_KEY,
  MINIMAL_DETAIL_STORAGE_KEY,
  MINIMAL_CHAT_HISTORY_STORAGE_KEY,
} from "../constants/config"

export interface UseVoiceSessionOptions {
  baseUrl: string
  voiceId: string
  mode: "detailed" | "minimal"
}

export interface UseVoiceSessionReturn {
  sessionState: DemoSessionState
  dispatchSession: React.Dispatch<DemoSessionAction>
  connect: () => Promise<void>
  disconnect: () => Promise<void>
  isConnecting: boolean
}

export function useVoiceSession(
  options: UseVoiceSessionOptions,
): UseVoiceSessionReturn {
  const { baseUrl, voiceId, mode } = options

  const [sessionState, dispatchSession] = useReducer(demoSessionReducer, initialDemoSessionState)
  const [isConnecting, setIsConnecting] = useState(false)

  const sessionRef = useRef<WebVoiceSession | null>(null)
  const agentRef = useRef<VoiceAgent | null>(null)
  const sdkPlayerRef = useRef<any>(null)
  const micRef = useRef<DemoMicInput | null>(null)
  const thinkingPlayerRef = useRef<ThinkingAudioPlayer | null>(null)

  const connect = useCallback(async () => {
    if (sessionRef.current || isConnecting) return
    setIsConnecting(true)

    try {
      // Create audio player and mic
      const player = new VisualizedPcmPlayer(
        () => {}, // bands callback - handled separately
        (active) => dispatchSession({ type: "setTts", currentSpokenSegment: "", playbackActive: active, streamActive: false })
      )
      sdkPlayerRef.current = player

      const mic = new DemoMicInput(
        async () => {}, // send chunk - will be set up after session
        () => {}, // onLevel
        () => {}  // onBands
      )
      micRef.current = mic

      // Create and connect session (simplified - full implementation would follow the original pattern)
      const runtimeBaseUrl = baseUrl.trim()
      const client = new OpenVoiceWebClient({ baseUrl: runtimeBaseUrl })
      
      // This is a simplified version - the full implementation would set up all the event handlers
      // similar to the original App.tsx
      
    } catch (error) {
      console.error("Failed to connect:", error)
    } finally {
      setIsConnecting(false)
    }
  }, [baseUrl, isConnecting])

  const disconnect = useCallback(async () => {
    if (!sessionRef.current) return
    
    try {
      await sessionRef.current.disconnect()
    } finally {
      sessionRef.current = null
      agentRef.current = null
    }
  }, [])

  return {
    sessionState,
    dispatchSession,
    connect,
    disconnect,
    isConnecting,
  }
}

export function useAudio() {
  const [micLevel, setMicLevel] = useState(0)
  const [micBands, setMicBands] = useState(() => zeroBands())
  const [ttsBands, setTtsBands] = useState(() => zeroBands())

  return {
    micLevel,
    setMicLevel,
    micBands,
    setMicBands,
    ttsBands,
    setTtsBands,
  }
}

export function useMinimalSettings() {
  const [minimalSettingsOpen, setMinimalSettingsOpen] = useState(false)
  const [minimalCaptionsEnabled, setMinimalCaptionsEnabled] = useState(() =>
    resolveStoredFlag(MINIMAL_CAPTIONS_STORAGE_KEY, true),
  )
  const [minimalDetailEnabled, setMinimalDetailEnabled] = useState(() =>
    resolveStoredFlag(MINIMAL_DETAIL_STORAGE_KEY, false),
  )
  const [minimalChatHistoryEnabled, setMinimalChatHistoryEnabled] = useState(() =>
    resolveStoredFlag(MINIMAL_CHAT_HISTORY_STORAGE_KEY, true),
  )

  return {
    minimalSettingsOpen,
    setMinimalSettingsOpen,
    minimalCaptionsEnabled,
    setMinimalCaptionsEnabled,
    minimalDetailEnabled,
    setMinimalDetailEnabled,
    minimalChatHistoryEnabled,
    setMinimalChatHistoryEnabled,
  }
}

export function useHistory(baseUrl: string, sessionId: string | null) {
  const [historySidebarOpen, setHistorySidebarOpen] = useState(false)
  const [sessionHistory, setSessionHistory] = useState<any[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState("")
  const [historyEndpointSupported, setHistoryEndpointSupported] = useState(true)
  const [selectedHistorySessionId, setSelectedHistorySessionId] = useState<string | null>(null)

  // Load initial history
  useEffect(() => {
    const stored = require("../utils").readStoredSessionHistory()
    setSessionHistory(stored)
  }, [])

  return {
    historySidebarOpen,
    setHistorySidebarOpen,
    sessionHistory,
    setSessionHistory,
    historyLoading,
    setHistoryLoading,
    historyError,
    setHistoryError,
    historyEndpointSupported,
    setHistoryEndpointSupported,
    selectedHistorySessionId,
    setSelectedHistorySessionId,
  }
}
