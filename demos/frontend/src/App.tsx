import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react"
import {
  FrontendTraceReporter,
  BrowserMicInput as SdkBrowserMicInput,
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
  toRuntimeConfigPayload,
  toSetTranscriptAction,
  computeAnalyserBands,
  type WebVoiceSession,
  type AudioOutputAdapter,
  type ConversationEvent,
  type SessionHistoryEntry,
  type RuntimeSessionConfig,
  type TtsChunk,
  type VoiceAgentSignal,
  type TranscriptEntry,
} from "@open-voice/web-sdk"
import { WebVoiceSession as WebVoiceSessionClass } from "@open-voice/web-sdk"
import { Button, Card, Input, Label, Select, TabsList, TabsTrigger } from "./components/ui"
import { GridVisualizer } from "./components/GridVisualizer"
import thinkingCueUrl from "../../../packages/web-sdk/examples/assets/sfx/achievement-fx.wav?url"

type Mode = "detailed" | "minimal"
type TurnPhase = "idle" | "listening" | "user_speaking" | "processing" | "agent_speaking"

const MINIMAL_VISUALIZER_STYLE: "radial" | "grid" = "grid"

const DEV_SERVER_LOGS = import.meta.env.DEV

function devLog(...args: unknown[]) {
  if (!DEV_SERVER_LOGS) return
  console.log(...args)
}

function devWarn(...args: unknown[]) {
  if (!DEV_SERVER_LOGS) return
  console.warn(...args)
}

type TranscriptItem = TranscriptEntry

interface SessionConversationHistory {
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

interface EngineReadiness {
  checked: boolean
  ok: boolean
  message: string
}

type PendingTurnPhase =
  | "idle"
  | "commit_sent"
  | "awaiting_backend"
  | "slow"
  | "degraded"
  | "timeout"

interface DemoSessionState {
  sessionId: string
  sessionStatus: string
  turnPhase: TurnPhase
  isListening: boolean
  sttLiveText: string
  sttProgress: { status: string | null; waitedMs: number | null; attempt: number | null }
  sttFinalMeta: { revision: number | null; finality: string | null; deferred: boolean | null; previousText: string | null }
  llmThinkingText: string
  toolActivityText: string
  toolActivityStatus: string
  llmResponseText: string
  llmThinkingActive: boolean
  llmResponseCompletedAt: number
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

type DemoSessionAction =
  | { type: "setSession"; sessionId: string; sessionStatus: string; turnPhase: TurnPhase; isListening: boolean }
  | { type: "setTurnPhaseOnly"; turnPhase: TurnPhase }
  | { type: "setSessionStatusOnly"; sessionStatus: string }
  | { type: "setSessionIdOnly"; sessionId: string }
  | { type: "setListeningOnly"; isListening: boolean }
  | { type: "setSttLiveText"; text: string }
  | { type: "setSttProgress"; progress: DemoSessionState["sttProgress"] }
  | { type: "setSttFinalMeta"; meta: DemoSessionState["sttFinalMeta"] }
  | { type: "setLlmThinking"; text: string; active: boolean }
  | { type: "setToolActivity"; text: string; status: string }
  | { type: "setLlmResponse"; text: string }
  | { type: "setLlmResponseCompletedAt"; at: number }
  | { type: "setTts"; currentSpokenSegment: string; playbackActive: boolean; streamActive: boolean }
  | { type: "setCurrentSpokenSegmentOnly"; currentSpokenSegment: string }
  | { type: "setTtsPlaybackOnly"; playbackActive: boolean }
  | { type: "setTtsStreamOnly"; streamActive: boolean }
  | { type: "setRoute"; routeName: string; provider: string | null; model: string | null }
  | { type: "setPendingTurn"; phase: PendingTurnPhase; elapsedMs: number }
  | { type: "setTranscript"; transcript: TranscriptItem[] }
  | { type: "setIsListening"; isListening: boolean }

const initialDemoSessionState: DemoSessionState = {
  sessionId: "-",
  sessionStatus: "disconnected",
  turnPhase: "idle",
  isListening: false,
  sttLiveText: "",
  sttProgress: { status: null, waitedMs: null, attempt: null },
  sttFinalMeta: { revision: null, finality: null, deferred: null, previousText: null },
  llmThinkingText: "",
  toolActivityText: "",
  toolActivityStatus: "",
  llmResponseText: "",
  llmThinkingActive: false,
  llmResponseCompletedAt: 0,
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

function demoSessionReducer(state: DemoSessionState, action: DemoSessionAction): DemoSessionState {
  switch (action.type) {
    case "setSession":
      return { ...state, sessionId: action.sessionId, sessionStatus: action.sessionStatus, turnPhase: action.turnPhase, isListening: action.isListening }
    case "setTurnPhaseOnly":
      return { ...state, turnPhase: action.turnPhase }
    case "setSessionStatusOnly":
      return { ...state, sessionStatus: action.sessionStatus }
    case "setSessionIdOnly":
      return { ...state, sessionId: action.sessionId }
    case "setListeningOnly":
      return { ...state, isListening: action.isListening }
    case "setSttLiveText":
      return { ...state, sttLiveText: action.text }
    case "setSttProgress":
      return { ...state, sttProgress: action.progress }
    case "setSttFinalMeta":
      return { ...state, sttFinalMeta: action.meta }
    case "setLlmThinking":
      return { ...state, llmThinkingText: action.text, llmThinkingActive: action.active }
    case "setToolActivity":
      return { ...state, toolActivityText: action.text, toolActivityStatus: action.status }
    case "setLlmResponse":
      return { ...state, llmResponseText: action.text }
    case "setLlmResponseCompletedAt":
      return { ...state, llmResponseCompletedAt: action.at }
    case "setTts":
      return { ...state, currentSpokenSegment: action.currentSpokenSegment, ttsPlaybackActive: action.playbackActive, ttsStreamActive: action.streamActive }
    case "setCurrentSpokenSegmentOnly":
      return { ...state, currentSpokenSegment: action.currentSpokenSegment }
    case "setTtsPlaybackOnly":
      return { ...state, ttsPlaybackActive: action.playbackActive }
    case "setTtsStreamOnly":
      return { ...state, ttsStreamActive: action.streamActive }
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

function describeToolActivity(toolName: string, status: string | null | undefined) {
  const normalizedTool = toolName.trim().toLowerCase().replace(/[_-]+/g, " ")
  const prettyTool = normalizedTool === "websearch"
    ? "web search"
    : normalizedTool === "webfetch"
      ? "web fetch"
      : (normalizedTool || "tool")
  const nextStatus = (status ?? "").trim().toLowerCase()

  if (prettyTool === "web search") {
    if (nextStatus === "pending") return { summary: "Preparing web search…", spokenHint: null }
    if (nextStatus === "running") return { summary: "Searching the web…", spokenHint: "Searching the web." }
    if (nextStatus === "completed") return { summary: "Web search completed.", spokenHint: null }
    if (nextStatus === "failed") return { summary: "Web search failed.", spokenHint: "The web search failed." }
  }

  if (prettyTool === "web fetch") {
    if (nextStatus === "pending") return { summary: "Preparing page fetch…", spokenHint: null }
    if (nextStatus === "running") return { summary: "Reading the page…", spokenHint: "Reading the page." }
    if (nextStatus === "completed") return { summary: "Page fetch completed.", spokenHint: null }
    if (nextStatus === "failed") return { summary: "Page fetch failed.", spokenHint: "The page fetch failed." }
  }

  const title = prettyTool ? prettyTool.charAt(0).toUpperCase() + prettyTool.slice(1) : "Tool"
  if (nextStatus === "pending") return { summary: `${title} pending…`, spokenHint: null }
  if (nextStatus === "running") return { summary: `${title} running…`, spokenHint: `Using ${prettyTool}.` }
  if (nextStatus === "completed") return { summary: `${title} completed.`, spokenHint: null }
  if (nextStatus === "failed") return { summary: `${title} failed.`, spokenHint: `${title} failed.` }
  return { summary: title, spokenHint: null }
}

class ThinkingAudioPlayer {
  private readonly audio: HTMLAudioElement
  private readonly gapMs: number
  private restartTimer: number | null = null
  private playing = false

  constructor(url: string, opts?: { gapMs?: number; volume?: number }) {
    this.audio = new Audio(url)
    this.audio.preload = "auto"
    this.audio.volume = opts?.volume ?? 0.2
    this.gapMs = opts?.gapMs ?? 180
    this.audio.addEventListener("ended", () => {
      if (!this.playing) return
      this.clearTimer()
      this.restartTimer = window.setTimeout(() => {
        if (!this.playing) return
        this.audio.currentTime = 0
        void this.audio.play().catch(() => undefined)
      }, this.gapMs)
    })
  }

  private clearTimer() {
    if (this.restartTimer !== null) {
      window.clearTimeout(this.restartTimer)
      this.restartTimer = null
    }
  }

  async start() {
    if (this.playing) return
    this.playing = true
    this.audio.currentTime = 0
    await this.audio.play().catch(() => {
      this.playing = false
    })
  }

  stop() {
    this.playing = false
    this.clearTimer()
    this.audio.pause()
    this.audio.currentTime = 0
  }

  isPlaying() {
    return this.playing
  }
}

const AUDIO_BAND_COUNT = 9
const DEMO_MIN_SPEECH_DURATION_MS = 320
const DEMO_VAD_ACTIVATION_THRESHOLD = 0.75
const DEMO_UI_VAD_PROBABILITY_THRESHOLD = 0.78
const DEMO_INTERRUPT_COOLDOWN_MS = 600
const DEMO_INTERRUPT_MIN_DURATION_SECONDS = 0.25
const DEMO_INTERRUPT_MIN_WORDS = 2
const DEMO_LOCAL_BARGE_IN_PEAK_THRESHOLD = 0.15
const DEMO_LOCAL_BARGE_IN_CONSECUTIVE_FRAMES = 2
const DEMO_LOCAL_BARGE_IN_COOLDOWN_MS = 1000
const DEMO_LOCAL_BARGE_IN_FLOOR_ALPHA = 0.08
const DEMO_LOCAL_BARGE_IN_FLOOR_MULTIPLIER = 2.2
const DEMO_LOCAL_BARGE_IN_FLOOR_BIAS = 0.04
const DEMO_ENABLE_STT_PARTIAL_AUTO_INTERRUPT = true
const DEMO_ENABLE_LOCAL_AUDIO_AUTO_INTERRUPT = true
const DEMO_ENABLE_VAD_AUTO_INTERRUPT = false
const DEMO_STT_TRANSCRIPT_TIMEOUT_MS = 2000
const DEMO_MIN_SILENCE_DURATION_MS = 1400
const DEMO_POST_RELEASE_PROCESSING_GRACE_MS = 1400
const DEMO_IDLE_TRANSITION_DELAY_MS = 220
const DEMO_SLOW_STT_STABILIZATION_MS = 160
const DEMO_MIC_STOP_COMMIT_GRACE_MS = 1600
const DEMO_AUTO_COMMIT_MIN_INTERVAL_MS = 400
const DEMO_ROUTER_TIMEOUT_MS = 3000
const DEMO_CAPTURE_BUFFER_SIZE = 4096
const DEMO_GENERATION_WATCHDOG_TIMEOUT_MS = 90000
const DEMO_STT_FINAL_TIMEOUT_MS = 1600
const DEMO_LLM_FIRST_DELTA_TIMEOUT_MS = 25000
const DEMO_LLM_TOTAL_TIMEOUT_MS = 60000
const DEMO_ROUTER_MODE: "disabled" | "fallback_only" | "enabled" = "fallback_only"
const DEMO_PHASE_DEBOUNCE_MS = 180
const DEMO_FORCE_SEND_NOW_DEFAULT = true
const DEMO_DISABLE_STT_STABILIZATION = false
const DEMO_EVENT_TRACE_MAX_ITEMS = 80
const DEMO_EVENT_TRACE_FLUSH_MS = 120
const DEMO_UI_STT_TEXT_THROTTLE_MS = 16
const DEMO_UI_LLM_DELTA_THROTTLE_MS = 16
const DEMO_UI_MIC_LEVEL_THROTTLE_MS = 16
const DEMO_UI_BAND_INTERVAL_MS = 16
const DEMO_ERROR_SPEECH_COOLDOWN_MS = 12000
const DEMO_MIC_CAPTURE_STALE_MS = 2600

const DEMO_SEND_NOW_RUNTIME_OWNED_COMMIT = true
const MINIMAL_CAPTIONS_STORAGE_KEY = "openvoice.minimal.captions"
const MINIMAL_DETAIL_STORAGE_KEY = "openvoice.minimal.detail"
const LOCAL_SESSION_HISTORY_STORAGE_KEY = "openvoice.session_history.v1"
const SESSION_HISTORY_LIMIT = 5
const SESSION_TRANSCRIPT_LIMIT = 50
function zeroBands(count = AUDIO_BAND_COUNT): number[] {
  return Array.from({ length: count }, () => 0)
}

function normalizeSpeechCompare(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

function isLikelyAssistantEcho(partialText: string, assistantText: string): boolean {
  const partial = normalizeSpeechCompare(partialText)
  const assistant = normalizeSpeechCompare(assistantText)
  if (partial.length < 4 || assistant.length < 4) return false
  return assistant.includes(partial)
}

function isLikelyNoisePartial(text: string): boolean {
  const trimmed = text.trim()
  if (!trimmed) return true

  const words = trimmed.split(/\s+/).filter(Boolean)
  const alphaCount = (trimmed.match(/[a-z]/gi) ?? []).length

  if (trimmed.length < 3) return true
  if (alphaCount < 2) return true
  if (words.length === 1 && alphaCount <= 2) return true

  // Common non-speech keyboard mash patterns.
  if (/^(?:[asdfjkl;]{2,}|[qwertyuiop]{2,}|[zxcvbnm]{2,}|[0-9]{2,})$/i.test(trimmed)) {
    return true
  }
  if (/^(.)\1{2,}$/.test(trimmed)) {
    return true
  }

  return false
}

function buildSpokenLlmErrorMessage(message: string, code?: string | null): string {
  const normalized = `${code ?? ""} ${message}`.toLowerCase()
  if (normalized.includes("rate limit")) {
    return "I hit a rate limit while generating that response. Please wait a moment and try again."
  }
  if (normalized.includes("provider_error") || normalized.includes("provider error")) {
    return "I ran into a provider error while generating that response. Please try again in a moment."
  }
  return "I ran into a language model error while generating that response. Please try again."
}

class DemoMicInput {
  private readonly mic: SdkBrowserMicInput
  private running = false
  private captureToken = 0

  constructor(
    sendChunk: (chunk: {
      data: ArrayBuffer
      sequence: number
      encoding: "pcm_s16le"
      sampleRateHz: number
      channels: number
      durationMs: number
    }) => void | Promise<void>,
    onLevel: (value: number) => void,
    onBands: (bands: number[]) => void,
    onChunkMeta?: (meta: {
      sequence: number
      sampleRateHz: number
      channels: number
      durationMs: number
      bytes: number
    }) => void,
  ) {
    this.mic = new SdkBrowserMicInput({
      sampleRateHz: 16000,
      channels: 1,
      chunkSize: DEMO_CAPTURE_BUFFER_SIZE,
      analyserBandCount: AUDIO_BAND_COUNT,
      analyserBandIntervalMs: DEMO_UI_BAND_INTERVAL_MS,
      analyserFftSize: 2048,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      onLevel,
      onBands,
      onChunkMeta,
    })
    this.sendChunk = sendChunk
  }

  private readonly sendChunk: (chunk: {
    data: ArrayBuffer
    sequence: number
    encoding: "pcm_s16le"
    sampleRateHz: number
    channels: number
    durationMs: number
  }) => void | Promise<void>

  async start(): Promise<void> {
    const captureToken = ++this.captureToken
    this.running = true
    await this.mic.start(async (chunk) => {
      if (!this.running || captureToken !== this.captureToken) {
        return
      }
      await this.sendChunk({
        data: chunk.data,
        sequence: chunk.sequence,
        encoding: "pcm_s16le",
        sampleRateHz: chunk.sampleRateHz,
        channels: chunk.channels,
        durationMs: chunk.durationMs ?? 0,
      })
    })
  }

  async stop(): Promise<void> {
    this.running = false
    this.captureToken += 1
    await this.mic.stop()
  }
}

class PcmLevelTap {
  private ctx: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private gain: GainNode | null = null
  private bandTimer: number | null = null

  constructor(private readonly onBands: (bands: number[]) => void, private readonly bandCount = 7) {
    this.onBands(zeroBands(this.bandCount))
  }

  private ensureContext(sampleRateHz: number) {
    if (!this.ctx || this.ctx.state === "closed") {
      this.ctx = new AudioContext({ sampleRate: sampleRateHz })
      this.analyser = this.ctx.createAnalyser()
      this.analyser.fftSize = 256
      this.analyser.smoothingTimeConstant = 0

      this.gain = this.ctx.createGain()
      this.gain.gain.value = 0
      this.gain.connect(this.analyser)
      this.analyser.connect(this.ctx.destination)

      this.bandTimer = window.setInterval(() => {
        if (!this.analyser) return
        this.onBands(computeAnalyserBands(this.analyser, this.bandCount))
      }, DEMO_UI_BAND_INTERVAL_MS)
    }
  }

  async appendPcm16(data: Uint8Array, sampleRateHz: number): Promise<void> {
    this.ensureContext(sampleRateHz)
    if (!this.ctx || !this.gain) return
    if (this.ctx.state === "suspended") {
      await this.ctx.resume()
    }

    const int16 = new Int16Array(data.buffer, data.byteOffset, data.byteLength / 2)
    const buffer = this.ctx.createBuffer(1, int16.length, this.ctx.sampleRate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < int16.length; i += 1) {
      channel[i] = int16[i] / 0x8000
    }

    const source = this.ctx.createBufferSource()
    source.buffer = buffer
    source.connect(this.gain)
    source.start()
  }

  async close(): Promise<void> {
    if (this.bandTimer !== null) {
      window.clearInterval(this.bandTimer)
      this.bandTimer = null
    }
    this.gain?.disconnect()
    this.analyser?.disconnect()
    this.gain = null
    this.analyser = null
    if (this.ctx) {
      await this.ctx.close()
      this.ctx = null
    }
    this.onBands(zeroBands(this.bandCount))
  }
}

class VisualizedPcmPlayer implements AudioOutputAdapter {
  private readonly tap: PcmLevelTap
  private audioContext: AudioContext | null = null
  private nextStartTime = 0
  private activeSources = new Set<AudioBufferSourceNode>()

  constructor(
    onBands: (bands: number[]) => void,
    private readonly onPlaybackActiveChange: (active: boolean) => void,
  ) {
    this.tap = new PcmLevelTap(onBands, AUDIO_BAND_COUNT)
  }

  private markPlaybackActive(active: boolean) {
    devLog("[AudioOutput] markPlaybackActive:", active)
    this.onPlaybackActiveChange(active)
  }

  private async ensureContext(sampleRateHz: number) {
    if (!this.audioContext || this.audioContext.state === "closed") {
      this.audioContext = new AudioContext({ sampleRate: sampleRateHz })
      this.nextStartTime = this.audioContext.currentTime
    }
    if (this.audioContext.state === "suspended") {
      await this.audioContext.resume()
    }
  }

  async appendTtsChunk(chunk: TtsChunk): Promise<void> {
    devLog("[AudioOutput] appendTtsChunk called, data length:", chunk.data.byteLength, "sampleRate:", chunk.sampleRateHz)
    await this.ensureContext(chunk.sampleRateHz)
    if (!this.audioContext) {
      devLog("[AudioOutput] no audioContext, returning early")
      return
    }

    const sampleRate = chunk.sampleRateHz
    const int16 = new Int16Array(
      chunk.data.buffer,
      chunk.data.byteOffset,
      chunk.data.byteLength / 2,
    )
    const buffer = this.audioContext.createBuffer(1, int16.length, sampleRate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < int16.length; i += 1) {
      channel[i] = int16[i] / 0x8000
    }

    const source = this.audioContext.createBufferSource()
    source.buffer = buffer
    source.connect(this.audioContext.destination)

    const when = Math.max(this.nextStartTime, this.audioContext.currentTime + 0.01)
    this.nextStartTime = when + buffer.duration

    this.activeSources.add(source)
    this.markPlaybackActive(true)

    source.addEventListener("ended", () => {
      this.activeSources.delete(source)
      if (this.activeSources.size === 0) {
        this.markPlaybackActive(false)
      }
    })

    source.start(when)
    await this.tap.appendPcm16(chunk.data, chunk.sampleRateHz)
  }

  async flush(reason?: string): Promise<void> {
    devLog("[AudioOutput] flush called, reason:", reason, "activeSources:", this.activeSources.size)
    for (const source of this.activeSources) {
      try {
        source.stop()
      } catch {
        // Ignore stop errors for already-ended nodes.
      }
      source.disconnect()
    }
    this.activeSources.clear()
    this.markPlaybackActive(false)

    if (this.audioContext) {
      await this.audioContext.close()
      this.audioContext = null
    }
    this.nextStartTime = 0

    await this.tap.close()
  }
}

const OPENCODE_MODE = "voice"

const VOICE_LLM_TOOLS = [
  {
    name: "websearch",
    kind: "function" as const,
    description: "Search the web for current information and relevant sources.",
  },
  {
    name: "webfetch",
    kind: "function" as const,
    description: "Fetch and read content from a specific web URL.",
  },
]

const OPEN_VOICE_SYSTEM_PROMPT = [
  "You are Open Voice, a realtime voice-first assistant for conversation and web research.",
  "Prioritize natural spoken responses that are concise, clear, and interruption-friendly.",
  "If a newer user utterance arrives, immediately abandon stale context and continue from the latest user intent.",
  "This is a voice-first conversation, so default to spoken next steps instead of screen or keyboard actions.",
  "Never ask the user to type, paste, click, tap, copy, upload, or use the keyboard unless they explicitly ask for a screen-based workflow.",
  "If you need more detail, ask the user to say it aloud, spell it slowly, or answer verbally.",
  "Do not tell the user to read or inspect the screen unless they explicitly ask for a screen-only answer.",
  "For current events or other time-sensitive questions, always search the web before answering.",
  "Never guess or rely on stale memory for news, politics, markets, sports, weather, or other live facts.",
  "Use tools when needed, but never expose internal routing, model, or tool implementation details.",
  "Never read full URLs aloud.",
  "If a source must be spoken, say only the domain name.",
  "Never include protocol, path, query params, tracking codes, or full link strings in spoken output.",
  "If the user asks for a link, explain what they will find there while speaking only the domain name.",
  "If the user explicitly requests the exact link text, say it can be shown on screen but not spoken aloud.",
].join(" ")

function envFlag(value: unknown, defaultValue = false): boolean {
  if (typeof value === "boolean") {
    return value
  }
  if (typeof value !== "string") {
    return defaultValue
  }

  const normalized = value.trim().toLowerCase()
  if (["1", "true", "yes", "on", "enabled"].includes(normalized)) {
    return true
  }
  if (["0", "false", "no", "off", "disabled", ""].includes(normalized)) {
    return false
  }
  return defaultValue
}

const FRONTEND_DIAGNOSTICS_ENABLED = envFlag(
  import.meta.env.VITE_OPEN_VOICE_FRONTEND_DIAGNOSTICS,
  false,
)

function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1"
}

function resolveInitialRuntimeBaseUrl(): string {
  const fromQuery = new URLSearchParams(location.search).get("runtime")?.trim()
  const fromStorage = localStorage.getItem("openvoice.runtimeBaseUrl")?.trim()
  const fallback = location.origin
  const candidate = fromQuery || fromStorage

  if (!candidate) return fallback

  try {
    const runtimeUrl = new URL(candidate, location.origin)
    if (!isLoopbackHost(location.hostname) && isLoopbackHost(runtimeUrl.hostname)) {
      return fallback
    }
    return runtimeUrl.origin
  } catch {
    return fallback
  }
}

function parseModeValue(value: string | null): Mode | null {
  if (value === "minimal" || value === "detailed") {
    return value
  }
  return null
}

function formatEventForPanel(event: ConversationEvent): string {
  if (event.type !== "tts.chunk") {
    return JSON.stringify(event, null, 2)
  }

  const chunk = event.chunk as {
    data_base64?: unknown
    encoding?: unknown
    sample_rate_hz?: unknown
    channels?: unknown
    sequence?: unknown
    duration_ms?: unknown
  }
  const raw = typeof chunk.data_base64 === "string" ? chunk.data_base64 : null
  const payload = {
    ...event,
    chunk: {
      ...chunk,
      data_base64: raw ? "[omitted]" : chunk.data_base64,
      data_base64_bytes: raw ? Math.floor((raw.length * 3) / 4) : undefined,
    },
  }
  return JSON.stringify(payload, null, 2)
}

function resolveInitialMode(): Mode {
  const params = new URLSearchParams(location.search)
  return parseModeValue(params.get("tab")) ?? parseModeValue(params.get("mode")) ?? "detailed"
}

function resolveSessionIdFromUrl(): string | null {
  const params = new URLSearchParams(location.search)
  const raw = params.get("session")?.trim() ?? params.get("session_id")?.trim() ?? ""
  return raw || null
}

function persistSessionIdToUrl(sessionId: string | null): void {
  const url = new URL(window.location.href)
  if (sessionId && sessionId.trim()) {
    url.searchParams.set("session", sessionId.trim())
  } else {
    url.searchParams.delete("session")
  }
  url.searchParams.delete("session_id")
  window.history.replaceState(null, "", url.toString())
}

function isSessionClosedOrFailed(status: unknown): boolean {
  return status === "closed" || status === "failed"
}

function isRecoverableResumeErrorMessage(message: string): boolean {
  const normalized = message.toLowerCase()
  return normalized.includes("requested session is closed")
    || normalized.includes("requested session is failed")
    || normalized.includes("session_not_found")
    || normalized.includes("was not found")
    || normalized.includes(" 404")
}

function resolveStoredFlag(storageKey: string, fallback: boolean): boolean {
  const raw = localStorage.getItem(storageKey)
  if (raw === "1" || raw === "true") return true
  if (raw === "0" || raw === "false") return false
  return fallback
}

function trimText(text: string, maxChars = 160): string {
  const normalized = text.trim().replace(/\s+/g, " ")
  if (normalized.length <= maxChars) {
    return normalized
  }
  return `${normalized.slice(0, maxChars - 3).trimEnd()}...`
}

function makeHistoryTitle(item: SessionHistoryEntry): string {
  const fromLastUser = typeof item.last_user_text === "string" ? trimText(item.last_user_text, 80) : ""
  const fromTitle = typeof item.title === "string" ? item.title.trim() : ""
  if (fromLastUser) return fromLastUser
  if (fromTitle) return fromTitle
  return item.session_id.slice(0, 8)
}

function buildSessionHistoryEntry(item: SessionHistoryEntry): SessionConversationHistory {
  return {
    sessionId: item.session_id,
    title: makeHistoryTitle(item),
    status: item.status,
    updatedAt: item.updated_at,
    turnCount: item.turn_count,
    completedTurnCount: item.completed_turn_count,
    lastUserText: typeof item.last_user_text === "string" ? item.last_user_text : null,
    lastAssistantText: typeof item.last_assistant_text === "string" ? item.last_assistant_text : null,
    transcript: [],
  }
}

function transcriptSummaryText(item: TranscriptItem): string {
  return trimText(item.text, 140)
}

function readStoredSessionHistory(): SessionConversationHistory[] {
  const raw = localStorage.getItem(LOCAL_SESSION_HISTORY_STORAGE_KEY)
  if (!raw) {
    return []
  }

  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      return []
    }

    const rows: SessionConversationHistory[] = []
    for (const item of parsed) {
      if (!item || typeof item !== "object") {
        continue
      }
      const sessionId = typeof item.sessionId === "string" ? item.sessionId : ""
      if (!sessionId) {
        continue
      }
      const transcript = Array.isArray((item as { transcript?: unknown }).transcript)
        ? (item as { transcript: unknown[] }).transcript
            .map((entry) => {
              if (!entry || typeof entry !== "object") return null
              const role = (entry as { role?: unknown }).role
              const text = (entry as { text?: unknown }).text
              if ((role === "user" || role === "assistant") && typeof text === "string") {
                return { role, text } as TranscriptItem
              }
              return null
            })
            .filter((entry): entry is TranscriptItem => entry !== null)
        : []

      rows.push({
        sessionId,
        title: typeof item.title === "string" && item.title.trim() ? item.title : `Session ${sessionId.slice(0, 8)}`,
        status: typeof item.status === "string" ? item.status : "unknown",
        updatedAt: typeof item.updatedAt === "string" ? item.updatedAt : new Date(0).toISOString(),
        turnCount: typeof item.turnCount === "number" ? item.turnCount : 0,
        completedTurnCount: typeof item.completedTurnCount === "number" ? item.completedTurnCount : 0,
        lastUserText: typeof item.lastUserText === "string" ? item.lastUserText : null,
        lastAssistantText: typeof item.lastAssistantText === "string" ? item.lastAssistantText : null,
        transcript,
      })
    }

    rows.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    return rows.slice(0, SESSION_HISTORY_LIMIT)
  } catch {
    return []
  }
}

function writeStoredSessionHistory(items: SessionConversationHistory[]): void {
  const payload = items.slice(0, SESSION_HISTORY_LIMIT)
  localStorage.setItem(LOCAL_SESSION_HISTORY_STORAGE_KEY, JSON.stringify(payload))
}

function dedupeAndSortHistory(items: SessionConversationHistory[]): SessionConversationHistory[] {
  const bySessionId = new Map<string, SessionConversationHistory>()
  for (const item of items) {
    const existing = bySessionId.get(item.sessionId)
    if (!existing) {
      bySessionId.set(item.sessionId, item)
      continue
    }
    const keepIncoming = item.updatedAt.localeCompare(existing.updatedAt) >= 0
    bySessionId.set(item.sessionId, keepIncoming ? item : existing)
  }
  return Array.from(bySessionId.values())
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, SESSION_HISTORY_LIMIT)
}

function latestTranscriptByRole(transcript: TranscriptItem[], role: TranscriptItem["role"]): string | null {
  for (let index = transcript.length - 1; index >= 0; index -= 1) {
    const item = transcript[index]
    if (item.role === role && item.text.trim()) {
      return item.text
    }
  }
  return null
}

function transcriptFromHistoryTurns(turns: Array<{
  turn_id: string
  user_text?: string | null
  assistant_text?: string | null
}>): TranscriptItem[] {
  const transcript: TranscriptItem[] = []
  for (const turn of turns) {
    if (typeof turn.user_text === "string" && turn.user_text.trim()) {
      transcript.push({ role: "user", text: turn.user_text })
    }
    if (typeof turn.assistant_text === "string" && turn.assistant_text.trim()) {
      transcript.push({ role: "assistant", text: turn.assistant_text })
    }
  }
  if (transcript.length <= SESSION_TRANSCRIPT_LIMIT) {
    return transcript
  }
  return transcript.slice(-SESSION_TRANSCRIPT_LIMIT)
}

export function App() {
  const [mode, setMode] = useState<Mode>(resolveInitialMode)
  const [baseUrl, setBaseUrl] = useState(resolveInitialRuntimeBaseUrl)
  const [requestedSessionId, setRequestedSessionId] = useState<string | null>(resolveSessionIdFromUrl)
  const [voiceId, setVoiceId] = useState("af_heart")
  const [queuePolicy, setQueuePolicy] = useState<"enqueue" | "send_now" | "inject_next_loop">(
    DEMO_FORCE_SEND_NOW_DEFAULT ? "send_now" : "send_now",
  )

  const [sessionState, dispatchSession] = useReducer(demoSessionReducer, initialDemoSessionState)

  const { sessionId, sessionStatus, turnPhase, isListening, sttLiveText, llmThinkingText, toolActivityText, toolActivityStatus, llmResponseText, llmResponseCompletedAt, currentSpokenSegment, routeName, routeProvider, routeModel, transcript, ttsPlaybackActive, ttsStreamActive, llmThinkingActive, pendingTurnPhase, pendingTurnElapsedMs, sttProgress, sttFinalMeta } = sessionState

  const setTurnPhase = useCallback((phase: TurnPhase) => {
    dispatchSession({ type: "setTurnPhaseOnly", turnPhase: phase })
  }, [])

  const setSessionId = useCallback((id: string) => {
    dispatchSession({ type: "setSessionIdOnly", sessionId: id })
  }, [])

  const setSessionStatus = useCallback((status: string) => {
    dispatchSession({ type: "setSessionStatusOnly", sessionStatus: status })
  }, [])

  const setTtsPlaybackActive = useCallback((active: boolean) => {
    dispatchSession({ type: "setTtsPlaybackOnly", playbackActive: active })
  }, [])

  const setTtsStreamActive = useCallback((active: boolean) => {
    dispatchSession({ type: "setTtsStreamOnly", streamActive: active })
  }, [])

  const setCurrentSpokenSegment = useCallback((segment: string) => {
    dispatchSession({ type: "setCurrentSpokenSegmentOnly", currentSpokenSegment: segment })
  }, [])

  const setSttProgressState = useCallback((progress: { status: string | null; waitedMs: number | null; attempt: number | null }) => {
    dispatchSession({ type: "setSttProgress", progress })
  }, [])

  const setSttFinalMetaState = useCallback((meta: { revision: number | null; finality: string | null; deferred: boolean | null; previousText: string | null }) => {
    dispatchSession({ type: "setSttFinalMeta", meta })
  }, [])

  const setSttLiveText = useCallback((text: string) => {
    dispatchSession({ type: "setSttLiveText", text })
  }, [])

  const setLlmThinkingText = useCallback((text: string) => {
    dispatchSession({ type: "setLlmThinking", text, active: llmThinkingActive })
  }, [llmThinkingActive])

  const setToolActivity = useCallback((text: string, status: string) => {
    dispatchSession({ type: "setToolActivity", text, status })
  }, [])

  const setLlmResponseText = useCallback((text: string) => {
    dispatchSession({ type: "setLlmResponse", text })
  }, [])

  const setLlmResponseCompletedAt = useCallback((at: number) => {
    dispatchSession({ type: "setLlmResponseCompletedAt", at })
  }, [])

  const setRouteName = useCallback((name: string) => {
    dispatchSession({ type: "setRoute", routeName: name, provider: routeProvider, model: routeModel })
  }, [routeProvider, routeModel])

  const setRouteProvider = useCallback((provider: string | null) => {
    dispatchSession({ type: "setRoute", routeName, provider, model: routeModel })
  }, [routeName, routeModel])

  const setRouteModel = useCallback((model: string | null) => {
    dispatchSession({ type: "setRoute", routeName, provider: routeProvider, model })
  }, [routeName, routeProvider])

  const setPendingTurnPhase = useCallback((phase: PendingTurnPhase) => {
    dispatchSession({ type: "setPendingTurn", phase, elapsedMs: pendingTurnElapsedMs })
  }, [pendingTurnElapsedMs])

  const setPendingTurnElapsedMs = useCallback((elapsed: number) => {
    dispatchSession({ type: "setPendingTurn", phase: pendingTurnPhase, elapsedMs: elapsed })
  }, [pendingTurnPhase])

  const setLlmThinkingActive = useCallback((active: boolean) => {
    dispatchSession({ type: "setLlmThinking", text: llmThinkingText, active })
  }, [llmThinkingText])

  const [events, setEvents] = useState<string[]>([])
  const [micLevel, setMicLevel] = useState(0)
  const [micBands, setMicBands] = useState<number[]>(() => zeroBands())
  const [ttsBands, setTtsBands] = useState<number[]>(() => zeroBands())
  const [pendingSpeechAfterThinking, setPendingSpeechAfterThinking] = useState(false)
  const [thinkingHoldUntil, setThinkingHoldUntil] = useState<number>(0)
  const [isMicHoldActive, setIsMicHoldActive] = useState(false)
  const [minimalSettingsOpen, setMinimalSettingsOpen] = useState(false)
  const [minimalCaptionsEnabled, setMinimalCaptionsEnabled] = useState(() =>
    resolveStoredFlag(MINIMAL_CAPTIONS_STORAGE_KEY, false),
  )
  const [minimalCaptionStickyText, setMinimalCaptionStickyText] = useState("")
  const [minimalDetailEnabled, setMinimalDetailEnabled] = useState(() =>
    resolveStoredFlag(MINIMAL_DETAIL_STORAGE_KEY, false),
  )
  const [historySidebarOpen, setHistorySidebarOpen] = useState(false)
  const [sessionHistory, setSessionHistory] = useState<SessionConversationHistory[]>(readStoredSessionHistory)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState("")
  const [historyEndpointSupported, setHistoryEndpointSupported] = useState(true)
  const [selectedHistorySessionId, setSelectedHistorySessionId] = useState<string | null>(null)
  const [isSwitchingSession, setIsSwitchingSession] = useState(false)
  const [lastError, setLastError] = useState("")
  const [ttsVisualCompletedUntil, setTtsVisualCompletedUntil] = useState<number>(0)
  const [engineReadiness, setEngineReadiness] = useState<EngineReadiness>({
    checked: false,
    ok: false,
    message: "Checking runtime engine availability...",
  })
  const effectiveQueuePolicy = DEMO_FORCE_SEND_NOW_DEFAULT
    ? "send_now"
    : (mode === "minimal" ? "send_now" : queuePolicy)

  const sessionRef = useRef<WebVoiceSession | null>(null)
  const agentRef = useRef<VoiceAgent | null>(null)
  const sessionStoreUnsubscribeRef = useRef<(() => void) | null>(null)
  const transcriptUnsubscribeRef = useRef<(() => void) | null>(null)
  const pendingTurnUnsubscribeRef = useRef<(() => void) | null>(null)
  const pendingTurnTracePhaseRef = useRef<PendingTurnPhase>("idle")
  const sttProgressUnsubscribeRef = useRef<(() => void) | null>(null)
  const sttFinalMetaUnsubscribeRef = useRef<(() => void) | null>(null)
  const routeStateUnsubscribeRef = useRef<(() => void) | null>(null)
  const agentSignalUnsubscribeRef = useRef<(() => void) | null>(null)
  const agentRealtimeSignalUnsubscribeRef = useRef<(() => void) | null>(null)
  const sessionStateUnsubscribeRef = useRef<(() => void) | null>(null)
  const turnPhaseUnsubscribeRef = useRef<(() => void) | null>(null)
  const sdkPlayerRef = useRef<AudioOutputAdapter | null>(null)
  const micRef = useRef<DemoMicInput | null>(null)
  const thinkingPlayerRef = useRef<ThinkingAudioPlayer | null>(null)
  const seenUserFinalRef = useRef<string>("")
  const activeAssistantTurnIdRef = useRef<string | null>(null)
  const interruptionInFlightRef = useRef(false)
  const ttsPlayingRef = useRef(false)
  const ttsStreamActiveRef = useRef(false)
  const pendingSpeechAfterThinkingRef = useRef(false)
  const llmThinkingActiveRef = useRef(false)
  const thinkingHoldUntilRef = useRef(0)
  const lastUserSpeechAtRef = useRef(0)
  const speechWatchdogTimerRef = useRef<number | null>(null)
  const idleTransitionTimerRef = useRef<number | null>(null)
  const micHoldTimerRef = useRef<number | null>(null)
  const vadSpeechStartedAtRef = useRef(0)
  const lastAutoCommitAtRef = useRef(0)
  const micLifecycleTokenRef = useRef(0)
  const isDisconnectingRef = useRef(false)
  const isConnectingRef = useRef(false)
  const startupCommittedRef = useRef(false)
  const interruptionEpochRef = useRef(0)
  const generationWatchdogTimerRef = useRef<number | null>(null)
  const micHoldActiveRef = useRef(false)
  const suppressMicTapRef = useRef(false)
  const suppressTtsUntilNextUserFinalRef = useRef(false)
  const activeGenerationIdRef = useRef<string | null>(null)
  const rejectedGenerationIdsRef = useRef<Set<string>>(new Set())
  const minimalMicUserControlledRef = useRef(false)
  const sessionStatusRef = useRef(sessionStatus)
  const turnPhaseRef = useRef<TurnPhase>(turnPhase)
  const localUserSpeechVisualUntilRef = useRef(0)
  const traceReporterRef = useRef<FrontendTraceReporter | null>(null)
  const disconnectForCleanupRef = useRef<(() => Promise<void>) | null>(null)
  const minimalSettingsRef = useRef<HTMLDivElement | null>(null)
  const historySidebarRef = useRef<HTMLDivElement | null>(null)
  const llmResponseTextRef = useRef("")
  const transcriptRef = useRef<TranscriptItem[]>([])
  const historyTranscriptRequestedRef = useRef<Set<string>>(new Set())
  const requestedSessionLoadAttemptRef = useRef<string | null>(null)
  const turnPhaseLastSetAtRef = useRef(0)
  const turnPhaseStickyUntilRef = useRef(0)
  const localBargeInFramesRef = useRef(0)
  const localBargeInCooldownUntilRef = useRef(0)
  const localBargeInNoiseFloorRef = useRef(0)
  const recentLocalUiSpeechAtRef = useRef(0)
  const allowAutoInterruptUntilRef = useRef(0)
  const bargeInSpeechActiveRef = useRef(false)
  const lastSpokenErrorKeyRef = useRef("")
  const lastSpokenErrorAtRef = useRef(0)
  const lastToolUpdateKeyRef = useRef("")
  const lastSpokenToolHintKeyRef = useRef("")
  const lastSpokenToolHintAtRef = useRef(0)
  const suppressErrorSpeechUntilRef = useRef(0)
  const ignoreAssistantUpdatesUntilRef = useRef(0)
  const errorSpeechFallbackTimerRef = useRef<number | null>(null)
  const interruptionPolicyRef = useRef(new InterruptionPolicy({ minWords: DEMO_INTERRUPT_MIN_WORDS, cooldownMs: 320 }))
  const eventBufferRef = useRef<string[]>([])
  const eventFlushTimerRef = useRef<number | null>(null)
  const sttUiTextRef = useRef("")
  const sttUiLastPaintAtRef = useRef(0)
  const sttUiTimerRef = useRef<number | null>(null)
  const llmThinkingUiTextRef = useRef("")
  const llmThinkingUiLastPaintAtRef = useRef(0)
  const llmThinkingUiTimerRef = useRef<number | null>(null)
  const llmResponseUiLastPaintAtRef = useRef(0)
  const llmResponseUiTimerRef = useRef<number | null>(null)
  const micLevelUiLastPaintAtRef = useRef(0)
  const micLevelUiPendingRef = useRef(0)
  const micLevelUiTimerRef = useRef<number | null>(null)
  const micStartInFlightRef = useRef(false)
  const freeMicWantedRef = useRef(true)
  const lastMicChunkAtRef = useRef(0)

  const clearSpeakingFlags = useCallback(() => {
    ttsPlayingRef.current = false
    setTtsPlaybackActive(false)
    ttsStreamActiveRef.current = false
    setTtsStreamActive(false)
    setTtsVisualCompletedUntil(0)
  }, [setTtsPlaybackActive, setTtsStreamActive])

  const isMicCaptureStale = useCallback(() => {
    if (!micRef.current) {
      return false
    }
    const lastChunkAt = lastMicChunkAtRef.current
    if (lastChunkAt <= 0) {
      return false
    }
    return Date.now() - lastChunkAt > DEMO_MIC_CAPTURE_STALE_MS
  }, [])

  const canConnect = !sessionRef.current

  const micButtonVisualState: "off" | "on" | "hold" = isMicHoldActive
    ? "hold"
    : isListening
      ? "on"
      : "off"
  const isAssistantFlowActive =
    turnPhase === "processing"
    || turnPhase === "agent_speaking"
    || ttsPlaybackActive
    || ttsStreamActive
    || pendingSpeechAfterThinking
    || llmThinkingActive
  const backendProcessingLike =
    llmThinkingActive
    || pendingSpeechAfterThinking
    || pendingTurnPhase !== "idle"
    || sessionStatus === "thinking"
    || sessionStatus === "transcribing"
  const visualTurnPhase = useMemo<TurnPhase>(() => {
    const now = Date.now()
    const localUserSpeechActive = now < localUserSpeechVisualUntilRef.current && Boolean(micRef.current)
    const recentUserSpeech = now - lastUserSpeechAtRef.current <= 220 && Boolean(micRef.current)
    // If user is actively speaking, respect that state
    if (turnPhase === "user_speaking" && (recentUserSpeech || localUserSpeechActive)) {
      return "user_speaking"
    }
    if (ttsPlaybackActive || ttsStreamActive) {
      return "agent_speaking"
    }
    // Never force listening/idle while still processing or speaking.
    if (turnPhase === "processing" || turnPhase === "agent_speaking") {
      return turnPhase
    }
    if (Date.now() <= ttsVisualCompletedUntil) {
      return "agent_speaking"
    }
    if (backendProcessingLike) {
      return "processing"
    }
    if (
      llmResponseCompletedAt > 0
      && Date.now() - llmResponseCompletedAt > 250
      && !ttsPlaybackActive
      && !ttsStreamActive
    ) {
      return sessionRef.current && micRef.current ? "listening" : "idle"
    }
    return turnPhase
  }, [backendProcessingLike, llmResponseCompletedAt, turnPhase, ttsPlaybackActive, ttsStreamActive, ttsVisualCompletedUntil])
  const isMicDisconnectedView =
    Boolean(sessionRef.current)
    && !isListening
    && !isMicHoldActive
    && !isAssistantFlowActive
  const routeModelLabel = routeProvider || routeModel
    ? `${routeProvider ?? "-"}/${routeModel ?? "-"}`
    : "-"
  const sessionStatusLabel = useMemo(() => {
    if (sessionStatus.startsWith("error:") || sessionStatus.startsWith("connect failed:")) {
      return sessionStatus
    }
    if (sessionStatus === "disconnected") return "disconnected"
    if (ttsPlaybackActive || ttsStreamActive) return "speaking"
    if (visualTurnPhase === "agent_speaking") return "speaking"
    if (llmThinkingActive) return "thinking"
    if (visualTurnPhase === "processing") return "thinking"
    if (visualTurnPhase === "user_speaking") return "listening"
    return sessionStatus
  }, [llmThinkingActive, sessionStatus, ttsPlaybackActive, ttsStreamActive, visualTurnPhase])

  const refreshSessionHistory = useCallback(async (opts?: { preserveError?: boolean }) => {
    const runtimeBaseUrl = baseUrl.trim()
    if (!historyEndpointSupported) {
      setHistoryLoading(false)
      return
    }
    if (!runtimeBaseUrl) {
      historyTranscriptRequestedRef.current.clear()
      setSessionHistory((prev) => (prev.length > 0 ? prev : readStoredSessionHistory()))
      setHistoryLoading(false)
      if (!opts?.preserveError) {
        setHistoryError("")
      }
      return
    }

    setHistoryLoading(true)
    if (!opts?.preserveError) {
      setHistoryError("")
    }

    try {
      const client = new OpenVoiceWebClient({ baseUrl: runtimeBaseUrl })
      const rows = await client.http.listSessions(SESSION_HISTORY_LIMIT)
      setHistoryEndpointSupported(true)
      const mapped = rows.map(buildSessionHistoryEntry)
      setSessionHistory((prev) => {
        const baseRows = dedupeAndSortHistory([...prev, ...mapped])
        const previousById = new Map(prev.map((item) => [item.sessionId, item]))
        const incomingIds = new Set(baseRows.map((item) => item.sessionId))
        for (const existingId of Array.from(historyTranscriptRequestedRef.current)) {
          if (!incomingIds.has(existingId)) {
            historyTranscriptRequestedRef.current.delete(existingId)
          }
        }
        return baseRows.map((item) => {
          const previous = previousById.get(item.sessionId)
          if (!previous) {
            return item
          }
          if (item.sessionId === sessionRef.current?.sessionId) {
            return {
              ...item,
              transcript: transcriptRef.current,
            }
          }
          return {
            ...item,
            transcript: previous.transcript,
          }
        })
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      if (/\b405\b/.test(message)) {
        setHistoryEndpointSupported(false)
        const fallback = readStoredSessionHistory()
        setSessionHistory((prev) => dedupeAndSortHistory([...prev, ...fallback]))
        setHistoryError(
          "Live history sync is unavailable on this backend. Showing locally saved chat history.",
        )
        setHistoryLoading(false)
        return
      }
      setHistoryError(`Could not load chat history: ${message}`)
    } finally {
      setHistoryLoading(false)
    }
  }, [baseUrl, historyEndpointSupported])

  const loadSessionTranscript = useCallback(async (
    targetSessionId: string,
    opts?: { setActiveTranscript?: boolean },
  ) => {
    const runtimeBaseUrl = baseUrl.trim()
    if (!historyEndpointSupported || !runtimeBaseUrl || !targetSessionId.trim()) {
      return
    }

    if (!opts?.setActiveTranscript && historyTranscriptRequestedRef.current.has(targetSessionId)) {
      return
    }
    historyTranscriptRequestedRef.current.add(targetSessionId)

    try {
      const client = new OpenVoiceWebClient({ baseUrl: runtimeBaseUrl })
      const turns = await client.http.listSessionTurns(targetSessionId, SESSION_TRANSCRIPT_LIMIT)
      const nextTranscript = transcriptFromHistoryTurns(turns)
      
      // Store the session reference to avoid race conditions
      const currentSession = sessionRef.current
      if (opts?.setActiveTranscript && currentSession && targetSessionId === currentSession.sessionId) {
        currentSession.store.dispatch(toSetTranscriptAction(nextTranscript))
      }
      setSessionHistory((prev) => prev.map((item) => (
        item.sessionId === targetSessionId
          ? { ...item, transcript: nextTranscript }
          : item
      )))
    } catch (error) {
      if (opts?.setActiveTranscript) {
        const message = error instanceof Error ? error.message : String(error)
        setHistoryError(`Could not load transcript for selected session: ${message}`)
      }
      // Keep the current history list if transcript fetch fails.
    }
  }, [baseUrl, historyEndpointSupported])

  const currentSessionHistory = useMemo(() => {
    if (!sessionRef.current) {
      return null
    }
    const activeSessionId = sessionRef.current.sessionId
    const fromList = sessionHistory.find((item) => item.sessionId === activeSessionId)
    if (fromList) {
      if (transcript.length > 0) {
        return {
          ...fromList,
          transcript,
        }
      }
      return fromList
    }

    if (transcript.length === 0) {
      return null
    }

    const lastUserText = latestTranscriptByRole(transcript, "user")
    const lastAssistantText = latestTranscriptByRole(transcript, "assistant")
    const userTurns = transcript.filter((item) => item.role === "user").length
    const assistantTurns = transcript.filter((item) => item.role === "assistant").length

    return {
      sessionId: activeSessionId,
      title: lastUserText ? trimText(lastUserText, 80) : `Session ${activeSessionId.slice(0, 8)}`,
      status: sessionStatusRef.current,
      updatedAt: new Date().toISOString(),
      turnCount: Math.max(userTurns, assistantTurns),
      completedTurnCount: Math.min(userTurns, assistantTurns),
      lastUserText,
      lastAssistantText,
      transcript,
    }
  }, [sessionHistory, transcript])

  const recentHistoryItems = useMemo(() => {
    const activeSessionId = sessionRef.current?.sessionId
    if (!activeSessionId) {
      return sessionHistory.slice(0, SESSION_HISTORY_LIMIT)
    }

    const withoutActive = sessionHistory.filter((item) => item.sessionId !== activeSessionId)
    if (currentSessionHistory) {
      return [currentSessionHistory, ...withoutActive].slice(0, SESSION_HISTORY_LIMIT)
    }
    return withoutActive.slice(0, SESSION_HISTORY_LIMIT)
  }, [currentSessionHistory, sessionHistory])

  const selectedHistorySession = useMemo(() => {
    if (!selectedHistorySessionId) {
      return null
    }
    return recentHistoryItems.find((item) => item.sessionId === selectedHistorySessionId) ?? null
  }, [recentHistoryItems, selectedHistorySessionId])

  const selectHistorySession = useCallback((targetSessionId: string) => {
    const normalized = targetSessionId.trim()
    if (!normalized) {
      return
    }
    setSelectedHistorySessionId(normalized)
    if (normalized !== sessionRef.current?.sessionId) {
      void loadSessionTranscript(normalized)
    }
  }, [loadSessionTranscript])

  const openHistoryPanel = useCallback(() => {
    if (!historyEndpointSupported) {
      setHistoryEndpointSupported(true)
    }
    setSelectedHistorySessionId(sessionRef.current?.sessionId ?? null)
    setHistorySidebarOpen(true)
    setSessionHistory((prev) => (prev.length > 0 ? prev : readStoredSessionHistory()))
    void refreshSessionHistory({ preserveError: true })
  }, [historyEndpointSupported, refreshSessionHistory])

  const closeHistoryPanel = useCallback(() => {
    setHistorySidebarOpen(false)
  }, [])

  const checkEngineReadiness = useCallback(async (runtimeBaseUrl: string) => {
    const client = new OpenVoiceWebClient({ baseUrl: runtimeBaseUrl })
    const catalog = await client.http.listEngines()

    const defaultStt = catalog.stt.find((entry) => entry.default) ?? catalog.stt[0]
    const sttOk = Boolean(defaultStt?.available)
    const sttReason = defaultStt ? `${defaultStt.id} (${defaultStt.status})` : "none"

    const vadEntries = catalog.vad ?? []
    const defaultVad = vadEntries.find((entry) => entry.default) ?? vadEntries[0]
    const vadRequired = vadEntries.length > 0
    const vadOk = !vadRequired || Boolean(defaultVad?.available)
    const vadReason = defaultVad ? `${defaultVad.id} (${defaultVad.status})` : "none"

    if (sttOk && vadOk) {
      setEngineReadiness({
        checked: true,
        ok: true,
        message: "Realtime STT/VAD engines are ready.",
      })
      return
    }

    const reasonParts = [
      `stt=${sttReason}`,
      vadRequired ? `vad=${vadReason}` : null,
    ].filter(Boolean)

    setEngineReadiness({
      checked: true,
      ok: false,
      message: `Realtime engines unavailable (${reasonParts.join(", ")}). Install backend deps for your configured STT/VAD engines (for example moonshine-voice + silero-vad, or Parakeet + silero-vad).`,
    })
  }, [])

  useEffect(() => {
    sessionStatusRef.current = sessionStatus
  }, [sessionStatus])

  useEffect(() => {
    turnPhaseRef.current = turnPhase
  }, [turnPhase])

  useEffect(() => {
    if (ttsVisualCompletedUntil <= Date.now()) {
      return
    }
    const remainingMs = Math.max(0, ttsVisualCompletedUntil - Date.now())
    const timer = window.setTimeout(() => {
      setTtsVisualCompletedUntil(0)
    }, remainingMs)
    return () => {
      window.clearTimeout(timer)
    }
  }, [ttsVisualCompletedUntil])

  useEffect(() => {
    transcriptRef.current = transcript
  }, [transcript])

  useEffect(() => {
    const activeSessionId = sessionRef.current?.sessionId
    if (!activeSessionId || transcript.length === 0) {
      return
    }

    const lastUserText = latestTranscriptByRole(transcript, "user")
    const lastAssistantText = latestTranscriptByRole(transcript, "assistant")
    const userTurns = transcript.filter((item) => item.role === "user").length
    const assistantTurns = transcript.filter((item) => item.role === "assistant").length
    const updatedAt = new Date().toISOString()

    setSessionHistory((prev) => {
      const updated: SessionConversationHistory = {
        sessionId: activeSessionId,
        title: lastUserText ? trimText(lastUserText, 80) : `Session ${activeSessionId.slice(0, 8)}`,
        status: sessionStatusRef.current,
        updatedAt,
        turnCount: Math.max(userTurns, assistantTurns),
        completedTurnCount: Math.min(userTurns, assistantTurns),
        lastUserText,
        lastAssistantText,
        transcript: transcript.slice(-SESSION_TRANSCRIPT_LIMIT),
      }

      return dedupeAndSortHistory([
        updated,
        ...prev.filter((item) => item.sessionId !== activeSessionId),
      ])
    })
  }, [transcript])

  useEffect(() => {
    const activeSessionId = sessionRef.current?.sessionId
    if (!activeSessionId) {
      return
    }
    setSessionHistory((prev) => {
      const nowIso = new Date().toISOString()
      const existing = prev.find((item) => item.sessionId === activeSessionId)
      const nextItem: SessionConversationHistory = existing
        ? {
            ...existing,
            status: sessionStatus,
            updatedAt: nowIso,
          }
        : {
            sessionId: activeSessionId,
            title: `Session ${activeSessionId.slice(0, 8)}`,
            status: sessionStatus,
            updatedAt: nowIso,
            turnCount: 0,
            completedTurnCount: 0,
            lastUserText: null,
            lastAssistantText: null,
            transcript: [],
          }

      return dedupeAndSortHistory([
        nextItem,
        ...prev.filter((item) => item.sessionId !== activeSessionId),
      ])
    })
  }, [sessionStatus])

  useEffect(() => {
    writeStoredSessionHistory(sessionHistory)
  }, [sessionHistory])

  useEffect(() => {
    if (!historySidebarOpen) {
      return
    }
    if (selectedHistorySessionId && recentHistoryItems.some((item) => item.sessionId === selectedHistorySessionId)) {
      return
    }
    if (recentHistoryItems.length === 0) {
      return
    }
    setSelectedHistorySessionId(recentHistoryItems[0].sessionId)
  }, [historySidebarOpen, recentHistoryItems, selectedHistorySessionId])

  useEffect(() => {
    if (!historySidebarOpen || !selectedHistorySession) {
      return
    }
    if (selectedHistorySession.transcript.length > 0) {
      return
    }
    if (selectedHistorySession.sessionId === sessionRef.current?.sessionId) {
      return
    }
    void loadSessionTranscript(selectedHistorySession.sessionId)
  }, [historySidebarOpen, loadSessionTranscript, selectedHistorySession])

  useEffect(() => {
    localStorage.setItem(
      MINIMAL_CAPTIONS_STORAGE_KEY,
      minimalCaptionsEnabled ? "1" : "0",
    )
  }, [minimalCaptionsEnabled])

  useEffect(() => {
    localStorage.setItem(
      MINIMAL_DETAIL_STORAGE_KEY,
      minimalDetailEnabled ? "1" : "0",
    )
  }, [minimalDetailEnabled])

  useEffect(() => {
    if (mode !== "minimal") {
      setHistorySidebarOpen(false)
      return
    }
    void refreshSessionHistory()
  }, [mode, refreshSessionHistory])

  useEffect(() => {
    if (!historySidebarOpen || mode !== "minimal") {
      return
    }
    void refreshSessionHistory({ preserveError: true })
  }, [historySidebarOpen, mode, refreshSessionHistory])

  useEffect(() => {
    if (mode !== "minimal") {
      return
    }
    setSessionHistory((prev) => {
      const activeSessionId = sessionRef.current?.sessionId
      if (!activeSessionId) {
        return prev
      }
      return prev.map((item) => (
        item.sessionId === activeSessionId
          ? { ...item, transcript }
          : item
      ))
    })
  }, [mode, transcript])

  useEffect(() => {
    if (!historySidebarOpen || mode !== "minimal") {
      return
    }
    const timer = window.setInterval(() => {
      void refreshSessionHistory({ preserveError: true })
    }, 6000)
    return () => {
      window.clearInterval(timer)
    }
  }, [historySidebarOpen, mode, refreshSessionHistory])

  useEffect(() => {
    if (mode !== "minimal") {
      return
    }
    for (const item of recentHistoryItems) {
      if (item.transcript.length > 0) {
        continue
      }
      if (item.sessionId === sessionRef.current?.sessionId) {
        continue
      }
      void loadSessionTranscript(item.sessionId)
    }
  }, [loadSessionTranscript, mode, recentHistoryItems])

  useEffect(() => {
    thinkingHoldUntilRef.current = thinkingHoldUntil
  }, [thinkingHoldUntil])

  useEffect(() => {
    llmThinkingActiveRef.current = llmThinkingActive
  }, [llmThinkingActive])

  const shouldPlayThinkingCue = useMemo(() => {
    return (
      sessionStatus === "thinking"
      && turnPhase === "processing"
      && !ttsPlaybackActive
      && !ttsStreamActive
    )
  }, [sessionStatus, ttsPlaybackActive, ttsStreamActive, turnPhase])

  useEffect(() => {
    if (!thinkingPlayerRef.current) {
      thinkingPlayerRef.current = new ThinkingAudioPlayer(thinkingCueUrl)
    }
    const player = thinkingPlayerRef.current
    if (!player) {
      return
    }
    if (shouldPlayThinkingCue) {
      void player.start()
      return
    }
    player.stop()
  }, [shouldPlayThinkingCue])

  useEffect(() => {
    const text = sttLiveText.trim()
    if (!text) {
      return
    }
    setMinimalCaptionStickyText(text)
  }, [sttLiveText])

  const clearEventFlushTimer = useCallback(() => {
    if (eventFlushTimerRef.current !== null) {
      window.clearTimeout(eventFlushTimerRef.current)
      eventFlushTimerRef.current = null
    }
  }, [])

  const flushEventBuffer = useCallback(() => {
    clearEventFlushTimer()
    if (eventBufferRef.current.length === 0) {
      return
    }
    const batch = eventBufferRef.current
    eventBufferRef.current = []
    setEvents((prev) => {
      const next = [...prev, ...batch]
      return next.length > DEMO_EVENT_TRACE_MAX_ITEMS
        ? next.slice(-DEMO_EVENT_TRACE_MAX_ITEMS)
        : next
    })
  }, [clearEventFlushTimer])

  const queueEventLine = useCallback((line: string) => {
    eventBufferRef.current.push(line)
    if (eventBufferRef.current.length >= DEMO_EVENT_TRACE_MAX_ITEMS) {
      flushEventBuffer()
      return
    }
    if (eventFlushTimerRef.current !== null) {
      return
    }
    eventFlushTimerRef.current = window.setTimeout(() => {
      eventFlushTimerRef.current = null
      flushEventBuffer()
    }, DEMO_EVENT_TRACE_FLUSH_MS)
  }, [flushEventBuffer])

  const clearSttUiTimer = useCallback(() => {
    if (sttUiTimerRef.current !== null) {
      window.clearTimeout(sttUiTimerRef.current)
      sttUiTimerRef.current = null
    }
  }, [])

  const flushSttUiText = useCallback(() => {
    clearSttUiTimer()
    sttUiLastPaintAtRef.current = Date.now()
    setSttLiveText(sttUiTextRef.current)
  }, [clearSttUiTimer])

  const queueSttUiText = useCallback((text: string, immediate = false) => {
    sttUiTextRef.current = text
    const now = Date.now()
    const elapsed = now - sttUiLastPaintAtRef.current
    if (immediate || elapsed >= DEMO_UI_STT_TEXT_THROTTLE_MS) {
      flushSttUiText()
      return
    }
    if (sttUiTimerRef.current !== null) {
      return
    }
    sttUiTimerRef.current = window.setTimeout(() => {
      sttUiTimerRef.current = null
      flushSttUiText()
    }, DEMO_UI_STT_TEXT_THROTTLE_MS - elapsed)
  }, [flushSttUiText])

  const clearLlmThinkingUiTimer = useCallback(() => {
    if (llmThinkingUiTimerRef.current !== null) {
      window.clearTimeout(llmThinkingUiTimerRef.current)
      llmThinkingUiTimerRef.current = null
    }
  }, [])

  const flushLlmThinkingUiText = useCallback(() => {
    clearLlmThinkingUiTimer()
    llmThinkingUiLastPaintAtRef.current = Date.now()
    setLlmThinkingText(llmThinkingUiTextRef.current)
  }, [clearLlmThinkingUiTimer])

  const queueLlmThinkingUiText = useCallback((immediate = false) => {
    const now = Date.now()
    const elapsed = now - llmThinkingUiLastPaintAtRef.current
    if (immediate || elapsed >= DEMO_UI_LLM_DELTA_THROTTLE_MS) {
      flushLlmThinkingUiText()
      return
    }
    if (llmThinkingUiTimerRef.current !== null) {
      return
    }
    llmThinkingUiTimerRef.current = window.setTimeout(() => {
      llmThinkingUiTimerRef.current = null
      flushLlmThinkingUiText()
    }, DEMO_UI_LLM_DELTA_THROTTLE_MS - elapsed)
  }, [flushLlmThinkingUiText])

  const clearLlmResponseUiTimer = useCallback(() => {
    if (llmResponseUiTimerRef.current !== null) {
      window.clearTimeout(llmResponseUiTimerRef.current)
      llmResponseUiTimerRef.current = null
    }
  }, [])

  const flushLlmResponseUiText = useCallback(() => {
    clearLlmResponseUiTimer()
    llmResponseUiLastPaintAtRef.current = Date.now()
    setLlmResponseText(llmResponseTextRef.current)
  }, [clearLlmResponseUiTimer])

  const queueLlmResponseUiText = useCallback((immediate = false) => {
    const now = Date.now()
    const elapsed = now - llmResponseUiLastPaintAtRef.current
    if (immediate || elapsed >= DEMO_UI_LLM_DELTA_THROTTLE_MS) {
      flushLlmResponseUiText()
      return
    }
    if (llmResponseUiTimerRef.current !== null) {
      return
    }
    llmResponseUiTimerRef.current = window.setTimeout(() => {
      llmResponseUiTimerRef.current = null
      flushLlmResponseUiText()
    }, DEMO_UI_LLM_DELTA_THROTTLE_MS - elapsed)
  }, [flushLlmResponseUiText])

  const clearMicLevelUiTimer = useCallback(() => {
    if (micLevelUiTimerRef.current !== null) {
      window.clearTimeout(micLevelUiTimerRef.current)
      micLevelUiTimerRef.current = null
    }
  }, [])

  const clearErrorSpeechFallbackTimer = useCallback(() => {
    if (errorSpeechFallbackTimerRef.current !== null) {
      window.clearTimeout(errorSpeechFallbackTimerRef.current)
      errorSpeechFallbackTimerRef.current = null
    }
  }, [])

  const flushMicLevelUi = useCallback(() => {
    clearMicLevelUiTimer()
    micLevelUiLastPaintAtRef.current = Date.now()
    setMicLevel(micLevelUiPendingRef.current)
  }, [clearMicLevelUiTimer])

  const queueMicLevelUi = useCallback((nextLevel: number, immediate = false) => {
    micLevelUiPendingRef.current = nextLevel
    const now = Date.now()
    const elapsed = now - micLevelUiLastPaintAtRef.current
    if (immediate || elapsed >= DEMO_UI_MIC_LEVEL_THROTTLE_MS) {
      flushMicLevelUi()
      return
    }
    if (micLevelUiTimerRef.current !== null) {
      return
    }
    micLevelUiTimerRef.current = window.setTimeout(() => {
      micLevelUiTimerRef.current = null
      flushMicLevelUi()
    }, DEMO_UI_MIC_LEVEL_THROTTLE_MS - elapsed)
  }, [flushMicLevelUi])

  const resetAssistantPanels = useCallback((turnId?: string | null) => {
    const normalizedTurnId = turnId ?? null
    if (normalizedTurnId && activeAssistantTurnIdRef.current === normalizedTurnId) {
      return
    }
    activeAssistantTurnIdRef.current = normalizedTurnId
    lastToolUpdateKeyRef.current = ""
    llmThinkingUiTextRef.current = ""
    llmResponseTextRef.current = ""
    setToolActivity("", "")
    flushLlmThinkingUiText()
    flushLlmResponseUiText()
  }, [flushLlmResponseUiText, flushLlmThinkingUiText, setToolActivity])

  const clearSpeechWatchdog = useCallback(() => {
    if (speechWatchdogTimerRef.current !== null) {
      window.clearTimeout(speechWatchdogTimerRef.current)
      speechWatchdogTimerRef.current = null
    }
  }, [])

  const clearMicHoldTimer = useCallback(() => {
    if (micHoldTimerRef.current !== null) {
      window.clearTimeout(micHoldTimerRef.current)
      micHoldTimerRef.current = null
    }
  }, [])

  const clearIdleTransitionTimer = useCallback(() => {
    if (idleTransitionTimerRef.current !== null) {
      window.clearTimeout(idleTransitionTimerRef.current)
      idleTransitionTimerRef.current = null
    }
  }, [])

  const hasAssistantFlowActive = useCallback(() => {
    const withinThinkingHold = Date.now() < thinkingHoldUntilRef.current
    return (
      turnPhaseRef.current === "processing"
      || turnPhaseRef.current === "agent_speaking"
      || pendingSpeechAfterThinkingRef.current
      || llmThinkingActiveRef.current
      || ttsPlayingRef.current
      || ttsStreamActiveRef.current
      || Boolean(thinkingPlayerRef.current?.isPlaying())
      || withinThinkingHold
    )
  }, [])

  const scheduleIdleTransition = useCallback(() => {
    clearIdleTransitionTimer()
    idleTransitionTimerRef.current = window.setTimeout(() => {
      idleTransitionTimerRef.current = null
      if (micRef.current) return
      if (hasAssistantFlowActive()) return
      const sawRecentUserSpeech =
        Date.now() - lastUserSpeechAtRef.current <= DEMO_POST_RELEASE_PROCESSING_GRACE_MS
      if (sawRecentUserSpeech) return
      setTurnPhase("idle")
    }, DEMO_IDLE_TRANSITION_DELAY_MS)
  }, [clearIdleTransitionTimer, hasAssistantFlowActive])

  const setTurnPhaseStable = useCallback((phase: TurnPhase) => {
    // SDK selectTurnPhase is the canonical source of truth.
    // Also keep a local override so the UI can react immediately to local mic
    // evidence instead of waiting for a backend VAD round-trip.
    const now = Date.now()
    turnPhaseLastSetAtRef.current = now
    if (phase === "agent_speaking") {
      turnPhaseStickyUntilRef.current = now + DEMO_PHASE_DEBOUNCE_MS
    } else if (phase === "processing") {
      turnPhaseStickyUntilRef.current = now + Math.max(DEMO_PHASE_DEBOUNCE_MS, 220)
    } else {
      turnPhaseStickyUntilRef.current = now
    }
    if (turnPhaseRef.current !== phase) {
      setTurnPhase(phase)
    }
  }, [setTurnPhase])

  const clearGenerationWatchdog = useCallback(() => {
    if (generationWatchdogTimerRef.current !== null) {
      window.clearTimeout(generationWatchdogTimerRef.current)
      generationWatchdogTimerRef.current = null
    }
  }, [])

  const pushTraceLocal = useCallback((type: string, payload: unknown, kind = "ui.action") => {
    traceReporterRef.current?.trackLocal(type, payload, kind)
  }, [])

  const rejectGeneration = useCallback((generationId: string | null) => {
    if (!generationId) return
    rejectedGenerationIdsRef.current.add(generationId)
    if (rejectedGenerationIdsRef.current.size > 64) {
      const oldest = rejectedGenerationIdsRef.current.values().next().value
      if (oldest) {
        rejectedGenerationIdsRef.current.delete(oldest)
      }
    }
  }, [])

  const hardStopPlayback = useCallback(async () => {
    pushTraceLocal("audio.output.flush", { reason: "hard_stop" }, "audio")
    rejectGeneration(activeGenerationIdRef.current)
    await sdkPlayerRef.current?.flush().catch(() => undefined)
    thinkingPlayerRef.current?.stop()
  }, [pushTraceLocal, rejectGeneration])

  const hardStopPlaybackNow = useCallback(() => {
    const epoch = ++interruptionEpochRef.current
    pushTraceLocal(
      "audio.output.flush_now",
      { reason: "interrupt_now", epoch },
      "audio",
    )
    rejectGeneration(activeGenerationIdRef.current)
    ttsPlayingRef.current = false
    setTtsPlaybackActive(false)
    ttsStreamActiveRef.current = false
    setTtsStreamActive(false)
    setTtsVisualCompletedUntil(0)
    pendingSpeechAfterThinkingRef.current = false
    setPendingSpeechAfterThinking(false)
    thinkingPlayerRef.current?.stop()
    try {
      if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel()
      }
    } catch {
      // best-effort browser speech cancellation
    }
    void sdkPlayerRef.current?.flush().catch(() => undefined)
    // now derived from SDK state
    setTurnPhaseStable(sessionRef.current && micRef.current ? "listening" : "idle")
  }, [clearSpeakingFlags, pushTraceLocal, rejectGeneration, setTurnPhaseStable])

  const announceLlmError = useCallback((message: string, code?: string | null) => {
    const normalizedMessage = message.trim() || "unknown error"
    const codePart = code ? ` (${code})` : ""
    const normalizedJoined = `${code ?? ""} ${normalizedMessage}`.toLowerCase()
    const isRateLimitLike = normalizedJoined.includes("rate limit")
    ignoreAssistantUpdatesUntilRef.current = Date.now() + 1800

    clearGenerationWatchdog()
    clearSpeechWatchdog()
    interruptionInFlightRef.current = false
    bargeInSpeechActiveRef.current = false
    suppressTtsUntilNextUserFinalRef.current = false
    activeGenerationIdRef.current = null
    pendingSpeechAfterThinkingRef.current = false
    setPendingSpeechAfterThinking(false)
    llmThinkingUiTextRef.current = ""
    llmResponseTextRef.current = ""
    clearLlmThinkingUiTimer()
    clearLlmResponseUiTimer()
    flushLlmThinkingUiText()
    flushLlmResponseUiText()
    setLlmThinkingActive(false)
    setThinkingHoldUntil(0)

    thinkingPlayerRef.current?.stop()
    ttsPlayingRef.current = false
    setTtsPlaybackActive(false)
    ttsStreamActiveRef.current = false
    setTtsStreamActive(false)
    setTtsVisualCompletedUntil(0)
    hardStopPlaybackNow()

    setLastError(`llm error: ${normalizedMessage}${codePart}`)
    setSessionStatus(sessionRef.current ? (micRef.current ? "listening" : "ready") : "disconnected")
    setTurnPhaseStable(sessionRef.current && micRef.current ? "listening" : "idle")

    const spokenMessage = buildSpokenLlmErrorMessage(normalizedMessage, code)
    const dedupeKey = `${code ?? ""}:${spokenMessage}`.toLowerCase()
    const now = Date.now()
    const isTimeoutError = (code ?? "").toLowerCase() === "timeout"
    const isProviderLikeError = normalizedJoined.includes("provider_error") || normalizedJoined.includes("provider error") || normalizedJoined.includes("all accounts are currently unavailable")
    const underGlobalCooldown = now < suppressErrorSpeechUntilRef.current
    const shouldSpeak = !underGlobalCooldown && (isTimeoutError || dedupeKey !== lastSpokenErrorKeyRef.current || now - lastSpokenErrorAtRef.current > 5000)
    const liveMicOrSessionActive = Boolean(
      micRef.current
      || (sessionRef.current && sessionStatusRef.current !== "disconnected")
    )
    if (shouldSpeak) {
      lastSpokenErrorKeyRef.current = dedupeKey
      lastSpokenErrorAtRef.current = now
      if (isRateLimitLike) {
        suppressErrorSpeechUntilRef.current = now + DEMO_ERROR_SPEECH_COOLDOWN_MS
      }

      // Do not create a synthetic runtime TTS turn for provider/LLM errors.
      // It forces the UI into speaking/agent_speaking even though there is no
      // real model response, and it masks live user speech on subsequent turns.
      if (!liveMicOrSessionActive && !isProviderLikeError) {
        try {
          if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel()
            const utterance = new SpeechSynthesisUtterance(spokenMessage)
            utterance.lang = "en-US"
            utterance.rate = 1
            utterance.pitch = 1
            utterance.volume = 1
            window.speechSynthesis.speak(utterance)
          }
        } catch {
          // best-effort browser speech fallback
        }
      }
    }
  }, [
    clearGenerationWatchdog,
    clearLlmResponseUiTimer,
    clearLlmThinkingUiTimer,
    clearSpeechWatchdog,
    flushLlmResponseUiText,
    flushLlmThinkingUiText,
    hardStopPlaybackNow,
    setLlmThinkingActive,
    setSessionStatus,
    setTtsPlaybackActive,
    setTtsStreamActive,
    setTurnPhaseStable,
  ])

  const announceToolHint = useCallback((spokenHint: string, dedupeKey: string) => {
    const now = Date.now()
    if (!spokenHint.trim()) {
      return
    }
    if (ttsPlayingRef.current || ttsStreamActiveRef.current || interruptionInFlightRef.current) {
      return
    }
    if (
      dedupeKey === lastSpokenToolHintKeyRef.current
      && now - lastSpokenToolHintAtRef.current < 4000
    ) {
      return
    }
    lastSpokenToolHintKeyRef.current = dedupeKey
    lastSpokenToolHintAtRef.current = now
    try {
      const shouldResumeThinkingCue = Boolean(
        llmThinkingActiveRef.current
        && !ttsPlayingRef.current
        && !ttsStreamActiveRef.current
        && thinkingPlayerRef.current,
      )
      thinkingPlayerRef.current?.stop()
      if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel()
        window.speechSynthesis.resume()
        const utterance = new SpeechSynthesisUtterance(spokenHint)
        utterance.lang = "en-US"
        utterance.rate = 1
        utterance.pitch = 1
        utterance.volume = 1
        utterance.onend = () => {
          if (shouldResumeThinkingCue) {
            void thinkingPlayerRef.current?.start()
          }
        }
        utterance.onerror = () => {
          if (shouldResumeThinkingCue) {
            void thinkingPlayerRef.current?.start()
          }
        }
        window.speechSynthesis.speak(utterance)
      }
    } catch {
      // best-effort browser speech fallback
    }
  }, [])

  const startSpeechWatchdog = useCallback(() => {
    clearSpeechWatchdog()
    speechWatchdogTimerRef.current = window.setTimeout(() => {
      if (pendingSpeechAfterThinkingRef.current && !ttsPlayingRef.current) {
        pendingSpeechAfterThinkingRef.current = false
        setPendingSpeechAfterThinking(false)
        if (sessionRef.current && micRef.current) {
          setTurnPhaseStable("listening")
        } else {
          setTurnPhaseStable("idle")
        }
      }
    }, 1200)
  }, [clearSpeechWatchdog, setTurnPhaseStable])

  const startGenerationWatchdog = useCallback((generationId: string) => {
    clearGenerationWatchdog()
    generationWatchdogTimerRef.current = window.setTimeout(() => {
      if (activeGenerationIdRef.current !== generationId) {
        return
      }
      if (ttsPlayingRef.current || ttsStreamActiveRef.current) {
        return
      }
      rejectGeneration(generationId)
      announceLlmError("The model timed out before finishing the response.", "timeout")
    }, DEMO_GENERATION_WATCHDOG_TIMEOUT_MS)
  }, [announceLlmError, clearGenerationWatchdog, rejectGeneration])

  const handleAgentSignal = useCallback((signal: VoiceAgentSignal) => {
    if (signal.type === "session.status") {
      if (mode === "minimal") {
        devLog("[MIC DEBUG] session.status", {
          status: signal.status,
          reason: signal.reason,
          hasMic: Boolean(micRef.current),
          micStartInFlight: micStartInFlightRef.current,
          isListening,
          freeWanted: freeMicWantedRef.current,
          sessionStatus: sessionStatusRef.current,
          turnPhase: turnPhaseRef.current,
          ttsPlaying: ttsPlayingRef.current,
          ttsStreamActive: ttsStreamActiveRef.current,
        })
      }
      pushTraceLocal(
        "ui.session.status",
        {
          status: signal.status,
          reason: signal.reason,
        },
        "ui.state",
      )
      if (signal.generationId) {
        activeGenerationIdRef.current = signal.generationId
      }
      if (signal.status === "transcribing" || signal.status === "thinking") {
        localUserSpeechVisualUntilRef.current = 0
        setTurnPhaseStable("processing")
      } else if (signal.status === "speaking") {
        if (!suppressTtsUntilNextUserFinalRef.current) {
          const hasAudiblePlayback = ttsPlayingRef.current || ttsStreamActiveRef.current
          if (hasAudiblePlayback) {
            setTurnPhaseStable("agent_speaking")
            pendingSpeechAfterThinkingRef.current = false
            setPendingSpeechAfterThinking(false)
            clearSpeechWatchdog()
          } else {
            setTurnPhaseStable("processing")
            pendingSpeechAfterThinkingRef.current = true
            setPendingSpeechAfterThinking(true)
            startSpeechWatchdog()
          }
        }
      } else if (signal.status === "listening" || signal.status === "ready") {
        const nextPhase = (() => {
          if (
            pendingSpeechAfterThinkingRef.current
            || ttsPlayingRef.current
            || ttsStreamActiveRef.current
          ) {
            return "agent_speaking" as TurnPhase
          }
          if (llmThinkingActiveRef.current || Date.now() < thinkingHoldUntilRef.current) {
            return "processing" as TurnPhase
          }
          if (pendingTurnPhase !== "idle") {
            return "processing" as TurnPhase
          }
          const recentUserSpeech =
            Date.now() - lastUserSpeechAtRef.current <= 220
          if (turnPhaseRef.current === "processing" && recentUserSpeech) {
            return "processing" as TurnPhase
          }
          return (micRef.current ? "listening" : "idle") as TurnPhase
        })()
        setTurnPhaseStable(nextPhase)
        if (
          pendingTurnPhase === "idle"
          && !ttsPlayingRef.current
          && !ttsStreamActiveRef.current
          && !llmThinkingActiveRef.current
        ) {
          clearGenerationWatchdog()
          activeGenerationIdRef.current = null
        }
        // Auto-start mic in free-mic mode when returning to listening
        const micStale = isMicCaptureStale()
        if (
          freeMicWantedRef.current
          && (!micRef.current || micStale)
          && !micStartInFlightRef.current
          && sessionRef.current
        ) {
          devLog("[MIC DEBUG] auto-start from session.status", {
            status: signal.status,
            hasMic: Boolean(micRef.current),
            micStale,
            micStartInFlight: micStartInFlightRef.current,
            isListening,
            freeWanted: freeMicWantedRef.current,
          })
          void startListening()
        }
      } else if (
        signal.status === "interrupted"
        || signal.status === "closed"
        || signal.status === "failed"
      ) {
        // now derived from SDK state
        // Check if user was recently speaking before setting to idle
        const recentUserSpeech = Date.now() - lastUserSpeechAtRef.current <= 220 && micRef.current
        const nextPhase = recentUserSpeech ? "user_speaking" as TurnPhase : "idle" as TurnPhase
        setTurnPhaseStable(nextPhase)
        pendingSpeechAfterThinkingRef.current = false
        setPendingSpeechAfterThinking(false)
        clearSpeechWatchdog()
        // Auto-start mic in free-mic mode when session becomes available
        if (
          signal.status === "interrupted"
          && freeMicWantedRef.current
          && !micRef.current
          && !micStartInFlightRef.current
          && sessionRef.current
        ) {
          devLog("[MIC DEBUG] auto-start from interrupted", {
            hasMic: Boolean(micRef.current),
            micStartInFlight: micStartInFlightRef.current,
            isListening,
            freeWanted: freeMicWantedRef.current,
          })
          void startListening()
        }
      }
      return
    }

    if (signal.type === "assistant.phase") {
      if (Date.now() < ignoreAssistantUpdatesUntilRef.current) {
        return
      }
      resetAssistantPanels(signal.turnId)
      if (!thinkingPlayerRef.current) {
        thinkingPlayerRef.current = new ThinkingAudioPlayer(thinkingCueUrl)
      }
      if (signal.generationId) {
        activeGenerationIdRef.current = signal.generationId
        suppressTtsUntilNextUserFinalRef.current = false
        startGenerationWatchdog(signal.generationId)
      }

      if (signal.phase === "thinking") {
        // now derived from SDK state
        setThinkingHoldUntil(Date.now() + 1200)
        setTurnPhaseStable("processing")
        pendingSpeechAfterThinkingRef.current = false
        setPendingSpeechAfterThinking(false)
        clearSpeechWatchdog()
        void thinkingPlayerRef.current.start()
      } else if (signal.phase === "generating") {
        // now derived from SDK state
        pendingSpeechAfterThinkingRef.current = true
        setPendingSpeechAfterThinking(true)
        if (ttsPlayingRef.current || ttsStreamActiveRef.current) {
          setTurnPhaseStable("agent_speaking")
        } else {
          setTurnPhaseStable("processing")
        }
        startSpeechWatchdog()
      } else {
        clearGenerationWatchdog()
        // now derived from SDK state
        thinkingPlayerRef.current.stop()
      }
      return
    }

    if (signal.type === "stt.final") {
      suppressTtsUntilNextUserFinalRef.current = false
      resetAssistantPanels(signal.turnId)
      interruptionInFlightRef.current = false
      const now = Date.now()
      const hadRecentUserSpeechEvidence = now - lastUserSpeechAtRef.current <= 1800
      const hasSpeechLikeFinal = signal.text.trim().length > 0 && !isLikelyNoisePartial(signal.text)
      const agentCurrentlySpeaking =
        turnPhaseRef.current === "agent_speaking"
        || sessionStatusRef.current === "speaking"
        || ttsPlayingRef.current
        || ttsStreamActiveRef.current
      if (
        effectiveQueuePolicy === "send_now"
        && hasSpeechLikeFinal
        && hadRecentUserSpeechEvidence
        && agentCurrentlySpeaking
        && !interruptionInFlightRef.current
      ) {
        triggerImmediateInterrupt("stt_final")
      }
      if (signal.text.trim()) {
        lastUserSpeechAtRef.current = now
        allowAutoInterruptUntilRef.current = 0
        bargeInSpeechActiveRef.current = false
      }
      queueSttUiText(signal.text || "", true)
      setTurnPhaseStable("processing")
      const dedupeKey = `${signal.turnId ?? "-"}:${signal.text}`
      const isCommittedUserFinal =
        typeof signal.generationId === "string" && signal.generationId.length > 0
      if (isCommittedUserFinal && dedupeKey !== seenUserFinalRef.current && signal.text.trim()) {
        seenUserFinalRef.current = dedupeKey
      }
      return
    }
    if (signal.type === "vad.state") {
      const shouldAutoBargeInterrupt = effectiveQueuePolicy === "send_now"
      if (signal.kind === "start_of_speech") {
        vadSpeechStartedAtRef.current = Date.now()
      }
      const canRenderUserSpeech = micRef.current && (sessionStatusRef.current === "listening" || sessionStatusRef.current === "ready")
      const confidentInferenceSpeech = signal.kind === "inference" && signal.speaking === true && typeof signal.probability === "number" && signal.probability >= DEMO_UI_VAD_PROBABILITY_THRESHOLD
      const speechStartDetected = signal.kind === "start_of_speech" || confidentInferenceSpeech
      if (speechStartDetected) {
        allowAutoInterruptUntilRef.current = Date.now() + 700
        if (!bargeInSpeechActiveRef.current && shouldAutoBargeInterrupt && DEMO_ENABLE_VAD_AUTO_INTERRUPT && interruptionPolicyRef.current.shouldInterruptFromVad({ type: "vad.state", session_id: sessionRef.current?.sessionId ?? "", event_id: "ui-vad", timestamp: new Date().toISOString(), sequence: 0, kind: signal.kind, speaking: signal.speaking ?? false, probability: signal.probability ?? undefined })) {
          triggerImmediateInterrupt("vad")
        }
        // Allow user_speaking even during interruption or when session is thinking
        // as long as mic is active and user is actually speaking
        const canRenderUserSpeech =
          micRef.current
          && Date.now() - recentLocalUiSpeechAtRef.current <= 180
        if (canRenderUserSpeech) {
          lastUserSpeechAtRef.current = Date.now()
          localUserSpeechVisualUntilRef.current = Date.now() + 180
          setTurnPhaseStable("user_speaking")
        }
      } else if (signal.kind === "end_of_speech" && signal.speaking === false) {
        bargeInSpeechActiveRef.current = false
        localUserSpeechVisualUntilRef.current = 0
        recentLocalUiSpeechAtRef.current = 0
        const hadRecentUserSpeech = Date.now() - lastUserSpeechAtRef.current <= DEMO_MIC_STOP_COMMIT_GRACE_MS
        const speakingWindowMs = Date.now() - vadSpeechStartedAtRef.current
        const isVoiceLikeSegment = speakingWindowMs >= DEMO_MIN_SPEECH_DURATION_MS
        const autoCommitCooldownSatisfied = Date.now() - lastAutoCommitAtRef.current >= DEMO_AUTO_COMMIT_MIN_INTERVAL_MS
        const canStartPendingTurn = pendingTurnPhase === "idle"
        if (
          !DEMO_SEND_NOW_RUNTIME_OWNED_COMMIT
          && sessionRef.current
          && micRef.current
          && (sessionStatusRef.current === "listening" || sessionStatusRef.current === "ready")
          && hadRecentUserSpeech
          && isVoiceLikeSegment
          && autoCommitCooldownSatisfied
          && canStartPendingTurn
        ) {
          const clientTurnId = `ct_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
          lastAutoCommitAtRef.current = Date.now()
          agentRef.current?.commit(undefined, clientTurnId)
        }
        if (micRef.current) {
          const backendProcessingLikeNow =
            llmThinkingActiveRef.current
            || pendingSpeechAfterThinkingRef.current
            || pendingTurnPhaseRef.current !== "idle"
            || sessionStatusRef.current === "thinking"
            || sessionStatusRef.current === "transcribing"
          const nextPhase: TurnPhase = backendProcessingLikeNow ? "processing" : "listening"
          setTurnPhaseStable(nextPhase)
        }
      } else if (sessionStatusRef.current === "listening" || sessionStatusRef.current === "ready") {
        const nextPhase = (() => {
          const prev = turnPhaseRef.current
          // Keep user_speaking if user was recently detected speaking
          if (prev === "user_speaking") {
            const recentUserSpeech = Date.now() - lastUserSpeechAtRef.current <= 1500
            if (recentUserSpeech && micRef.current) {
              return "user_speaking" as TurnPhase
            }
          }
          if (prev === "agent_speaking") return prev
          if (prev === "processing" && Date.now() < thinkingHoldUntilRef.current) return prev
          return (micRef.current ? "listening" : "idle") as TurnPhase
        })()
        setTurnPhaseStable(nextPhase)
      }
      return
    }
    if (signal.type === "assistant.thinking") {
      if (Date.now() < ignoreAssistantUpdatesUntilRef.current) {
        return
      }
      // now derived from SDK state
      llmThinkingUiTextRef.current += signal.delta || ""
      if (mode !== "minimal") {
        queueLlmThinkingUiText()
      }
      if (!ttsPlayingRef.current && !ttsStreamActiveRef.current) {
        setTurnPhaseStable("processing")
      }
      return
    }
    if (signal.type === "assistant.tool") {
      if (Date.now() < ignoreAssistantUpdatesUntilRef.current) {
        return
      }
      const toolKey = `${signal.generationId ?? "-"}:${signal.toolName}:${signal.status ?? "-"}`
      const nextSummary = signal.summary?.trim() ?? ""
      if (nextSummary && toolKey !== lastToolUpdateKeyRef.current) {
        lastToolUpdateKeyRef.current = toolKey
        llmThinkingUiTextRef.current = nextSummary
        setLlmThinkingActive(true)
        queueLlmThinkingUiText(true)
      }
      if (signal.spokenHint) {
        announceToolHint(signal.spokenHint, toolKey)
      }
      if (!ttsPlayingRef.current && !ttsStreamActiveRef.current) {
        setTurnPhaseStable("processing")
      }
      return
    }
    if (signal.type === "assistant.response.delta") {
      if (Date.now() < ignoreAssistantUpdatesUntilRef.current) {
        return
      }
      // now derived from SDK state
      const cleanDelta = (signal.delta || "")
        .replace(/\*\*/g, "")
        .replace(/\*/g, "")
        .replace(/__/g, "")
        .replace(/`/g, "")
      llmResponseTextRef.current += cleanDelta
      if (mode !== "minimal") {
        queueLlmResponseUiText()
      }
      if (ttsPlayingRef.current || ttsStreamActiveRef.current) {
        setTurnPhaseStable("agent_speaking")
      } else {
        setTurnPhaseStable("processing")
      }
      return
    }
    if (signal.type === "assistant.response.final") {
      if (Date.now() < ignoreAssistantUpdatesUntilRef.current) {
        return
      }
      clearGenerationWatchdog()
      setLlmResponseCompletedAt(Date.now())
      window.setTimeout(() => {
        if (!ttsPlayingRef.current && !ttsStreamActiveRef.current) {
          clearSpeakingFlags()
        }
      }, 350)
      suppressErrorSpeechUntilRef.current = 0
      if (signal.text.trim()) {
        llmResponseTextRef.current = signal.text
        if (mode !== "minimal") {
          queueLlmResponseUiText(true)
        }
      }
      return
    }
    if (signal.type === "assistant.error") {
      if (signal.generationId && rejectedGenerationIdsRef.current.has(signal.generationId)) {
        return
      }
      if (signal.generationId) {
        activeGenerationIdRef.current = signal.generationId
        startGenerationWatchdog(signal.generationId)
      }
      announceLlmError(signal.message, signal.code)
      return
    }
    if (signal.type === "assistant.speaking.state") {
      if (signal.state === "playing") {
        thinkingPlayerRef.current?.stop()
        try {
          if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel()
          }
        } catch {
          // best-effort browser speech cancellation
        }
        clearGenerationWatchdog()
        ttsStreamActiveRef.current = true
        setTtsStreamActive(true)
        setTtsVisualCompletedUntil(0)
        // now derived from SDK state
        pendingSpeechAfterThinkingRef.current = false
        setPendingSpeechAfterThinking(false)
        clearSpeechWatchdog()
        setTurnPhaseStable("agent_speaking")
      } else if (signal.state === "complete") {
        ttsStreamActiveRef.current = false
        setTtsStreamActive(false)
        setTtsVisualCompletedUntil(Date.now() + 700)
        pendingSpeechAfterThinkingRef.current = false
        setPendingSpeechAfterThinking(false)
        clearSpeechWatchdog()
        // Auto-start mic in free-mic mode after TTS completes
        const micStale = isMicCaptureStale()
        if (
          freeMicWantedRef.current
          && (!micRef.current || micStale)
          && !micStartInFlightRef.current
          && sessionRef.current
        ) {
          devLog("[MIC DEBUG] auto-start from tts.complete", {
            hasMic: Boolean(micRef.current),
            micStale,
            micStartInFlight: micStartInFlightRef.current,
            isListening,
            freeWanted: freeMicWantedRef.current,
          })
          void startListening()
        }
      }
      return
    }
    if (signal.type === "interrupt.lifecycle" && signal.stage === "acknowledged") {
      suppressTtsUntilNextUserFinalRef.current = true
      rejectGeneration(activeGenerationIdRef.current)
      // now derived from SDK state
      ttsPlayingRef.current = false
      setTtsPlaybackActive(false)
      ttsStreamActiveRef.current = false
      setTtsStreamActive(false)
      setTtsVisualCompletedUntil(0)
      pendingSpeechAfterThinkingRef.current = false
      setPendingSpeechAfterThinking(false)
      clearSpeechWatchdog()
      try {
        if ("speechSynthesis" in window) {
          window.speechSynthesis.cancel()
        }
      } catch {
        // best-effort browser speech cancellation
      }
      setTurnPhaseStable(sessionRef.current && micRef.current ? "listening" : "idle")
      void hardStopPlayback()
      // Auto-start mic in free-mic mode after interruption acknowledged
      const micStale = isMicCaptureStale()
      if (
        freeMicWantedRef.current
        && (!micRef.current || micStale)
        && !micStartInFlightRef.current
        && sessionRef.current
      ) {
        devLog("[MIC DEBUG] auto-start from interrupt.ack", {
          hasMic: Boolean(micRef.current),
          micStale,
          micStartInFlight: micStartInFlightRef.current,
          isListening,
          freeWanted: freeMicWantedRef.current,
        })
        void startListening()
      }
      interruptionInFlightRef.current = false
      return
    }
    if (signal.type === "sdk.error") {
      const timeoutKind = typeof signal.details?.timeout_kind === "string" ? signal.details.timeout_kind : null
      if (timeoutKind === "stt_final_timeout") {
        return
      }
      const codePart = signal.code ? ` (${signal.code})` : ""
      setLastError(`${signal.message}${codePart}`)
      clearGenerationWatchdog()
      interruptionInFlightRef.current = false
      suppressTtsUntilNextUserFinalRef.current = false
      activeGenerationIdRef.current = null
      ttsPlayingRef.current = false
      setTtsPlaybackActive(false)
      ttsStreamActiveRef.current = false
      setTtsStreamActive(false)
      setTtsVisualCompletedUntil(0)
      // now derived from SDK state
      setSessionStatus(`error: ${signal.message}`)
      setTurnPhaseStable(sessionRef.current && micRef.current ? "listening" : "idle")
      const codeLower = (signal.code ?? "").toLowerCase()
      const msgLower = signal.message.toLowerCase()
      if (codeLower.includes("provider") || msgLower.includes("rate limit")) {
        announceLlmError(signal.message, signal.code)
      }
    }
  }, [
    announceLlmError,
    announceToolHint,
    clearSpeakingFlags,
    clearGenerationWatchdog,
    clearSpeechWatchdog,
    hardStopPlayback,
    mode,
    pendingTurnPhase,
    queueLlmResponseUiText,
    queueLlmThinkingUiText,
    queueSttUiText,
    resetAssistantPanels,
    hardStopPlayback,
    startGenerationWatchdog,
    startSpeechWatchdog,
    setTurnPhaseStable,
    pushTraceLocal,
    setTtsVisualCompletedUntil,
    setLlmResponseCompletedAt,
  ])

  const pendingTurnTraceElapsedRef = useRef(0)

  useEffect(() => {
    if (pendingTurnPhase === "idle") {
      pendingTurnTracePhaseRef.current = "idle"
      pendingTurnTraceElapsedRef.current = 0
      return
    }

    if (pendingTurnTracePhaseRef.current === "idle") {
      pushTraceLocal(
        "ui.pending_turn.start",
        {
          phase: pendingTurnPhase,
        },
        "ui.state",
      )
      pendingTurnTracePhaseRef.current = pendingTurnPhase
      pendingTurnTraceElapsedRef.current = pendingTurnElapsedMs
      return
    }

    if (pendingTurnPhase !== pendingTurnTracePhaseRef.current) {
      pushTraceLocal(
        "ui.pending_turn.phase",
        {
          from: pendingTurnTracePhaseRef.current,
          to: pendingTurnPhase,
          elapsed_ms: pendingTurnElapsedMs,
        },
        "ui.state",
      )
      pendingTurnTracePhaseRef.current = pendingTurnPhase
      pendingTurnTraceElapsedRef.current = pendingTurnElapsedMs
      return
    }

    if (pendingTurnElapsedMs - pendingTurnTraceElapsedRef.current >= 1000) {
      pendingTurnTraceElapsedRef.current = pendingTurnElapsedMs
      pushTraceLocal(
        "ui.pending_turn.elapsed",
        {
          phase: pendingTurnPhase,
          elapsed_ms: pendingTurnElapsedMs,
        },
        "ui.state",
      )
    }
  }, [pendingTurnElapsedMs, pendingTurnPhase, pushTraceLocal])

  const pendingTurnMessage = useMemo(() => {
    if (pendingTurnPhase === "idle") {
      return ""
    }
    const elapsedSeconds = Math.max(1, Math.round(pendingTurnElapsedMs / 1000))
    if (pendingTurnPhase === "commit_sent") {
      return "Audio sent. Waiting for server confirmation..."
    }
    if (pendingTurnPhase === "awaiting_backend") {
      return "Server confirmed. Waiting for transcript..."
    }
    if (pendingTurnPhase === "slow") {
      return `Still waiting for transcript (${elapsedSeconds}s). Network or STT may be slow.`
    }
    if (pendingTurnPhase === "degraded") {
      return `Delayed response (${elapsedSeconds}s). You can keep speaking; latest turn will be prioritized.`
    }
    return `Taking longer than usual (${elapsedSeconds}s). You can interrupt and retry.`
  }, [pendingTurnElapsedMs, pendingTurnPhase])

  const sttProgressMessage = useMemo(() => {
    const status = sttProgress.status
    if (!status) {
      return ""
    }
    const waitedMs = sttProgress.waitedMs
    const attempt = sttProgress.attempt
    const waited = typeof waitedMs === "number" ? ` (${Math.max(0, waitedMs)}ms)` : ""
    const attemptText = typeof attempt === "number" ? `, attempt ${attempt}` : ""

    if (status === "queued") {
      return `STT queued${waited}${attemptText}`
    }
    if (status === "transcribing") {
      return `STT transcribing${waited}${attemptText}`
    }
    if (status === "waiting_final") {
      return `Waiting for STT final${waited}${attemptText}`
    }
    if (status === "stabilizing") {
      return `Stabilizing transcript${waited}${attemptText}`
    }
    if (status === "retry_scheduled") {
      return `Retry scheduled${waited}${attemptText}`
    }
    return `STT status: ${status}${waited}${attemptText}`
  }, [sttProgress.attempt, sttProgress.status, sttProgress.waitedMs])

  const sttFinalMetaMessage = useMemo(() => {
    const details: string[] = []
    if (typeof sttFinalMeta.revision === "number") {
      details.push(`rev ${sttFinalMeta.revision}`)
    }
    if (sttFinalMeta.finality) {
      details.push(sttFinalMeta.finality)
    }
    if (typeof sttFinalMeta.deferred === "boolean") {
      details.push(sttFinalMeta.deferred ? "deferred" : "immediate")
    }
    if (details.length === 0) {
      return ""
    }
    return `STT final: ${details.join(", ")}`
  }, [sttFinalMeta.deferred, sttFinalMeta.finality, sttFinalMeta.revision])

  const triggerImmediateInterrupt = useCallback((source: "vad" | "stt_final" | "local_audio") => {
    const session = sessionRef.current
    if (!session) return
    const now = Date.now()

    const agentCurrentlySpeaking =
      turnPhaseRef.current === "agent_speaking"
      || turnPhaseRef.current === "processing"
      || sessionStatusRef.current === "speaking"
      || sessionStatusRef.current === "transcribing"
      || sessionStatusRef.current === "thinking"
      || ttsPlayingRef.current
      || ttsStreamActiveRef.current
      || llmThinkingActiveRef.current

    if (!agentCurrentlySpeaking || interruptionInFlightRef.current) {
      return
    }

    if (bargeInSpeechActiveRef.current) {
      return
    }

    if (!interruptionPolicyRef.current.canInterrupt(now)) {
      return
    }

    localBargeInFramesRef.current = 0
    localBargeInCooldownUntilRef.current = now + DEMO_LOCAL_BARGE_IN_COOLDOWN_MS
    allowAutoInterruptUntilRef.current = 0
    interruptionInFlightRef.current = true
    bargeInSpeechActiveRef.current = true
    suppressTtsUntilNextUserFinalRef.current = true
    interruptionPolicyRef.current.markInterrupted(now)
    rejectGeneration(activeGenerationIdRef.current)
    const interruptEventName =
      source === "vad"
        ? "ui.auto_interrupt.vad"
        : source === "stt_final"
            ? "ui.auto_interrupt.stt_final"
          : "ui.auto_interrupt.local_audio"
    pushTraceLocal(
      interruptEventName,
      {
        session_id: session.sessionId,
        source,
      },
      "ui.action",
    )
    hardStopPlaybackNow()
    void session.interrupt("barge_in")
  }, [hardStopPlaybackNow, pushTraceLocal, rejectGeneration])

  const activeGridBands = useMemo(() => {
    // Always use mic bands when user is speaking or mic is active
    if (micBands.length > 0 && !ttsStreamActive) {
      return micBands
    }
    return visualTurnPhase === "agent_speaking" ? ttsBands : micBands
  }, [micBands, ttsBands, visualTurnPhase, ttsStreamActive])

  const runtimeConfig = useMemo<RuntimeSessionConfig>(() => {
    const effectivePolicy = effectiveQueuePolicy
    return {
      turnQueue: { policy: effectivePolicy },
      retry: {
        enabled: true,
        afterMs: 250,
      },
      router: {
        timeoutMs: DEMO_ROUTER_TIMEOUT_MS,
        mode: DEMO_ROUTER_MODE,
      },
      interruption: {
        mode: "immediate",
        minDuration: DEMO_INTERRUPT_MIN_DURATION_SECONDS,
        minWords: DEMO_INTERRUPT_MIN_WORDS,
        cooldownMs: DEMO_INTERRUPT_COOLDOWN_MS,
        autoInterrupt: {
          enabled: DEMO_ENABLE_STT_PARTIAL_AUTO_INTERRUPT,
          vadThreshold: DEMO_UI_VAD_PROBABILITY_THRESHOLD,
          minDurationMs: DEMO_MIN_SPEECH_DURATION_MS,
        },
      },
      client: {
        echoFiltering: {
          enabled: true,
        },
        autoCommit: {
          enabled: DEMO_SEND_NOW_RUNTIME_OWNED_COMMIT,
          vadEndOfSpeechDelayMs: DEMO_MIC_STOP_COMMIT_GRACE_MS,
          minSpeechDurationMs: DEMO_MIN_SPEECH_DURATION_MS,
        },
      },
      llm: {
        systemPrompt: OPEN_VOICE_SYSTEM_PROMPT,
        first_delta_timeout_ms: DEMO_LLM_FIRST_DELTA_TIMEOUT_MS,
        total_timeout_ms: DEMO_LLM_TOTAL_TIMEOUT_MS,
        enable_fast_ack: false,
        opencode_mode: OPENCODE_MODE,
        tools: VOICE_LLM_TOOLS,
      },
      turnDetection: {
        mode: "hybrid",
        transcript_timeout_ms: DEMO_STT_TRANSCRIPT_TIMEOUT_MS,
        stabilization_ms: DEMO_DISABLE_STT_STABILIZATION ? 0 : DEMO_SLOW_STT_STABILIZATION_MS,
        min_silence_duration_ms: DEMO_MIN_SILENCE_DURATION_MS,
        min_speech_duration_ms: DEMO_MIN_SPEECH_DURATION_MS,
        activation_threshold: DEMO_VAD_ACTIVATION_THRESHOLD,
      },
      stt: {
        final_timeout_ms: DEMO_STT_FINAL_TIMEOUT_MS,
      },
      raw: {
        allow_rapid_short_followups: true,
      },
    }
  }, [effectiveQueuePolicy])

  const appendEvent = useCallback((event: ConversationEvent) => {
    if (mode === "minimal") {
      return
    }
    if (event.type === "vad.state" && event.kind === "inference") {
      return
    }
    const rendered = formatEventForPanel(event)
    queueEventLine(rendered)
  }, [mode, queueEventLine])

  const handleRawEvent = useCallback((event: ConversationEvent) => {
    if (event.type === "tts.chunk") {
      const chunk = event.chunk as { data_base64?: unknown; sequence?: unknown }
      devLog("[TTS] chunk received, sequence:", chunk.sequence, "data length:", typeof chunk.data_base64 === "string" ? chunk.data_base64.length : "unknown")
    }
    if (event.type === "tts.completed") {
      devLog("[TTS] completed, duration_ms:", (event as { duration_ms?: number }).duration_ms)
    }
    if (event.type === "llm.error") {
      devLog("[LLM] error event:", event)
      const code = event.error?.code ?? null
      const message = event.error?.message ?? "unknown error"
      announceLlmError(message, code)
    }
    if (event.type === "llm.completed") {
      devLog("[LLM] completed")
    }
    if (event.type === "llm.tool.update") {
      const presentation = describeToolActivity(event.tool_name, event.status)
      const toolKey = `${event.generation_id ?? "-"}:${event.tool_name}:${event.status ?? "-"}`
      if (presentation.summary && toolKey !== lastToolUpdateKeyRef.current) {
        lastToolUpdateKeyRef.current = toolKey
        llmThinkingUiTextRef.current = presentation.summary
        setLlmThinkingActive(true)
        setToolActivity(presentation.summary, event.status ?? "")
        pushTraceLocal(
          "ui.tool.activity",
          {
            tool_name: event.tool_name,
            status: event.status ?? null,
            summary: presentation.summary,
          },
          "ui.state",
        )
        queueEventLine(
          JSON.stringify(
            {
              type: "ui.tool.activity",
              tool_name: event.tool_name,
              status: event.status ?? null,
              summary: presentation.summary,
            },
            null,
            2,
          ),
        )
        queueLlmThinkingUiText(true)
      }
      if (presentation.spokenHint) {
        announceToolHint(presentation.spokenHint, toolKey)
        pushTraceLocal(
          "ui.tool.hint.spoken",
          {
            tool_name: event.tool_name,
            status: event.status ?? null,
            spoken_hint: presentation.spokenHint,
          },
          "ui.action",
        )
        queueEventLine(
          JSON.stringify(
            {
              type: "ui.tool.hint.spoken",
              tool_name: event.tool_name,
              status: event.status ?? null,
              spoken_hint: presentation.spokenHint,
            },
            null,
            2,
          ),
        )
      }
    }

    appendEvent(event)

    if (event.type === "session.ready") {
      setSessionStatus("ready")
      // now derived from SDK state
      setTurnPhaseStable(micRef.current ? "listening" : "idle")
      pendingSpeechAfterThinkingRef.current = false
      setPendingSpeechAfterThinking(false)
      clearSpeechWatchdog()
      return
    }

    if (event.type === "turn.accepted") {
      return
    }
  }, [announceLlmError, announceToolHint, appendEvent, clearSpeechWatchdog, pushTraceLocal, queueEventLine, queueLlmThinkingUiText, setLlmThinkingActive, setToolActivity, setTurnPhaseStable])

  const startListening = useCallback(async () => {
    if (micStartInFlightRef.current) {
      devLog("[MIC DEBUG] startListening skipped (already in flight)", {
        hasSession: Boolean(sessionRef.current),
        hasMic: Boolean(micRef.current),
        isListening,
        sessionStatus: sessionStatusRef.current,
        turnPhase: turnPhaseRef.current,
        freeWanted: freeMicWantedRef.current,
      })
      return
    }
    devLog("[MIC DEBUG] startListening called", {
      hasSession: Boolean(sessionRef.current),
      hasMic: Boolean(micRef.current),
      micStartInFlight: micStartInFlightRef.current,
      isListening,
      sessionStatus: sessionStatusRef.current,
      turnPhase: turnPhaseRef.current,
      freeWanted: freeMicWantedRef.current,
    })
    if (!sessionRef.current || isDisconnectingRef.current) return
    if (micRef.current) {
      const lastChunkAt = lastMicChunkAtRef.current
      const staleMs = lastChunkAt > 0 ? Date.now() - lastChunkAt : 0
      const micStale = lastChunkAt > 0 && staleMs > DEMO_MIC_CAPTURE_STALE_MS
      if (!micStale) {
        dispatchSession({ type: "setListeningOnly", isListening: true })
        return
      }
      devWarn("[MIC DEBUG] startListening recreating stale mic", {
        staleMs,
        lastChunkAt,
        hasMic: Boolean(micRef.current),
        sessionStatus: sessionStatusRef.current,
        turnPhase: turnPhaseRef.current,
      })
      const staleMic = micRef.current
      micRef.current = null
      lastMicChunkAtRef.current = 0
      dispatchSession({ type: "setListeningOnly", isListening: false })
      queueMicLevelUi(0, true)
      setMicBands(zeroBands())
      await staleMic.stop().catch(() => undefined)
    }
    micStartInFlightRef.current = true
    const lifecycleToken = ++micLifecycleTokenRef.current
    try {
      if (!window.isSecureContext) {
        throw new Error("Microphone requires HTTPS (or localhost).")
      }
      pushTraceLocal("ui.start_listening", {
        session_id: sessionRef.current.sessionId,
      })
      const mic = new DemoMicInput(
        (chunk) => agentRef.current?.sendAudio(chunk),
        (level) => {
          const normalizedLevel = Math.max(0, level)
          queueMicLevelUi(Math.min(100, Math.round(level * 140)))
          if (normalizedLevel >= 0.18 && micRef.current) {
            const now = Date.now()
            localUserSpeechVisualUntilRef.current = now + 80
          }

          if (effectiveQueuePolicy !== "send_now") {
            localBargeInFramesRef.current = 0
            localBargeInNoiseFloorRef.current = 0
            return
          }

          const agentAudioPlaying =
            ttsPlayingRef.current
            || ttsStreamActiveRef.current
            || turnPhaseRef.current === "agent_speaking"
            || sessionStatusRef.current === "speaking"
          const agentThinking =
            sessionStatusRef.current === "thinking"
            || llmThinkingActiveRef.current
            || pendingSpeechAfterThinkingRef.current
            || turnPhaseRef.current === "processing"
          const agentInterruptible = agentAudioPlaying || agentThinking

          if (!agentInterruptible) {
            localBargeInFramesRef.current = 0
            localBargeInNoiseFloorRef.current = normalizedLevel
            return
          }

          const currentFloor = localBargeInNoiseFloorRef.current || normalizedLevel
          const nextFloor =
            currentFloor * (1 - DEMO_LOCAL_BARGE_IN_FLOOR_ALPHA)
            + normalizedLevel * DEMO_LOCAL_BARGE_IN_FLOOR_ALPHA
          localBargeInNoiseFloorRef.current = nextFloor
          const dynamicThreshold = Math.min(
            0.65,
            Math.max(
              DEMO_LOCAL_BARGE_IN_PEAK_THRESHOLD,
              nextFloor * DEMO_LOCAL_BARGE_IN_FLOOR_MULTIPLIER + DEMO_LOCAL_BARGE_IN_FLOOR_BIAS,
            ),
          )

          const now = Date.now()
          const uiSpeechThreshold = Math.max(0.16, Math.min(0.34, dynamicThreshold * 0.85))
          const localUiSpeechDetected = normalizedLevel >= uiSpeechThreshold
          if (localUiSpeechDetected) {
            recentLocalUiSpeechAtRef.current = now
            if (micRef.current) {
              // Short-lived live speaking hint only. Do not extend the long speech
              // grace windows here, or fan/cooler noise will stick as user_speaking.
              localUserSpeechVisualUntilRef.current = Math.max(localUserSpeechVisualUntilRef.current, now + 90)
              if (agentAudioPlaying || sessionStatusRef.current === "listening" || sessionStatusRef.current === "ready") {
                setTurnPhaseStable("user_speaking")
              }
            }
          }

          if (interruptionInFlightRef.current) {
            localBargeInFramesRef.current = 0
            return
          }

          if (now < localBargeInCooldownUntilRef.current) {
            return
          }

          // Local audio barge-in is only used to stop active assistant speech quickly.
          // Thinking interruptions should continue to rely on VAD/STT-confirmed paths so
          // we do not prematurely interrupt while the user is still forming a turn.
          if (!DEMO_ENABLE_LOCAL_AUDIO_AUTO_INTERRUPT || !agentAudioPlaying) {
            localBargeInFramesRef.current = 0
            return
          }

          if (normalizedLevel >= dynamicThreshold) {
            localBargeInFramesRef.current += 1
            if (localBargeInFramesRef.current >= DEMO_LOCAL_BARGE_IN_CONSECUTIVE_FRAMES) {
              pushTraceLocal(
                "ui.auto_interrupt.local_audio",
                {
                  session_id: sessionRef.current?.sessionId ?? null,
                  phase: agentAudioPlaying ? "speaking" : "thinking",
                  peak: Number(normalizedLevel.toFixed(3)),
                  floor: Number(nextFloor.toFixed(3)),
                  threshold: Number(dynamicThreshold.toFixed(3)),
                },
                "ui.action",
              )
              triggerImmediateInterrupt("local_audio")
            }
          } else {
            localBargeInFramesRef.current = 0
          }
        },
        (bands) => {
          setMicBands(bands)
        },
        (meta) => {
          lastMicChunkAtRef.current = Date.now()
          pushTraceLocal("audio.input.chunk", meta, "audio.chunk")
        },
      )
      await mic.start()
      if (lifecycleToken !== micLifecycleTokenRef.current || !sessionRef.current || micRef.current) {
        await mic.stop().catch(() => undefined)
        return
      }
      micRef.current = mic
      lastMicChunkAtRef.current = Date.now()
      dispatchSession({ type: "setListeningOnly", isListening: true })
      devLog("[MIC DEBUG] startListening success", {
        hasMic: Boolean(micRef.current),
        isListening: true,
        sessionStatus: sessionStatusRef.current,
        freeWanted: freeMicWantedRef.current,
      })
      // Keep user_speaking if user was recently speaking
      const recentUserSpeech = Date.now() - lastUserSpeechAtRef.current <= 220
      if (sessionStatusRef.current === "listening" || sessionStatusRef.current === "ready") {
        setTurnPhaseStable(recentUserSpeech ? "user_speaking" : "listening")
      }
    } finally {
      micStartInFlightRef.current = false
    }
  }, [
    effectiveQueuePolicy,
    isListening,
    pushTraceLocal,
    queueMicLevelUi,
    setTurnPhaseStable,
    triggerImmediateInterrupt,
  ])

  const stopListening = useCallback(async (reason = "unknown") => {
    if (reason === "unknown") {
      devWarn("[MIC DEBUG] stopListening unknown caller", new Error().stack)
    }
    devLog("[MIC DEBUG] stopListening called", {
      reason,
      hasMicBeforeStop: Boolean(micRef.current),
      isListening,
      sessionStatus: sessionStatusRef.current,
      turnPhase: turnPhaseRef.current,
      freeWanted: freeMicWantedRef.current,
    })
    const lifecycleToken = ++micLifecycleTokenRef.current
    pushTraceLocal("ui.stop_listening", {
      session_id: sessionRef.current?.sessionId ?? null,
    })
    const mic = micRef.current
    micRef.current = null
    lastMicChunkAtRef.current = 0

    dispatchSession({ type: "setListeningOnly", isListening: false })
    setIsMicHoldActive(false)
    queueMicLevelUi(0, true)
    setMicBands(zeroBands())
    localBargeInFramesRef.current = 0
    localBargeInCooldownUntilRef.current = 0
    localBargeInNoiseFloorRef.current = 0
    allowAutoInterruptUntilRef.current = 0

    if (!mic) {
      return
    }
    if (mic) {
      await mic.stop().catch(() => undefined)
    }
    if (lifecycleToken !== micLifecycleTokenRef.current) {
      return
    }

    const shouldCommitOnStop =
      !DEMO_SEND_NOW_RUNTIME_OWNED_COMMIT
      && Boolean(sessionRef.current)
      && !isDisconnectingRef.current
      && Date.now() - lastUserSpeechAtRef.current <= DEMO_MIC_STOP_COMMIT_GRACE_MS
    if (shouldCommitOnStop) {
      const clientTurnId = `ct_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
      pushTraceLocal(
        "ui.pending_turn.start",
        {
          trigger: "mic_stop_commit",
          client_turn_id: clientTurnId,
          stabilization_ms: DEMO_SLOW_STT_STABILIZATION_MS,
          transcript_timeout_ms: DEMO_STT_TRANSCRIPT_TIMEOUT_MS,
        },
        "ui.state",
      )
      agentRef.current?.commit(undefined, clientTurnId)
    }

    if (sessionStatusRef.current === "speaking") {
      setTurnPhaseStable("agent_speaking")
      return
    }
    if (
      sessionStatusRef.current === "thinking"
      || sessionStatusRef.current === "transcribing"
      || turnPhaseRef.current === "processing"
      || turnPhaseRef.current === "agent_speaking"
      || pendingSpeechAfterThinkingRef.current
      || ttsPlayingRef.current
      || ttsStreamActiveRef.current
      || llmThinkingActiveRef.current
    ) {
      setTurnPhaseStable("processing")
      return
    }
    const sawRecentUserSpeech =
      Date.now() - lastUserSpeechAtRef.current <= DEMO_POST_RELEASE_PROCESSING_GRACE_MS
    if (sawRecentUserSpeech) {
      setTurnPhaseStable("processing")
      return
    }
    setTurnPhaseStable("idle")
  }, [pushTraceLocal, queueMicLevelUi, setTurnPhaseStable])

  useEffect(() => {
    const timer = window.setInterval(() => {
      if (!sessionRef.current || !freeMicWantedRef.current || !micRef.current) {
        return
      }
      if (micStartInFlightRef.current || isDisconnectingRef.current) {
        return
      }
      if (ttsPlayingRef.current || ttsStreamActiveRef.current) {
        return
      }
      const lastChunkAt = lastMicChunkAtRef.current
      if (lastChunkAt <= 0) {
        return
      }
      const staleMs = Date.now() - lastChunkAt
      if (staleMs <= DEMO_MIC_CAPTURE_STALE_MS) {
        return
      }
      devWarn("[MIC DEBUG] watchdog restarting stale mic", {
        staleMs,
        sessionStatus: sessionStatusRef.current,
        turnPhase: turnPhaseRef.current,
        hasMic: Boolean(micRef.current),
      })
      void startListening()
    }, 900)

    return () => {
      window.clearInterval(timer)
    }
  }, [startListening])

  const handleMinimalMicToggle = useCallback(async () => {
    if (!sessionRef.current) {
      return
    }
    if (suppressMicTapRef.current) {
      suppressMicTapRef.current = false
      return
    }
    minimalMicUserControlledRef.current = true
    setIsMicHoldActive(false)
    const shouldTurnMicOff = isListening || isMicHoldActive || micHoldActiveRef.current
    if (shouldTurnMicOff) {
      freeMicWantedRef.current = false
      await stopListening("minimal.toggle_off")
      return
    }
    try {
      freeMicWantedRef.current = true
      await startListening()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setLastError(`mic start failed: ${message}`)
    }
  }, [isListening, isMicHoldActive, startListening, stopListening])

  const handleMinimalMicPointerDown = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0 || !sessionRef.current) {
      return
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    minimalMicUserControlledRef.current = true
    freeMicWantedRef.current = false
    clearMicHoldTimer()
    micHoldActiveRef.current = false
    micHoldTimerRef.current = window.setTimeout(() => {
      if (!sessionRef.current || micRef.current) {
        return
      }
      micHoldActiveRef.current = true
      suppressMicTapRef.current = true
      setIsMicHoldActive(true)
      void startListening().catch((error) => {
        const message = error instanceof Error ? error.message : String(error)
        setLastError(`mic start failed: ${message}`)
        micHoldActiveRef.current = false
        setIsMicHoldActive(false)
      })
    }, 240)
  }, [clearMicHoldTimer, startListening])

  const handleMinimalMicPointerRelease = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    clearMicHoldTimer()
    if (!micHoldActiveRef.current) {
      return
    }
    micHoldActiveRef.current = false
    setIsMicHoldActive(false)
    freeMicWantedRef.current = false
    if (micStartInFlightRef.current) {
      void stopListening("minimal.hold_release_inflight")
      return
    }
    if (micRef.current || isListening) {
      void stopListening("minimal.hold_release")
    }
  }, [clearMicHoldTimer, isListening, stopListening])

  const handleToolbarStopListening = useCallback(async () => {
    freeMicWantedRef.current = false
    await stopListening("toolbar.stop_button")
  }, [stopListening])

  const disconnect = useCallback(async () => {
    isDisconnectingRef.current = true
    isConnectingRef.current = false
    pushTraceLocal("ui.disconnect", {
      session_id: sessionRef.current?.sessionId ?? null,
    })
    freeMicWantedRef.current = false
    await stopListening("disconnect")
    agentSignalUnsubscribeRef.current?.()
    agentSignalUnsubscribeRef.current = null
    agentRealtimeSignalUnsubscribeRef.current?.()
    agentRealtimeSignalUnsubscribeRef.current = null
    sessionStoreUnsubscribeRef.current?.()
    sessionStoreUnsubscribeRef.current = null
    transcriptUnsubscribeRef.current?.()
    transcriptUnsubscribeRef.current = null
    pendingTurnUnsubscribeRef.current?.()
    pendingTurnUnsubscribeRef.current = null
    sttProgressUnsubscribeRef.current?.()
    sttProgressUnsubscribeRef.current = null
    sttFinalMetaUnsubscribeRef.current?.()
    sttFinalMetaUnsubscribeRef.current = null
    routeStateUnsubscribeRef.current?.()
    routeStateUnsubscribeRef.current = null
    sessionStateUnsubscribeRef.current?.()
    sessionStateUnsubscribeRef.current = null
    turnPhaseUnsubscribeRef.current?.()
    turnPhaseUnsubscribeRef.current = null
    await agentRef.current?.disconnect().catch(() => undefined)
    agentRef.current = null
    sessionRef.current = null
    await sdkPlayerRef.current?.flush().catch(() => undefined)
    sdkPlayerRef.current = null
    setTtsBands(zeroBands())
    setMicBands(zeroBands())
    thinkingPlayerRef.current?.stop()
    ttsPlayingRef.current = false
    setTtsPlaybackActive(false)
    ttsStreamActiveRef.current = false
    setTtsStreamActive(false)
    setTtsVisualCompletedUntil(0)
    pendingSpeechAfterThinkingRef.current = false
    setPendingSpeechAfterThinking(false)
    clearSpeechWatchdog()
    clearMicHoldTimer()
    clearIdleTransitionTimer()
    clearErrorSpeechFallbackTimer()
    micHoldActiveRef.current = false
    suppressMicTapRef.current = false
    minimalMicUserControlledRef.current = false
    setIsMicHoldActive(false)
    persistSessionIdToUrl(null)
    setRequestedSessionId(null)
    requestedSessionLoadAttemptRef.current = null
    setSessionId("-")
    setSessionStatus("disconnected")
    historyTranscriptRequestedRef.current.clear()
    setSelectedHistorySessionId(null)
    setTurnPhaseStable("idle")
    clearGenerationWatchdog()
    sttUiTextRef.current = ""
    llmThinkingUiTextRef.current = ""
    llmResponseTextRef.current = ""
    setMinimalCaptionStickyText("")
    clearSttUiTimer()
    clearLlmThinkingUiTimer()
    clearLlmResponseUiTimer()
    flushSttUiText()
    flushLlmThinkingUiText()
    flushLlmResponseUiText()
    clearMicLevelUiTimer()
    queueMicLevelUi(0, true)
    clearEventFlushTimer()
    eventBufferRef.current = []
    setEvents([])
    setRouteName("-")
    setRouteProvider(null)
    setRouteModel(null)
    setCurrentSpokenSegment("")
    setPendingTurnPhase("idle")
    setPendingTurnElapsedMs(0)
    pendingTurnTracePhaseRef.current = "idle"
    pendingTurnTraceElapsedRef.current = 0
    setSttProgressState({ status: null, waitedMs: null, attempt: null })
    setSttFinalMetaState({ revision: null, finality: null, deferred: null, previousText: null })
    activeAssistantTurnIdRef.current = null
    interruptionInFlightRef.current = false
    bargeInSpeechActiveRef.current = false
    suppressTtsUntilNextUserFinalRef.current = false
    activeGenerationIdRef.current = null
    rejectedGenerationIdsRef.current.clear()
    try {
      if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel()
      }
    } catch {
      // ignore speech-synthesis cleanup errors
    }
    localBargeInFramesRef.current = 0
    localBargeInCooldownUntilRef.current = 0
    localBargeInNoiseFloorRef.current = 0
    allowAutoInterruptUntilRef.current = 0
    await traceReporterRef.current?.flush(false)
    traceReporterRef.current?.stop()
    traceReporterRef.current = null
    isDisconnectingRef.current = false
    void refreshSessionHistory({ preserveError: true })
  }, [
    clearEventFlushTimer,
    clearIdleTransitionTimer,
    clearLlmResponseUiTimer,
    clearLlmThinkingUiTimer,
    clearMicLevelUiTimer,
    clearMicHoldTimer,
    clearSttUiTimer,
    clearGenerationWatchdog,
    clearErrorSpeechFallbackTimer,
    clearSpeechWatchdog,
    flushLlmResponseUiText,
    flushLlmThinkingUiText,
    flushSttUiText,
    queueMicLevelUi,
    refreshSessionHistory,
    setTurnPhaseStable,
    stopListening,
  ])

  const connect = useCallback(async (forcedSessionId?: string) => {
    if (sessionRef.current || isConnectingRef.current || isDisconnectingRef.current) return
    isConnectingRef.current = true
    setLastError("")
    let resumeSessionId = forcedSessionId?.trim() || requestedSessionId?.trim() || undefined
    let resumeFallbackReason: string | null = null
    let traceReporter: FrontendTraceReporter | null = null
    try {
      await checkEngineReadiness(baseUrl)
      if (!engineReadiness.ok && engineReadiness.checked) {
        throw new Error(engineReadiness.message)
      }

      const client = new OpenVoiceWebClient({ baseUrl })
      const agent = client.createVoiceAgent()
      if (resumeSessionId) {
        try {
          const existing = await client.http.getSession(resumeSessionId)
          if (isSessionClosedOrFailed(existing.status)) {
            resumeFallbackReason = `requested session is ${existing.status}`
            resumeSessionId = undefined
            persistSessionIdToUrl(null)
            setRequestedSessionId(null)
            requestedSessionLoadAttemptRef.current = null
          }
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          if (!isRecoverableResumeErrorMessage(message)) {
            throw error
          }
          resumeFallbackReason = message
          resumeSessionId = undefined
          persistSessionIdToUrl(null)
          setRequestedSessionId(null)
          requestedSessionLoadAttemptRef.current = null
        }
      }
      if (!sdkPlayerRef.current) {
        sdkPlayerRef.current = new VisualizedPcmPlayer(
          (bands) => {
            setTtsBands(bands)
          },
          (active) => {
            ttsPlayingRef.current = active
            setTtsPlaybackActive(active)
            if (active) {
              thinkingPlayerRef.current?.stop()
              clearErrorSpeechFallbackTimer()
              setTtsVisualCompletedUntil(0)
              pendingSpeechAfterThinkingRef.current = false
              setPendingSpeechAfterThinking(false)
              clearSpeechWatchdog()
              setTurnPhaseStable("agent_speaking")
            } else if (ttsStreamActiveRef.current) {
              setTurnPhaseStable("agent_speaking")
            } else if (pendingSpeechAfterThinkingRef.current || llmThinkingActiveRef.current || Date.now() < thinkingHoldUntilRef.current) {
              setTurnPhaseStable("processing")
            } else if (sessionRef.current && micRef.current) {
              setTurnPhaseStable("listening")
            } else {
              setTurnPhaseStable("idle")
            }
          },
        )
      }

      traceReporter =
        traceReporterRef.current ??
        new FrontendTraceReporter({
          runtimeBaseUrl: baseUrl,
          enabled: FRONTEND_DIAGNOSTICS_ENABLED,
        })
      traceReporter.start()
      traceReporter.trackLocal("ui.connect_start", {
        runtime_url: baseUrl,
        mode,
        queue_policy: effectiveQueuePolicy,
        resume_session_id: resumeSessionId ?? null,
      })
      traceReporterRef.current = traceReporter

      const baseConnectOptions = {
        engineSelection: { router: "arch-router" },
        metadata: { source: "react-demo", voice_id: voiceId, language: "en-US" },
        runtimeConfig,
        audioOutput: sdkPlayerRef.current,
        autoStart: false,
        verifyEngines: false,
        traceReporter,
        onEvent: handleRawEvent,
        debug: true,
      }

      let session: WebVoiceSession
      let resumedExistingSession = false
      if (resumeSessionId) {
        session = await agent.connect({
          ...baseConnectOptions,
          sessionId: resumeSessionId,
        })
        resumedExistingSession = true
      } else {
        session = await agent.connect(baseConnectOptions)
      }

      traceReporter.setSessionId(session.sessionId)
      if (resumeFallbackReason) {
        traceReporter.trackLocal("ui.resume_session_fallback", {
          requested_session_id: forcedSessionId ?? requestedSessionId ?? null,
          reason: resumeFallbackReason,
          connected_session_id: session.sessionId,
        })
      }
      traceReporter.trackLocal("ui.connected", {
        runtime_url: baseUrl,
        mode,
        queue_policy: effectiveQueuePolicy,
        resumed_existing_session: resumedExistingSession,
      })

      sessionRef.current = session
      agentRef.current = agent
      agentSignalUnsubscribeRef.current?.()
      sessionStoreUnsubscribeRef.current?.()
      transcriptUnsubscribeRef.current?.()
      pendingTurnUnsubscribeRef.current?.()
      sttProgressUnsubscribeRef.current?.()
      sttFinalMetaUnsubscribeRef.current?.()
      routeStateUnsubscribeRef.current?.()
      agentRealtimeSignalUnsubscribeRef.current?.()
      agentSignalUnsubscribeRef.current = agent.onSignal(handleAgentSignal)
      sessionStoreUnsubscribeRef.current = session.store.subscribeSelector(
        selectCurrentSpokenSegment,
        (nextSegment) => {
          dispatchSession({ type: "setCurrentSpokenSegmentOnly", currentSpokenSegment: nextSegment.text ?? "" })
        },
        { emitCurrent: true },
      )
      transcriptUnsubscribeRef.current = session.store.subscribeSelector(
        selectTranscript,
        (nextTranscript) => {
          dispatchSession({ type: "setTranscript", transcript: nextTranscript })
        },
        { emitCurrent: true },
      )
      sttProgressUnsubscribeRef.current = session.store.subscribeSelector(
        selectSttProgress,
        (nextProgress) => {
          dispatchSession({ type: "setSttProgress", progress: nextProgress })
        },
        { emitCurrent: true },
      )
      sttFinalMetaUnsubscribeRef.current = session.store.subscribeSelector(
        selectSttFinalMeta,
        (nextMeta) => {
          dispatchSession({ type: "setSttFinalMeta", meta: nextMeta })
        },
        { emitCurrent: true },
      )
      pendingTurnUnsubscribeRef.current = session.store.subscribeSelector(
        selectPendingTurnState,
        (nextPendingTurn) => {
          dispatchSession({ type: "setPendingTurn", phase: nextPendingTurn.phase, elapsedMs: nextPendingTurn.elapsedMs })
        },
        { emitCurrent: true },
      )
      routeStateUnsubscribeRef.current = session.store.subscribeSelector(
        selectRouteState,
        (nextRoute) => {
          dispatchSession({ type: "setRoute", routeName: nextRoute.routeName ?? "-", provider: nextRoute.provider, model: nextRoute.model })
        },
        { emitCurrent: true },
      )
      sessionStateUnsubscribeRef.current = session.onStateChange((state) => {
        dispatchSession({ type: "setSessionIdOnly", sessionId: state.sessionId })
        dispatchSession({ type: "setSessionStatusOnly", sessionStatus: state.sessionStatus === "disconnected" ? "disconnected" : state.sessionStatus })
        dispatchSession({ type: "setListeningOnly", isListening: Boolean(micRef.current) })
        dispatchSession({ type: "setSttLiveText", text: state.stt.finalText ?? "" })
        dispatchSession({ type: "setLlmThinking", text: state.llm.thinkingText, active: state.llm.phase === "thinking" || state.llm.phase === "generating" })
        dispatchSession({ type: "setLlmResponse", text: state.llm.responseText })
        dispatchSession({ type: "setCurrentSpokenSegmentOnly", currentSpokenSegment: state.tts.currentSpokenSegment ?? "" })
      })
      turnPhaseUnsubscribeRef.current = session.store.subscribeSelector(
        selectTurnPhase,
        (nextTurnPhase) => {
          dispatchSession({ type: "setTurnPhaseOnly", turnPhase: nextTurnPhase })
        },
        { emitCurrent: true },
      )
      historyTranscriptRequestedRef.current.add(session.sessionId)
      persistSessionIdToUrl(session.sessionId)
      setRequestedSessionId(session.sessionId)
      setSessionId(session.sessionId)
      if (resumeFallbackReason) {
        setLastError("Requested session was unavailable, so a new session was started.")
      }
      clearEventFlushTimer()
      eventBufferRef.current = []
      setEvents([])
      sttUiTextRef.current = ""
      clearSttUiTimer()
      flushSttUiText()
      setMinimalCaptionStickyText("")
      llmThinkingUiTextRef.current = ""
      llmResponseTextRef.current = ""
      clearLlmThinkingUiTimer()
      clearLlmResponseUiTimer()
      flushLlmThinkingUiText()
      flushLlmResponseUiText()
      clearMicLevelUiTimer()
      queueMicLevelUi(0, true)
      setRouteName("-")
      setRouteProvider(null)
      setRouteModel(null)
      setTtsBands(zeroBands())
      setMicBands(zeroBands())
      ttsPlayingRef.current = false
      setTtsPlaybackActive(false)
      ttsStreamActiveRef.current = false
      setTtsStreamActive(false)
      setTtsVisualCompletedUntil(0)
      pendingSpeechAfterThinkingRef.current = false
      setPendingSpeechAfterThinking(false)
      clearSpeechWatchdog()
      activeAssistantTurnIdRef.current = null
      interruptionInFlightRef.current = false
      bargeInSpeechActiveRef.current = false
      setPendingTurnPhase("idle")
      setPendingTurnElapsedMs(0)
      pendingTurnTracePhaseRef.current = "idle"
      pendingTurnTraceElapsedRef.current = 0
      setSttProgressState({ status: null, waitedMs: null, attempt: null })
      setSttFinalMetaState({ revision: null, finality: null, deferred: null, previousText: null })
      suppressTtsUntilNextUserFinalRef.current = false
      activeGenerationIdRef.current = null
      rejectedGenerationIdsRef.current.clear()
      seenUserFinalRef.current = ""
      lastMicChunkAtRef.current = 0
      localStorage.setItem("openvoice.runtimeBaseUrl", baseUrl)
      void refreshSessionHistory({ preserveError: true })
      if (resumedExistingSession) {
        void loadSessionTranscript(session.sessionId, { setActiveTranscript: true })
      }
      const sessionStartConfig = toRuntimeConfigPayload(runtimeConfig) ?? {}
      session.send({
        type: "session.start",
        session_id: session.sessionId,
        metadata: { source: "react-demo", voice_id: voiceId, language: "en-US" },
        config: sessionStartConfig,
      })

      if (!micRef.current) {
        try {
          await startListening()
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error)
          setLastError(`connected, but mic start failed: ${message}`)
        }
      }
      requestedSessionLoadAttemptRef.current = null
    } catch (error) {
      // now derived from SDK state
      if (!sessionRef.current && traceReporter) {
        traceReporter.trackLocal("ui.connect_failed", {
          runtime_url: baseUrl,
        })
        traceReporter.stop()
        traceReporterRef.current = null
      }
      const message = error instanceof Error ? error.message : String(error)
      setLastError(message)
      setSessionStatus(`connect failed: ${message}`)
    } finally {
      isConnectingRef.current = false
    }
  }, [
    baseUrl,
    checkEngineReadiness,
    clearErrorSpeechFallbackTimer,
    clearEventFlushTimer,
    clearLlmResponseUiTimer,
    clearLlmThinkingUiTimer,
    clearMicLevelUiTimer,
    clearSttUiTimer,
    clearSpeechWatchdog,
    engineReadiness.checked,
    engineReadiness.message,
    engineReadiness.ok,
    flushLlmResponseUiText,
    flushLlmThinkingUiText,
    flushSttUiText,
    handleRawEvent,
    mode,
    effectiveQueuePolicy,
    loadSessionTranscript,
    queueMicLevelUi,
    requestedSessionId,
    runtimeConfig,
    refreshSessionHistory,
    setTurnPhaseStable,
    startListening,
    voiceId,
  ])

  useEffect(() => {
    if (isSwitchingSession) {
      return
    }
    const fromUrl = requestedSessionId?.trim() || ""
    if (!fromUrl) {
      requestedSessionLoadAttemptRef.current = null
      return
    }
    if (sessionRef.current?.sessionId === fromUrl) {
      requestedSessionLoadAttemptRef.current = null
      return
    }
    if (requestedSessionLoadAttemptRef.current === fromUrl) {
      return
    }
    requestedSessionLoadAttemptRef.current = fromUrl
    void connect(fromUrl)
  }, [connect, isSwitchingSession, requestedSessionId])

  const resumeHistorySession = useCallback(async (targetSessionId: string) => {
    const normalized = targetSessionId.trim()
    if (!normalized) {
      return
    }
    if (normalized === sessionRef.current?.sessionId) {
      setHistorySidebarOpen(false)
      return
    }

    setLastError("")
    setIsSwitchingSession(true)
    requestedSessionLoadAttemptRef.current = normalized
    setSelectedHistorySessionId(normalized)
    const cachedTranscript = sessionHistory.find((item) => item.sessionId === normalized)?.transcript ?? []
    try {
      await disconnect()
      // Set URL and state AFTER disconnect clears them
      persistSessionIdToUrl(normalized)
      setRequestedSessionId(normalized)
      // Ensure sessionRef is cleared before connecting
      sessionRef.current = null
      await connect(normalized)
      // Load transcript for the resumed session
      const activeSession = sessionRef.current as WebVoiceSessionClass | null
      if (activeSession) {
        if (cachedTranscript.length > 0) {
          activeSession.store.dispatch(toSetTranscriptAction(cachedTranscript))
        }
        await loadSessionTranscript(activeSession.sessionId, { setActiveTranscript: true })
      }
      setHistorySidebarOpen(false)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setLastError(message)
    } finally {
      setIsSwitchingSession(false)
    }
  }, [connect, disconnect, loadSessionTranscript, sessionHistory])

  const interrupt = useCallback(async () => {
    if (!sessionRef.current) return
    pushTraceLocal("ui.interrupt", { reason: "demo" })
    interruptionInFlightRef.current = true
    suppressTtsUntilNextUserFinalRef.current = true
    rejectGeneration(activeGenerationIdRef.current)
    activeGenerationIdRef.current = null
    setTurnPhaseStable("idle")
    await hardStopPlayback()
    await agentRef.current?.interrupt("demo")
  }, [hardStopPlayback, pushTraceLocal, rejectGeneration, setTurnPhaseStable])

  useEffect(() => {
    disconnectForCleanupRef.current = disconnect
  }, [disconnect])

  useEffect(() => {
    if (mode !== "minimal") return
    if (startupCommittedRef.current) return
    startupCommittedRef.current = true
    if (historySidebarOpen) return
    if (requestedSessionId?.trim()) return
    if (isSwitchingSession) return
    const run = async () => {
      try {
        if (!sessionRef.current) {
          await connect()
        } else {
          agentRef.current?.updateConfig({ turnQueue: { policy: "send_now" } })
        }
        if (freeMicWantedRef.current && !micRef.current && sessionRef.current) {
          await startListening()
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        setLastError(message)
        setSessionStatus(`minimal mode failed: ${message}`)
      }
    }
    void run()
  }, [mode, historySidebarOpen, requestedSessionId, isSwitchingSession, connect, startListening])

  useEffect(() => {
    if (!baseUrl.trim()) return
    localStorage.setItem("openvoice.runtimeBaseUrl", baseUrl.trim())
  }, [baseUrl])

  useEffect(() => {
    setHistoryEndpointSupported(true)
  }, [baseUrl])

  useEffect(() => {
    const url = new URL(window.location.href)
    const current = parseModeValue(url.searchParams.get("tab"))
    const hadLegacyModeParam = url.searchParams.has("mode")

    if (mode === "detailed") {
      if (current === null && !hadLegacyModeParam) {
        return
      }
      url.searchParams.delete("tab")
      url.searchParams.delete("mode")
      window.history.replaceState(window.history.state, "", url)
      return
    }

    if (current === mode && !hadLegacyModeParam) {
      return
    }
    url.searchParams.set("tab", mode)
    url.searchParams.delete("mode")
    window.history.replaceState(window.history.state, "", url)
  }, [mode])

  useEffect(() => {
    const syncFromUrl = () => {
      setRequestedSessionId(resolveSessionIdFromUrl())
    }
    window.addEventListener("popstate", syncFromUrl)
    return () => {
      window.removeEventListener("popstate", syncFromUrl)
    }
  }, [])

  useEffect(() => {
    if (mode !== "minimal") {
      setMinimalSettingsOpen(false)
    }
  }, [mode])

  useEffect(() => {
    if (!minimalSettingsOpen) return

    const onPointerDown = (event: MouseEvent) => {
      const target = event.target
      if (!(target instanceof Node)) return
      if (!minimalSettingsRef.current?.contains(target)) {
        setMinimalSettingsOpen(false)
      }
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMinimalSettingsOpen(false)
      }
    }

    window.addEventListener("mousedown", onPointerDown)
    window.addEventListener("keydown", onKeyDown)

    return () => {
      window.removeEventListener("mousedown", onPointerDown)
      window.removeEventListener("keydown", onKeyDown)
    }
  }, [minimalSettingsOpen])

  useEffect(() => {
    if (!historySidebarOpen) {
      return
    }

    const onPointerDown = (event: MouseEvent) => {
      const target = event.target
      if (!(target instanceof Node)) return
      if (!historySidebarRef.current?.contains(target)) {
        setHistorySidebarOpen(false)
      }
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setHistorySidebarOpen(false)
      }
    }

    window.addEventListener("mousedown", onPointerDown)
    window.addEventListener("keydown", onKeyDown)
    return () => {
      window.removeEventListener("mousedown", onPointerDown)
      window.removeEventListener("keydown", onKeyDown)
    }
  }, [historySidebarOpen])

  useEffect(() => {
    let cancelled = false
    const run = async () => {
      if (!baseUrl.trim()) return
      setEngineReadiness({
        checked: false,
        ok: false,
        message: "Checking runtime engine availability...",
      })
      try {
        const client = new OpenVoiceWebClient({ baseUrl })
        const catalog = await client.http.listEngines()
        if (cancelled) return

        const defaultStt = catalog.stt.find((entry) => entry.default) ?? catalog.stt[0]
        const sttOk = Boolean(defaultStt?.available)
        const sttReason = defaultStt ? `${defaultStt.id} (${defaultStt.status})` : "none"

        const vadEntries = catalog.vad ?? []
        const defaultVad = vadEntries.find((entry) => entry.default) ?? vadEntries[0]
        const vadRequired = vadEntries.length > 0
        const vadOk = !vadRequired || Boolean(defaultVad?.available)
        const vadReason = defaultVad ? `${defaultVad.id} (${defaultVad.status})` : "none"

        if (sttOk && vadOk) {
          setEngineReadiness({
            checked: true,
            ok: true,
            message: "Realtime STT/VAD engines are ready.",
          })
          return
        }

        const reasonParts = [
          `stt=${sttReason}`,
          vadRequired ? `vad=${vadReason}` : null,
        ].filter(Boolean)

        setEngineReadiness({
          checked: true,
          ok: false,
          message: `Realtime engines unavailable (${reasonParts.join(", ")}). Install backend deps for your configured STT/VAD engines (for example moonshine-voice + silero-vad, or Parakeet + silero-vad).`,
        })
      } catch (error) {
        if (cancelled) return
        const message = error instanceof Error ? error.message : String(error)
        setEngineReadiness({
          checked: true,
          ok: false,
          message: `Could not query runtime engines: ${message}`,
        })
      }
    }
    void run()
    return () => {
      cancelled = true
    }
  }, [baseUrl])

  useEffect(() => {
    const onBeforeUnload = () => {
      void traceReporterRef.current?.flush(true)
    }
    window.addEventListener("beforeunload", onBeforeUnload)
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload)
    }
  }, [])

  useEffect(() => {
    return () => {
      clearEventFlushTimer()
      clearSttUiTimer()
      clearLlmThinkingUiTimer()
      clearLlmResponseUiTimer()
      clearMicLevelUiTimer()
      clearMicHoldTimer()
      clearIdleTransitionTimer()
      clearErrorSpeechFallbackTimer()
      clearSpeechWatchdog()
      agentSignalUnsubscribeRef.current?.()
      agentSignalUnsubscribeRef.current = null
      agentRealtimeSignalUnsubscribeRef.current?.()
      agentRealtimeSignalUnsubscribeRef.current = null
      sessionStoreUnsubscribeRef.current?.()
      sessionStoreUnsubscribeRef.current = null
      transcriptUnsubscribeRef.current?.()
      transcriptUnsubscribeRef.current = null
      pendingTurnUnsubscribeRef.current?.()
      pendingTurnUnsubscribeRef.current = null
      sttProgressUnsubscribeRef.current?.()
      sttProgressUnsubscribeRef.current = null
      sttFinalMetaUnsubscribeRef.current?.()
      sttFinalMetaUnsubscribeRef.current = null
      routeStateUnsubscribeRef.current?.()
      routeStateUnsubscribeRef.current = null
      sessionStateUnsubscribeRef.current?.()
      sessionStateUnsubscribeRef.current = null
      turnPhaseUnsubscribeRef.current?.()
      turnPhaseUnsubscribeRef.current = null
      try {
        if ("speechSynthesis" in window) {
          window.speechSynthesis.cancel()
        }
      } catch {
        // ignore speech-synthesis cleanup errors
      }
      void disconnectForCleanupRef.current?.()
    }
  }, [])

  useEffect(() => {
    return () => {
      clearGenerationWatchdog()
    }
  }, [clearGenerationWatchdog])

  useEffect(() => {
    document.body.classList.toggle("minimal-mode-active", mode === "minimal")
    return () => {
      document.body.classList.remove("minimal-mode-active")
    }
  }, [mode])

  useEffect(() => {
    if (mode !== "minimal") {
      return
    }
    clearEventFlushTimer()
    eventBufferRef.current = []
    setEvents([])
    sttUiTextRef.current = ""
    clearSttUiTimer()
    flushSttUiText()
    llmThinkingUiTextRef.current = ""
    llmResponseTextRef.current = ""
    clearLlmThinkingUiTimer()
    clearLlmResponseUiTimer()
    flushLlmThinkingUiText()
    flushLlmResponseUiText()
    clearMicLevelUiTimer()
    queueMicLevelUi(0, true)
  }, [
    clearEventFlushTimer,
    clearLlmResponseUiTimer,
    clearLlmThinkingUiTimer,
    clearMicLevelUiTimer,
    clearSttUiTimer,
    flushLlmResponseUiText,
    flushLlmThinkingUiText,
    flushSttUiText,
    mode,
    queueMicLevelUi,
  ])

  const radialClass = useMemo(() => {
    if (isMicDisconnectedView) return "idle"
    if (visualTurnPhase === "agent_speaking") return "speaking"
    if (visualTurnPhase === "user_speaking") return "speaking"
    if (visualTurnPhase === "processing") return "thinking"
    if (sessionStatus !== "disconnected") return "ready"
    return "idle"
  }, [isMicDisconnectedView, sessionStatus, visualTurnPhase])

  if (mode === "minimal") {
    return (
      <main className="shell shell-minimal" aria-label="Open Voice SDK minimal mode">
        {historySidebarOpen ? (
          <aside className="history-overlay" aria-hidden="true" />
        ) : null}
        {historySidebarOpen ? (
          <aside className="history-sidebar" ref={historySidebarRef} aria-label="Conversation history">
            <div className="history-sidebar-head">
              <h2>Recent chats</h2>
              <div className="history-sidebar-actions">
                <Button
                  type="button"
                  className="history-open-session-btn"
                  onClick={() => void resumeHistorySession(selectedHistorySession?.sessionId ?? "")}
                  disabled={
                    isSwitchingSession
                    || !selectedHistorySession
                    || selectedHistorySession.sessionId === sessionRef.current?.sessionId
                  }
                >
                  {isSwitchingSession ? "Opening..." : "Open selected"}
                </Button>
                <Button type="button" className="history-close-btn" onClick={closeHistoryPanel}>Close</Button>
              </div>
            </div>
            <p className="history-subtitle">Last {SESSION_HISTORY_LIMIT} chats</p>
            {historyLoading ? <p className="history-status">Loading history...</p> : null}
            {historyError ? <p className="history-status history-status-error">{historyError}</p> : null}
            <div className="history-list" role="list">
              {recentHistoryItems.length === 0 ? (
                <p className="history-status">No previous sessions yet.</p>
              ) : null}
              {recentHistoryItems.map((item) => {
                const isCurrent = item.sessionId === sessionRef.current?.sessionId
                const isSelected = item.sessionId === selectedHistorySession?.sessionId
                return (
                  <article
                    key={item.sessionId}
                    role="listitem"
                    className={`history-item${isCurrent ? " current" : ""}${isSelected ? " selected" : ""}`}
                  >
                    <button
                      type="button"
                      className="history-open-btn"
                      onClick={() => void resumeHistorySession(item.sessionId)}
                      disabled={isCurrent || isSwitchingSession}
                      title={isCurrent ? "Current session" : "Open this session"}
                    >
                      <span className="history-item-title">{item.title}</span>
                      <span className="history-item-meta">{item.status} · {item.completedTurnCount} turns</span>
                    </button>
                    {item.transcript.length > 0 ? (
                      <div className="history-item-preview" aria-live="polite">
                        {item.transcript.slice(-3).map((turn, index) => (
                          <p key={`${item.sessionId}-${index}`} className={`history-line ${turn.role}`}>
                            <strong>{turn.role === "user" ? "You" : "AI"}:</strong> {transcriptSummaryText(turn)}
                          </p>
                        ))}
                      </div>
                    ) : (
                      <p className="history-empty">No transcript captured yet.</p>
                    )}
                  </article>
                )
              })}
            </div>
          </aside>
        ) : null}
        <section className="minimal-stage" aria-label="Minimal voice experience">
          <article className={`minimal-notebook${minimalCaptionsEnabled ? " show-captions" : ""}${minimalDetailEnabled ? " show-detail" : ""}`}>
            <div className="minimal-frame" aria-hidden="true">
              <span className="frame-line frame-line-top" />
              <span className="frame-line frame-line-right" />
              <span className="frame-line frame-line-bottom" />
              <span className="frame-line frame-line-left" />
            </div>

            <img src="/logo-icon.svg" alt="Open Voice" className="minimal-logo-img" />

            <div className="minimal-center">
              {MINIMAL_VISUALIZER_STYLE === "radial" ? (
                <div
                  className={`radial-viz ${radialClass}`}
                  style={{ "--mic-level": String(Math.max(0.14, micLevel / 100)) } as React.CSSProperties}
                >
                  <div className="radial-ring" />
                  <div className="radial-core" />
                  <div className="radial-bars">
                    {Array.from({ length: 24 }).map((_, i) => (
                      <span key={i} style={{ ["--i" as string]: i } as React.CSSProperties} />
                    ))}
                  </div>
                </div>
              ) : (
                <GridVisualizer
                  state={radialClass}
                  speakingRole={turnPhase === "agent_speaking" || visualTurnPhase === "agent_speaking" ? "agent" : "user"}
                  level={Math.max(0.08, micLevel / 100)}
                  rowCount={9}
                  columnCount={9}
                  interval={100}
                  bands={activeGridBands}
                />
              )}
            </div>

            <div className="minimal-mic-dock">
              <button
                type="button"
                className={`minimal-mic-btn ${micButtonVisualState}`}
                onClick={() => void handleMinimalMicToggle()}
                onPointerDown={handleMinimalMicPointerDown}
                onPointerUp={handleMinimalMicPointerRelease}
                onPointerCancel={handleMinimalMicPointerRelease}
                disabled={!sessionRef.current}
                aria-label={
                  micButtonVisualState === "hold"
                    ? "Holding to talk"
                    : isListening
                      ? "Turn microphone off"
                      : "Turn microphone on or hold to talk"
                }
                title={
                  micButtonVisualState === "hold"
                    ? "Hold-to-talk active"
                    : isListening
                      ? "Mic on (tap to turn off)"
                      : "Mic off (tap to turn on, hold to talk)"
                }
              >
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                  <rect x="9" y="3" width="6" height="11" rx="3" />
                  <path d="M5 11a7 7 0 0 0 14 0" />
                  <path d="M12 18v3" />
                  <path d="M9 21h6" />
                  {micButtonVisualState === "off" ? <path d="M4 4 20 20" /> : null}
                  {micButtonVisualState !== "off" ? (
                    <>
                      <path d="M17.7 9.4a3.5 3.5 0 0 1 0 5.2" />
                      <path d="M20.2 7.3a6.5 6.5 0 0 1 0 9.4" />
                    </>
                  ) : null}
                  {micButtonVisualState === "hold" ? <circle cx="12" cy="8.5" r="1.25" fill="currentColor" stroke="none" /> : null}
                </svg>
              </button>
            </div>

            <p
              className={`pending-text minimal-pending minimal-pending-inline${pendingTurnMessage ? " is-visible" : ""}`}
              aria-live="polite"
            >
              {pendingTurnMessage || " "}
            </p>
            <p
              className={`pending-text minimal-pending minimal-pending-inline${sttFinalMetaMessage ? " is-visible" : ""}`}
              aria-live="polite"
            >
              {sttFinalMetaMessage || " "}
            </p>

            {minimalCaptionsEnabled ? (
              <Card className="minimal-caption-card">
                <div className="mini-stt minimal-caption" aria-live="polite">
                  {sttLiveText || minimalCaptionStickyText || " "}
                </div>
              </Card>
            ) : null}

            {minimalDetailEnabled ? (
              <Card className="minimal-detail-card" aria-live="polite">
                <div className="minimal-detail-grid">
                  <div className="minimal-detail-item">
                    <p className="minimal-detail-label">State</p>
                    <p className="minimal-detail-value">{sessionStatusLabel}</p>
                  </div>
                  <div className="minimal-detail-item">
                    <p className="minimal-detail-label">Turn</p>
                    <p className="minimal-detail-value">{visualTurnPhase}</p>
                  </div>
                  <div className="minimal-detail-item">
                    <p className="minimal-detail-label">LLM</p>
                    <p className="minimal-detail-value">{routeModelLabel}</p>
                  </div>
                </div>
              </Card>
            ) : null}

            {lastError ? <p className="error-text minimal-error">{lastError}</p> : null}

            <div className="minimal-settings" ref={minimalSettingsRef}>
              {minimalSettingsOpen ? (
                <Card className="minimal-settings-menu" role="menu" aria-label="Minimal settings">
                  <Button
                    type="button"
                    className={`minimal-setting-toggle${minimalCaptionsEnabled ? " enabled" : ""}`}
                    onClick={() => setMinimalCaptionsEnabled((prev) => !prev)}
                  >
                    <span>Captions</span>
                    <span aria-hidden="true">{minimalCaptionsEnabled ? "On" : "Off"}</span>
                  </Button>
                  <Button
                    type="button"
                    className={`minimal-setting-toggle${minimalDetailEnabled ? " enabled" : ""}`}
                    onClick={() => setMinimalDetailEnabled((prev) => !prev)}
                  >
                    <span>Detail</span>
                    <span aria-hidden="true">{minimalDetailEnabled ? "On" : "Off"}</span>
                  </Button>
                  <Button
                    type="button"
                    className="minimal-setting-toggle"
                    onClick={() => {
                      setMinimalSettingsOpen(false)
                      openHistoryPanel()
                    }}
                  >
                    <span>History</span>
                    <span aria-hidden="true">Open</span>
                  </Button>
                </Card>
              ) : null}

              <Button
                type="button"
                className="minimal-settings-btn"
                onClick={() => setMinimalSettingsOpen((prev) => !prev)}
                aria-haspopup="menu"
                aria-expanded={minimalSettingsOpen}
                aria-label="Settings"
              >
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                  <line x1="4" y1="6" x2="20" y2="6" />
                  <circle cx="9" cy="6" r="2" />
                  <line x1="4" y1="12" x2="20" y2="12" />
                  <circle cx="15" cy="12" r="2" />
                  <line x1="4" y1="18" x2="20" y2="18" />
                  <circle cx="11" cy="18" r="2" />
                </svg>
              </Button>
            </div>
          </article>
        </section>
      </main>
    )
  }

  return (
    <main className="shell" aria-label="Open Voice SDK integration demo">
      {historySidebarOpen ? (
        <aside className="history-overlay" aria-hidden="true" onClick={closeHistoryPanel} />
      ) : null}
      {historySidebarOpen ? (
        <aside className="history-sidebar" ref={historySidebarRef} aria-label="Conversation history">
          <div className="history-sidebar-head">
            <h2>Recent chats</h2>
            <div className="history-sidebar-actions">
              <Button
                type="button"
                className="history-open-session-btn"
                onClick={() => void resumeHistorySession(selectedHistorySession?.sessionId ?? "")}
                disabled={
                  isSwitchingSession
                  || !selectedHistorySession
                  || selectedHistorySession.sessionId === sessionRef.current?.sessionId
                }
              >
                {isSwitchingSession ? "Opening..." : "Open selected"}
              </Button>
              <Button type="button" className="history-close-btn" onClick={closeHistoryPanel}>Close</Button>
            </div>
          </div>
          <p className="history-subtitle">Last {SESSION_HISTORY_LIMIT} chats</p>
          {historyLoading ? <p className="history-status">Loading history...</p> : null}
          {historyError ? <p className="history-status history-status-error">{historyError}</p> : null}
          <div className="history-list" role="list">
            {recentHistoryItems.length === 0 ? (
              <p className="history-status">No previous sessions yet.</p>
            ) : null}
            {recentHistoryItems.map((item) => {
              const isCurrent = item.sessionId === sessionRef.current?.sessionId
              const isSelected = item.sessionId === selectedHistorySessionId
              return (
                <article
                  key={item.sessionId}
                  role="listitem"
                  className={`history-item${isCurrent ? " current" : ""}${isSelected ? " selected" : ""}`}
                >
                  <button
                    type="button"
                    className="history-open-btn"
                    onClick={() => void resumeHistorySession(item.sessionId)}
                    disabled={isCurrent || isSwitchingSession}
                    title={isCurrent ? "Current session" : "Open this session"}
                  >
                    <span className="history-item-title">{item.title}</span>
                    <span className="history-item-meta">{item.status} · {item.completedTurnCount} turns</span>
                  </button>
                  {item.transcript.length > 0 ? (
                    <div className="history-item-preview" aria-live="polite">
                      {item.transcript.slice(-3).map((turn, index) => (
                        <p key={`${item.sessionId}-${index}`} className={`history-line ${turn.role}`}>
                          <strong>{turn.role === "user" ? "You" : "AI"}:</strong> {transcriptSummaryText(turn)}
                        </p>
                      ))}
                    </div>
                  ) : (
                    <p className="history-empty">No transcript captured yet.</p>
                  )}
                </article>
              )
            })}
          </div>
        </aside>
      ) : null}
      <Card className="hero">
        <h1>OpenVoice SDK Demo</h1>
        <dl className="hero-status" aria-live="polite">
          <div>
            <dt>Session</dt>
            <dd>{sessionStatusLabel}</dd>
          </div>
          <div>
            <dt>Session ID</dt>
            <dd>{sessionId}</dd>
          </div>
        </dl>
      </Card>

      <Card className="engine-banner" aria-live="polite">
        <p className={engineReadiness.ok ? "engine-ok" : "engine-bad"}>{engineReadiness.message}</p>
      </Card>

      <TabsList aria-label="Demo tabs">
        <TabsTrigger active={mode === "detailed"} onClick={() => setMode("detailed")}>Detailed</TabsTrigger>
        <TabsTrigger active={mode !== "detailed"} onClick={() => setMode("minimal")}>Minimal</TabsTrigger>
      </TabsList>

      <Card className="toolbar" aria-label="SDK controls">
        <Label>
          Runtime URL
          <Input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} />
        </Label>
        <Label>
          Voice
          <Input value={voiceId} onChange={(event) => setVoiceId(event.target.value)} />
        </Label>
        <Label>
          Queue policy
          <Select value={queuePolicy} onChange={(event) => setQueuePolicy(event.target.value as "enqueue" | "send_now" | "inject_next_loop")}> 
            <option value="enqueue">enqueue</option>
            <option value="send_now">send_now</option>
            <option value="inject_next_loop">inject_next_loop</option>
          </Select>
        </Label>
        <div className="actions">
          <Button disabled={!canConnect || !engineReadiness.ok} onClick={() => void connect()}>Connect</Button>
          <Button disabled={!sessionRef.current} onClick={() => void disconnect()}>Disconnect</Button>
          <Button disabled={!sessionRef.current || isListening} onClick={() => void startListening()}>Start listening</Button>
          <Button disabled={!isListening} onClick={() => void handleToolbarStopListening()}>Stop listening</Button>
          <Button disabled={!sessionRef.current} onClick={interrupt}>Interrupt</Button>
          {/* <Button type="button" onClick={openHistoryPanel}>History</Button> */}
        </div>
        {!isListening && sessionRef.current ? (
          <p className="error-text">Mic is not streaming yet. Click `Start listening` or allow microphone permission.</p>
        ) : null}
        {lastError ? <p className="error-text">{lastError}</p> : null}
        {pendingTurnMessage ? <p className="pending-text">{pendingTurnMessage}</p> : null}
        {sttFinalMetaMessage ? <p className="pending-text">{sttFinalMetaMessage}</p> : null}
      </Card>

      <Card className="route-overview" aria-live="polite">
        <div className="route-overview-grid">
          <div className="route-overview-item">
            <p className="route-overview-label">Router output</p>
            <p className="route-overview-value">{routeName}</p>
          </div>
          <div className="route-overview-item">
            <p className="route-overview-label">LLM model</p>
            <p className="route-overview-value">{routeModelLabel}</p>
          </div>
        </div>
      </Card>

      <section className="pipeline-grid">
        <Card className="stage">
          <h2>STT (Realtime user transcript)</h2>
          <div className="stream-card">{sttLiveText || " "}</div>
          <p className="subcopy">Mic level: {micLevel}%</p>
          {sttFinalMetaMessage ? <p className="subcopy">{sttFinalMetaMessage}</p> : null}
        </Card>
        <Card className="stage">
          <h2>LLM Thinking</h2>
          <div className="stream-card">{llmThinkingText || toolActivityText || " "}</div>
        </Card>
        <Card className="stage">
          <h2>Tool Activity</h2>
          <div className="stream-card">{toolActivityText || " "}</div>
          <p className="subcopy">Status: {toolActivityStatus || "-"}</p>
        </Card>
        <Card className="stage">
          <h2>LLM Response</h2>
          <div className="stream-card">{llmResponseText || " "}</div>
          <p className="subcopy">Speaking now: {currentSpokenSegment || "-"}</p>
        </Card>
        <Card className="stage transcript-stage">
          <h2>Transcript</h2>
          <div className="transcript">
            {transcript.map((item, index) => (
              <div key={`${item.role}-${index}`} className={`bubble ${item.role}`}>{item.text}</div>
            ))}
          </div>
        </Card>
      </section>

      <Card className="diagnostics">
        <h2>Event Trace</h2>
        <pre>{events.join("\n\n")}</pre>
      </Card>
    </main>
  )
}
