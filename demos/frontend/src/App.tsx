import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  FrontendTraceReporter,
  OpenVoiceWebClient,
  toRuntimeConfigPayload,
  type AudioOutputAdapter,
  type ConversationEvent,
  type RuntimeSessionConfig,
  type TtsChunk,
  type WebVoiceSession,
} from "@open-voice/web-sdk"
import { Button, Card, Input, Label, Select, TabsList, TabsTrigger } from "./components/ui"
import { GridVisualizer } from "./components/GridVisualizer"
import thinkingCueUrl from "../../../packages/web-sdk/examples/assets/sfx/achievement-fx.wav?url"

type Mode = "detailed" | "minimal"
type TurnPhase = "idle" | "listening" | "user_speaking" | "processing" | "agent_speaking"

const MINIMAL_VISUALIZER_STYLE: "radial" | "grid" = "grid"

interface TranscriptItem {
  role: "user" | "assistant"
  text: string
}

interface EngineReadiness {
  checked: boolean
  ok: boolean
  message: string
}

type SttProgressStatus =
  | "queued"
  | "transcribing"
  | "stabilizing"
  | "waiting_final"
  | "retry_scheduled"

interface SttProgress {
  status: SttProgressStatus
  waitedMs: number
  attempt: number
}

type PendingTurnPhase =
  | "idle"
  | "commit_sent"
  | "awaiting_backend"
  | "slow"
  | "degraded"
  | "timeout"

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
const AUDIO_LO_PASS = 100
const AUDIO_HI_PASS = 200
const DEMO_MIN_SPEECH_DURATION_MS = 180
const DEMO_VAD_ACTIVATION_THRESHOLD = 0.55
const DEMO_UI_VAD_PROBABILITY_THRESHOLD = 0.6
const DEMO_INTERRUPT_COOLDOWN_MS = 60
const DEMO_INTERRUPT_MIN_DURATION_SECONDS = 0.05
const DEMO_INTERRUPT_MIN_WORDS = 1
const DEMO_STT_TRANSCRIPT_TIMEOUT_MS = 120
const DEMO_MIN_SILENCE_DURATION_MS = 140
const DEMO_POST_RELEASE_PROCESSING_GRACE_MS = 1400
const DEMO_IDLE_TRANSITION_DELAY_MS = 220
const DEMO_SLOW_STT_STABILIZATION_MS = 0
const DEMO_MIC_STOP_COMMIT_GRACE_MS = 900
const DEMO_AUTO_COMMIT_MIN_INTERVAL_MS = 250
const DEMO_ROUTER_TIMEOUT_MS = 7000
const DEMO_CAPTURE_BUFFER_SIZE = 256
const DEMO_PENDING_TURN_SLOW_MS = 2000
const DEMO_PENDING_TURN_DEGRADED_MS = 8000
const DEMO_PENDING_TURN_TIMEOUT_MS = 25000
const DEMO_STT_FINAL_TIMEOUT_MS = 550
const DEMO_ROUTER_MODE: "disabled" | "fallback_only" | "enabled" = "fallback_only"
const DEMO_PHASE_DEBOUNCE_MS = 180
const DEMO_FORCE_SEND_NOW_DEFAULT = true
const DEMO_DISABLE_STT_STABILIZATION = true
const MINIMAL_CAPTIONS_STORAGE_KEY = "openvoice.minimal.captions"
const MINIMAL_DETAIL_STORAGE_KEY = "openvoice.minimal.detail"
const FILLER_PARTIAL_TOKENS = new Set([
  "uh",
  "um",
  "hmm",
  "mm",
  "ah",
  "oh",
  "er",
  "erm",
])

function zeroBands(count = AUDIO_BAND_COUNT): number[] {
  return Array.from({ length: count }, () => 0)
}

function normalizeDbValue(value: number): number {
  const minDb = -100
  const maxDb = -10
  const clamped = Math.max(minDb, Math.min(maxDb, value))
  const normalized = 1 - (clamped * -1) / 100
  return Math.sqrt(normalized)
}

function computeAnalyserBands(analyser: AnalyserNode, bands: number): number[] {
  const dataArray = new Float32Array(analyser.frequencyBinCount)
  analyser.getFloatFrequencyData(dataArray)

  const sliced = dataArray.slice(AUDIO_LO_PASS, AUDIO_HI_PASS)
  const normalized = sliced.map((value) => {
    if (value === -Infinity) return 0
    return normalizeDbValue(value)
  })

  const totalBins = normalized.length
  const chunks: number[] = []

  for (let i = 0; i < bands; i += 1) {
    const startIndex = Math.floor((i * totalBins) / bands)
    const endIndex = Math.floor(((i + 1) * totalBins) / bands)
    const chunk = normalized.slice(startIndex, endIndex)

    if (chunk.length === 0) {
      chunks.push(0)
      continue
    }

    const summed = chunk.reduce((acc, val) => acc + val, 0)
    chunks.push(summed / chunk.length)
  }

  return chunks
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

function meaningfulWordTokens(text: string): string[] {
  return text
    .toLowerCase()
    .split(/\s+/)
    .map((token) => token.replace(/[^a-z0-9]/g, ""))
    .filter((token) => token.length >= 2)
}

function isInterruptWorthyPartial(text: string): boolean {
  const trimmed = text.trim()
  if (trimmed.length < 4) return false

  const tokens = meaningfulWordTokens(trimmed)
  if (tokens.length === 0) return false

  const nonFillerTokens = tokens.filter((token) => !FILLER_PARTIAL_TOKENS.has(token))
  if (nonFillerTokens.length >= 2) return true
  return nonFillerTokens.length === 1 && nonFillerTokens[0].length >= 3
}

class BrowserMicInput {
  private sequence = 0
  private ctx: AudioContext | null = null
  private processor: ScriptProcessorNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private stream: MediaStream | null = null
  private analyser: AnalyserNode | null = null
  private bandTimer: number | null = null
  private running = false
  private captureToken = 0

  constructor(private readonly sendChunk: (chunk: {
    data: ArrayBuffer
    sequence: number
    encoding: "pcm_s16le"
    sampleRateHz: number
    channels: number
    durationMs: number
  }) => void | Promise<void>, private readonly onLevel: (value: number) => void, private readonly onBands: (bands: number[]) => void, private readonly onChunkMeta?: (meta: {
    sequence: number
    sampleRateHz: number
    channels: number
    durationMs: number
    bytes: number
  }) => void) {}

  async start(): Promise<void> {
    const captureToken = ++this.captureToken
    this.running = true
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: { ideal: 24000 },
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })

    this.ctx = new AudioContext({ sampleRate: 24000 })
    this.source = this.ctx.createMediaStreamSource(this.stream)
    this.processor = this.ctx.createScriptProcessor(DEMO_CAPTURE_BUFFER_SIZE, 1, 1)
    this.analyser = this.ctx.createAnalyser()
    this.analyser.fftSize = 2048
    this.analyser.smoothingTimeConstant = 0

    this.processor.onaudioprocess = (event) => {
      if (!this.running || captureToken !== this.captureToken) {
        return
      }
      const channel = event.inputBuffer.getChannelData(0)
      let peak = 0
      const pcm = new Int16Array(channel.length)
      for (let i = 0; i < channel.length; i += 1) {
        const sample = Math.max(-1, Math.min(1, channel[i]))
        peak = Math.max(peak, Math.abs(sample))
        pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
      }
      if (!this.running || captureToken !== this.captureToken) {
        return
      }
      const sampleRate = this.ctx?.sampleRate ?? 24000
      const sequence = this.sequence
      this.sequence += 1
      this.onLevel(peak)
      void Promise.resolve(
        this.sendChunk({
          data: pcm.buffer,
          sequence,
          encoding: "pcm_s16le",
          sampleRateHz: sampleRate,
          channels: 1,
          durationMs: (pcm.length / sampleRate) * 1000,
        }),
      ).catch(() => undefined)
      this.onChunkMeta?.({
        sequence,
        sampleRateHz: sampleRate,
        channels: 1,
        durationMs: (pcm.length / sampleRate) * 1000,
        bytes: pcm.byteLength,
      })
    }

    this.source.connect(this.processor)
    this.source.connect(this.analyser)
    this.processor.connect(this.ctx.destination)

    this.bandTimer = window.setInterval(() => {
      if (!this.analyser) return
      this.onBands(computeAnalyserBands(this.analyser, AUDIO_BAND_COUNT))
    }, 32)
  }

  async stop(): Promise<void> {
    this.running = false
    this.captureToken += 1
    if (this.bandTimer !== null) {
      window.clearInterval(this.bandTimer)
      this.bandTimer = null
    }
    if (this.processor) {
      this.processor.onaudioprocess = null
    }
    this.processor?.disconnect()
    this.analyser?.disconnect()
    this.source?.disconnect()
    this.stream?.getTracks().forEach((track) => track.stop())
    await this.ctx?.close()
    this.processor = null
    this.analyser = null
    this.source = null
    this.stream = null
    this.ctx = null
    this.onLevel(0)
    this.onBands(zeroBands())
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
      }, 32)
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
    await this.ensureContext(chunk.sampleRateHz)
    if (!this.audioContext) return

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

  async flush(): Promise<void> {
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
    kind: "mcp" as const,
    description: "Search the web for current information and relevant sources.",
  },
]

const OPEN_VOICE_SYSTEM_PROMPT = [
  "You are Open Voice, a realtime voice-first assistant for conversation and web research.",
  "Prioritize natural spoken responses that are concise, clear, and interruption-friendly.",
  "If a newer user utterance arrives, immediately abandon stale context and continue from the latest user intent.",
  "For current events or other time-sensitive questions, always search the web before answering.",
  "Never guess or rely on stale memory for news, politics, markets, sports, weather, or other live facts.",
  "Use tools when needed, but never expose internal routing, model, or tool implementation details.",
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
  window.history.replaceState(window.history.state, "", url)
}

function isSessionClosedOrFailed(status: unknown): boolean {
  return status === "closed" || status === "failed"
}

function resolveStoredFlag(storageKey: string, fallback: boolean): boolean {
  const raw = localStorage.getItem(storageKey)
  if (raw === "1" || raw === "true") return true
  if (raw === "0" || raw === "false") return false
  return fallback
}

export function App() {
  const [mode, setMode] = useState<Mode>(resolveInitialMode)
  const [baseUrl, setBaseUrl] = useState(resolveInitialRuntimeBaseUrl)
  const [requestedSessionId, setRequestedSessionId] = useState<string | null>(resolveSessionIdFromUrl)
  const [voiceId, setVoiceId] = useState("af_heart")
  const [queuePolicy, setQueuePolicy] = useState<"enqueue" | "send_now" | "inject_next_loop">(
    DEMO_FORCE_SEND_NOW_DEFAULT ? "send_now" : "send_now",
  )

  const [sessionId, setSessionId] = useState("-")
  const [sessionStatus, setSessionStatus] = useState("disconnected")
  const [turnPhase, setTurnPhase] = useState<TurnPhase>("idle")
  const [sttLiveText, setSttLiveText] = useState("")
  const [llmThinkingText, setLlmThinkingText] = useState("")
  const [llmResponseText, setLlmResponseText] = useState("")
  const [routeName, setRouteName] = useState("-")
  const [routeProvider, setRouteProvider] = useState<string | null>(null)
  const [routeModel, setRouteModel] = useState<string | null>(null)
  const [events, setEvents] = useState<string[]>([])
  const [transcript, setTranscript] = useState<TranscriptItem[]>([])
  const [isListening, setIsListening] = useState(false)
  const [micLevel, setMicLevel] = useState(0)
  const [micBands, setMicBands] = useState<number[]>(() => zeroBands())
  const [ttsBands, setTtsBands] = useState<number[]>(() => zeroBands())
  const [ttsPlaybackActive, setTtsPlaybackActive] = useState(false)
  const [ttsStreamActive, setTtsStreamActive] = useState(false)
  const [pendingSpeechAfterThinking, setPendingSpeechAfterThinking] = useState(false)
  const [llmThinkingActive, setLlmThinkingActive] = useState(false)
  const [thinkingHoldUntil, setThinkingHoldUntil] = useState<number>(0)
  const [pendingTurnPhase, setPendingTurnPhase] = useState<PendingTurnPhase>("idle")
  const [pendingTurnElapsedMs, setPendingTurnElapsedMs] = useState(0)
  const [sttProgress, setSttProgress] = useState<SttProgress | null>(null)
  const [sttFinalMeta, setSttFinalMeta] = useState<{
    revision: number | null
    finality: "stable" | "revised" | "duplicate" | null
    deferred: boolean | null
    previousText: string | null
  } | null>(null)
  const [isMicHoldActive, setIsMicHoldActive] = useState(false)
  const [minimalSettingsOpen, setMinimalSettingsOpen] = useState(false)
  const [minimalCaptionsEnabled, setMinimalCaptionsEnabled] = useState(() =>
    resolveStoredFlag(MINIMAL_CAPTIONS_STORAGE_KEY, true),
  )
  const [minimalDetailEnabled, setMinimalDetailEnabled] = useState(() =>
    resolveStoredFlag(MINIMAL_DETAIL_STORAGE_KEY, false),
  )
  const [lastError, setLastError] = useState("")
  const [engineReadiness, setEngineReadiness] = useState<EngineReadiness>({
    checked: false,
    ok: false,
    message: "Checking runtime engine availability...",
  })

  const sessionRef = useRef<WebVoiceSession | null>(null)
  const sdkPlayerRef = useRef<AudioOutputAdapter | null>(null)
  const micRef = useRef<BrowserMicInput | null>(null)
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
  const pendingTurnStartedAtRef = useRef<number | null>(null)
  const pendingTurnTimerRef = useRef<number | null>(null)
  const pendingTurnClientIdRef = useRef<string | null>(null)
  const vadSpeechStartedAtRef = useRef(0)
  const lastAutoCommitAtRef = useRef(0)
  const micLifecycleTokenRef = useRef(0)
  const isDisconnectingRef = useRef(false)
  const micHoldActiveRef = useRef(false)
  const suppressMicTapRef = useRef(false)
  const suppressTtsUntilNextUserFinalRef = useRef(false)
  const activeGenerationIdRef = useRef<string | null>(null)
  const rejectedGenerationIdsRef = useRef<Set<string>>(new Set())
  const minimalMicUserControlledRef = useRef(false)
  const sessionStatusRef = useRef(sessionStatus)
  const turnPhaseRef = useRef<TurnPhase>(turnPhase)
  const traceReporterRef = useRef<FrontendTraceReporter | null>(null)
  const minimalSettingsRef = useRef<HTMLDivElement | null>(null)
  const llmResponseTextRef = useRef("")
  const turnPhaseLastSetAtRef = useRef(0)
  const turnPhaseStickyUntilRef = useRef(0)

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
    if (turnPhase === "agent_speaking") return "speaking"
    if (llmThinkingActive) return "thinking"
    if (turnPhase === "processing") return "thinking"
    if (turnPhase === "user_speaking") return "listening"
    return sessionStatus
  }, [llmThinkingActive, sessionStatus, turnPhase])

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
    thinkingHoldUntilRef.current = thinkingHoldUntil
  }, [thinkingHoldUntil])

  useEffect(() => {
    llmThinkingActiveRef.current = llmThinkingActive
  }, [llmThinkingActive])

  const resetAssistantPanels = useCallback((turnId?: string | null) => {
    const normalizedTurnId = turnId ?? null
    if (normalizedTurnId && activeAssistantTurnIdRef.current === normalizedTurnId) {
      return
    }
    activeAssistantTurnIdRef.current = normalizedTurnId
    llmResponseTextRef.current = ""
    setLlmThinkingText("")
    setLlmResponseText("")
  }, [])

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

  const clearPendingTurnTimer = useCallback(() => {
    if (pendingTurnTimerRef.current !== null) {
      window.clearInterval(pendingTurnTimerRef.current)
      pendingTurnTimerRef.current = null
    }
  }, [])

  const clearPendingTurn = useCallback(() => {
    pendingTurnStartedAtRef.current = null
    pendingTurnClientIdRef.current = null
    clearPendingTurnTimer()
    setPendingTurnElapsedMs(0)
    setPendingTurnPhase("idle")
    setSttProgress(null)
    setSttFinalMeta(null)
  }, [clearPendingTurnTimer])

  const startPendingTurn = useCallback((clientTurnId: string): boolean => {
    const existingStartedAt = pendingTurnStartedAtRef.current
    if (
      existingStartedAt !== null
      && Date.now() - existingStartedAt < DEMO_PENDING_TURN_TIMEOUT_MS
    ) {
      return false
    }
    pendingTurnClientIdRef.current = clientTurnId
    pendingTurnStartedAtRef.current = Date.now()
    setPendingTurnElapsedMs(0)
    setPendingTurnPhase("commit_sent")
    clearPendingTurnTimer()
    pendingTurnTimerRef.current = window.setInterval(() => {
      const startedAt = pendingTurnStartedAtRef.current
      if (!startedAt) {
        clearPendingTurnTimer()
        return
      }
      const elapsedMs = Date.now() - startedAt
      setPendingTurnElapsedMs(elapsedMs)
      if (elapsedMs >= DEMO_PENDING_TURN_TIMEOUT_MS) {
        setPendingTurnPhase("timeout")
      } else if (elapsedMs >= DEMO_PENDING_TURN_DEGRADED_MS) {
        setPendingTurnPhase("degraded")
      } else if (elapsedMs >= DEMO_PENDING_TURN_SLOW_MS) {
        setPendingTurnPhase("slow")
      }
    }, 300)
    return true
  }, [clearPendingTurnTimer])

  const markPendingTurnResolved = useCallback((via: string) => {
    const startedAt = pendingTurnStartedAtRef.current
    if (startedAt) {
      traceReporterRef.current?.trackLocal(
        "ui.pending_turn.resolved",
        {
          via,
          elapsed_ms: Date.now() - startedAt,
        },
        "ui.state",
      )
    }
    clearPendingTurn()
  }, [clearPendingTurn])

  const markPendingTurnCancelled = useCallback((reason: string) => {
    const startedAt = pendingTurnStartedAtRef.current
    if (startedAt) {
      traceReporterRef.current?.trackLocal(
        "ui.pending_turn.cancelled",
        {
          reason,
          elapsed_ms: Date.now() - startedAt,
        },
        "ui.state",
      )
    }
    clearPendingTurn()
  }, [clearPendingTurn])

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
    if (!sttProgress) return ""
    const waitedSeconds = Math.max(0, Math.round(sttProgress.waitedMs / 1000))
    if (sttProgress.status === "queued") {
      return `Turn accepted (attempt ${sttProgress.attempt}).`
    }
    if (sttProgress.status === "transcribing") {
      return "Transcribing audio..."
    }
    if (sttProgress.status === "waiting_final") {
      return "Waiting for final transcript..."
    }
    if (sttProgress.status === "stabilizing") {
      return waitedSeconds > 0
        ? `Stabilizing transcript (${waitedSeconds}s)...`
        : "Stabilizing transcript..."
    }
    return waitedSeconds > 0
      ? `Retry scheduled in ${waitedSeconds}s (attempt ${sttProgress.attempt}).`
      : `Retry scheduled (attempt ${sttProgress.attempt}).`
  }, [sttProgress])

  const sttFinalMetaMessage = useMemo(() => {
    if (!sttFinalMeta) return ""
    const revisionLabel = sttFinalMeta.revision ?? 1
    if (sttFinalMeta.finality === "revised") {
      return sttFinalMeta.previousText
        ? `Final revised (r${revisionLabel}): ${sttFinalMeta.previousText} -> ${sttLiveText || "updated"}`
        : `Final revised (r${revisionLabel}).`
    }
    if (sttFinalMeta.finality === "duplicate") {
      return `Duplicate final received (r${revisionLabel}).`
    }
    return `Final transcript stable (r${revisionLabel}).`
  }, [sttFinalMeta, sttLiveText])

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
    const now = Date.now()
    const current = turnPhaseRef.current
    if (
      phase !== current
      && (phase === "listening" || phase === "processing")
      && now < turnPhaseStickyUntilRef.current
    ) {
      return
    }
    if (phase !== current && now - turnPhaseLastSetAtRef.current < DEMO_PHASE_DEBOUNCE_MS) {
      return
    }
    turnPhaseLastSetAtRef.current = now

    if (phase === "processing" || phase === "agent_speaking") {
      turnPhaseStickyUntilRef.current = now + DEMO_PHASE_DEBOUNCE_MS
    } else if (phase === "user_speaking") {
      turnPhaseStickyUntilRef.current = now + Math.max(80, DEMO_PHASE_DEBOUNCE_MS / 2)
    }

    if (phase === "idle") {
      scheduleIdleTransition()
      return
    }
    clearIdleTransitionTimer()
    setTurnPhase(phase)
  }, [clearIdleTransitionTimer, scheduleIdleTransition])

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
    traceReporterRef.current?.trackLocal("audio.output.flush", { reason: "hard_stop" }, "audio")
    rejectGeneration(activeGenerationIdRef.current)
    await sdkPlayerRef.current?.flush().catch(() => undefined)
    thinkingPlayerRef.current?.stop()
  }, [rejectGeneration])

  const activeGridBands = useMemo(() => {
    return turnPhase === "agent_speaking" ? ttsBands : micBands
  }, [micBands, ttsBands, turnPhase])

  const runtimeConfig = useMemo<RuntimeSessionConfig>(() => {
    const effectivePolicy = DEMO_FORCE_SEND_NOW_DEFAULT
      ? "send_now"
      : (mode === "minimal" ? "send_now" : queuePolicy)
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
      },
      llm: {
        systemPrompt: OPEN_VOICE_SYSTEM_PROMPT,
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
  }, [mode, queuePolicy])

  const appendEvent = useCallback((event: ConversationEvent) => {
    if (mode === "minimal") {
      return
    }
    if (event.type === "vad.state" && event.kind === "inference") {
      return
    }
    setEvents((prev) => [...prev.slice(-399), JSON.stringify(event, null, 2)])
  }, [mode])

  const handleEvent = useCallback(async (event: ConversationEvent) => {
    const shouldAutoBargeInterrupt = mode === "minimal" || queuePolicy === "send_now"
    appendEvent(event)
    const eventGenerationId = typeof event.generation_id === "string" ? event.generation_id : null

    if (event.type === "session.ready") {
      markPendingTurnResolved("session.ready")
      setSessionStatus("ready")
      setLlmThinkingActive(false)
      setTurnPhaseStable(micRef.current ? "listening" : "idle")
      pendingSpeechAfterThinkingRef.current = false
      setPendingSpeechAfterThinking(false)
      clearSpeechWatchdog()
      return
    }

    if (event.type === "turn.accepted") {
      if (!pendingTurnClientIdRef.current || pendingTurnClientIdRef.current !== event.client_turn_id) {
        return
      }
      setPendingTurnPhase("awaiting_backend")
      setSttProgress((prev) => ({
        status: "queued",
        waitedMs: prev?.waitedMs ?? 0,
        attempt: prev?.attempt ?? 1,
      }))
      return
    }

    if (event.type === "stt.status") {
      if (!pendingTurnStartedAtRef.current) {
        return
      }
      if (event.status === "retry_scheduled") {
        setPendingTurnPhase("commit_sent")
      }
      setSttProgress({
        status: event.status,
        waitedMs: event.waited_ms ?? 0,
        attempt: event.attempt ?? 1,
      })
      return
    }

    if (event.type === "session.status") {
      if (eventGenerationId && rejectedGenerationIdsRef.current.has(eventGenerationId)) {
        return
      }
      traceReporterRef.current?.trackLocal(
        "ui.session.status",
        {
          status: event.status,
          reason: event.reason ?? null,
        },
        "ui.state",
      )
      setSessionStatus(event.status)
      setLlmThinkingActive(event.status === "thinking" || event.status === "transcribing")
      if (event.status === "transcribing") {
        if (pendingTurnStartedAtRef.current) {
          markPendingTurnResolved("session.status.transcribing")
        }
        if (eventGenerationId) {
          activeGenerationIdRef.current = eventGenerationId
        }
        setTurnPhaseStable("processing")
      }
      else if (event.status === "thinking") {
        if (pendingTurnStartedAtRef.current) {
          markPendingTurnResolved("session.status.thinking")
        }
        if (eventGenerationId) {
          activeGenerationIdRef.current = eventGenerationId
        }
        setTurnPhaseStable("processing")
      }
      else if (event.status === "speaking") {
        if (pendingTurnStartedAtRef.current) {
          markPendingTurnResolved("session.status.speaking")
        }
        if (suppressTtsUntilNextUserFinalRef.current) {
          return
        }
        if (eventGenerationId) {
          activeGenerationIdRef.current = eventGenerationId
        }
        setTurnPhaseStable("agent_speaking")
        pendingSpeechAfterThinkingRef.current = false
        setPendingSpeechAfterThinking(false)
        clearSpeechWatchdog()
      }
      else if (event.status === "listening" || event.status === "ready") {
        const nextPhase = (() => {
          if (
            pendingSpeechAfterThinkingRef.current
            || ttsPlayingRef.current
            || ttsStreamActiveRef.current
          ) {
            return "agent_speaking" as TurnPhase
          }
          if (pendingTurnStartedAtRef.current) {
            return "processing" as TurnPhase
          }
          const recentUserSpeech =
            Date.now() - lastUserSpeechAtRef.current <= DEMO_POST_RELEASE_PROCESSING_GRACE_MS
          if (turnPhaseRef.current === "processing" && recentUserSpeech) {
            return "processing" as TurnPhase
          }
          return (micRef.current ? "listening" : "idle") as TurnPhase
        })()
        setTurnPhaseStable(nextPhase)
      } else if (
        event.status === "interrupted" ||
        event.status === "closed" ||
        event.status === "failed"
      ) {
        setLlmThinkingActive(false)
        setTurnPhaseStable("idle")
        pendingSpeechAfterThinkingRef.current = false
        setPendingSpeechAfterThinking(false)
        clearSpeechWatchdog()
      }

      return
    }

    if (event.type === "vad.state") {
      if (event.kind === "start_of_speech") {
        vadSpeechStartedAtRef.current = Date.now()
      }

      const canRenderUserSpeech =
        micRef.current
        && (sessionStatusRef.current === "listening" || sessionStatusRef.current === "ready")
      const confidentInferenceSpeech =
        event.kind === "inference"
        && event.speaking === true
        && typeof event.probability === "number"
        && event.probability >= DEMO_UI_VAD_PROBABILITY_THRESHOLD
      const speechStartDetected = event.kind === "start_of_speech" || confidentInferenceSpeech

      if (speechStartDetected) {
        if (pendingTurnStartedAtRef.current) {
          markPendingTurnResolved("vad.speech_start")
        }
        const agentCurrentlySpeaking =
          turnPhaseRef.current === "agent_speaking"
          || sessionStatusRef.current === "speaking"
          || ttsPlayingRef.current
          || ttsStreamActiveRef.current
        if (!agentCurrentlySpeaking && canRenderUserSpeech) {
          lastUserSpeechAtRef.current = Date.now()
          setTurnPhaseStable("user_speaking")
        }
      } else if (event.kind === "end_of_speech" && event.speaking === false) {
        const hadRecentUserSpeech =
          Date.now() - lastUserSpeechAtRef.current <= DEMO_MIC_STOP_COMMIT_GRACE_MS
        const speakingWindowMs = Date.now() - vadSpeechStartedAtRef.current
        const isVoiceLikeSegment = speakingWindowMs >= DEMO_MIN_SPEECH_DURATION_MS
        const autoCommitCooldownSatisfied =
          Date.now() - lastAutoCommitAtRef.current >= DEMO_AUTO_COMMIT_MIN_INTERVAL_MS
        const canStartPendingTurn = !pendingTurnStartedAtRef.current
        if (
          sessionRef.current
          && micRef.current
          && (sessionStatusRef.current === "listening" || sessionStatusRef.current === "ready")
          && hadRecentUserSpeech
          && isVoiceLikeSegment
          && autoCommitCooldownSatisfied
          && canStartPendingTurn
        ) {
          const clientTurnId = `ct_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
          const started = startPendingTurn(clientTurnId)
          if (started) {
            lastAutoCommitAtRef.current = Date.now()
            sessionRef.current.commit(undefined, clientTurnId)
          }
        }
      } else if (sessionStatusRef.current === "listening" || sessionStatusRef.current === "ready") {
        const nextPhase = (() => {
          const prev = turnPhaseRef.current
          if (prev === "agent_speaking") return prev
          if (prev === "processing" && Date.now() < thinkingHoldUntilRef.current) return prev
          return (micRef.current ? "listening" : "idle") as TurnPhase
        })()
        setTurnPhaseStable(nextPhase)
      }
      return
    }

    if (event.type === "stt.partial") {
      if (pendingTurnStartedAtRef.current) {
        markPendingTurnResolved("stt.partial")
      }
      const partialText = event.text || ""
      setSttLiveText(partialText)
      const hasPartialText = partialText.trim().length > 0
      if (hasPartialText) {
        lastUserSpeechAtRef.current = Date.now()
        if (!vadSpeechStartedAtRef.current) {
          vadSpeechStartedAtRef.current = Date.now()
        }
      }
      const canRenderUserSpeech =
        micRef.current
        && (
        sessionStatusRef.current === "listening"
        || sessionStatusRef.current === "ready"
        )
      const agentCurrentlySpeaking =
        turnPhaseRef.current === "agent_speaking"
        || sessionStatusRef.current === "speaking"
        || ttsPlayingRef.current
        || ttsStreamActiveRef.current

      if (agentCurrentlySpeaking && isLikelyAssistantEcho(partialText, llmResponseTextRef.current)) {
        return
      }

      if (
        shouldAutoBargeInterrupt
        && agentCurrentlySpeaking
        && !interruptionInFlightRef.current
        && isInterruptWorthyPartial(partialText)
      ) {
        interruptionInFlightRef.current = true
        suppressTtsUntilNextUserFinalRef.current = true
        rejectGeneration(activeGenerationIdRef.current)
        traceReporterRef.current?.trackLocal(
          "ui.auto_interrupt.stt_partial",
          { text: partialText },
          "ui.action",
        )
        void hardStopPlayback()
        sessionRef.current?.interrupt("barge_in")
      }
      if (hasPartialText && canRenderUserSpeech) {
        setTurnPhaseStable("user_speaking")
      }
      return
    }

    if (event.type === "stt.final") {
      if (pendingTurnStartedAtRef.current) {
        markPendingTurnResolved("stt.final")
      }
      const incomingGenerationId = typeof event.generation_id === "string" ? event.generation_id : null
      if (
        incomingGenerationId
        && activeGenerationIdRef.current
        && incomingGenerationId !== activeGenerationIdRef.current
      ) {
        rejectGeneration(activeGenerationIdRef.current)
      }
      if (incomingGenerationId) {
        activeGenerationIdRef.current = incomingGenerationId
      }
      suppressTtsUntilNextUserFinalRef.current = false
      resetAssistantPanels(event.turn_id || null)
      interruptionInFlightRef.current = false
      if (event.text.trim()) {
        lastUserSpeechAtRef.current = Date.now()
      }
      setSttLiveText(event.text || "")
      setSttFinalMeta({
        revision: event.revision ?? null,
        finality: event.finality ?? null,
        deferred: event.deferred ?? null,
        previousText: event.previous_text ?? null,
      })
      setSttProgress(null)
      setRouteName("routing")
      setRouteProvider(null)
      setRouteModel(null)
      setTurnPhaseStable("processing")
      const dedupeKey = `${event.turn_id ?? "-"}:${event.text}`
      if (dedupeKey !== seenUserFinalRef.current && event.text.trim()) {
        seenUserFinalRef.current = dedupeKey
        if (mode !== "minimal") {
          setTranscript((prev) => {
            const next = [...prev, { role: "user", text: event.text }]
            return next.length > 50 ? next.slice(-50) : next
          })
        }
      }
      return
    }

    if (event.type === "route.selected") {
      if (pendingTurnStartedAtRef.current) {
        markPendingTurnResolved("route.selected")
      }
      if (eventGenerationId && rejectedGenerationIdsRef.current.has(eventGenerationId)) {
        return
      }
      if (eventGenerationId) {
        activeGenerationIdRef.current = eventGenerationId
        suppressTtsUntilNextUserFinalRef.current = false
      }
      setRouteName(event.route_name || "selected")
      setRouteProvider(event.provider ?? null)
      setRouteModel(event.model ?? null)
      setTurnPhaseStable("processing")
      return
    }

    if (event.type === "llm.phase") {
      if (pendingTurnStartedAtRef.current) {
        markPendingTurnResolved("llm.phase")
      }
      if (eventGenerationId && rejectedGenerationIdsRef.current.has(eventGenerationId)) {
        return
      }
      if (eventGenerationId) {
        activeGenerationIdRef.current = eventGenerationId
        suppressTtsUntilNextUserFinalRef.current = false
      }
      resetAssistantPanels(event.turn_id || null)
      if (!thinkingPlayerRef.current) {
        thinkingPlayerRef.current = new ThinkingAudioPlayer(thinkingCueUrl)
      }
      if (event.phase === "thinking") {
        setLlmThinkingActive(true)
        setThinkingHoldUntil(Date.now() + 1200)
        setTurnPhaseStable("processing")
        pendingSpeechAfterThinkingRef.current = false
        setPendingSpeechAfterThinking(false)
        clearSpeechWatchdog()
        void thinkingPlayerRef.current.start()
      } else if (event.phase === "generating") {
        setLlmThinkingActive(false)
        pendingSpeechAfterThinkingRef.current = true
        setPendingSpeechAfterThinking(true)
        if (ttsPlayingRef.current || ttsStreamActiveRef.current) {
          setTurnPhaseStable("agent_speaking")
        } else {
          setTurnPhaseStable("processing")
        }
        startSpeechWatchdog()
        thinkingPlayerRef.current.stop()
      } else {
        setLlmThinkingActive(false)
        thinkingPlayerRef.current.stop()
      }
      return
    }

    if (event.type === "llm.reasoning.delta") {
      if (pendingTurnStartedAtRef.current) {
        markPendingTurnResolved("llm.reasoning.delta")
      }
      if (eventGenerationId && rejectedGenerationIdsRef.current.has(eventGenerationId)) {
        return
      }
      if (eventGenerationId) {
        activeGenerationIdRef.current = eventGenerationId
      }
      resetAssistantPanels(event.turn_id || null)
      setLlmThinkingActive(true)
      if (mode !== "minimal") {
        setLlmThinkingText((prev) => prev + (event.delta || ""))
      }
      if (!ttsPlayingRef.current && !ttsStreamActiveRef.current) {
        setTurnPhaseStable("processing")
      }
      return
    }

    if (event.type === "llm.response.delta") {
      if (pendingTurnStartedAtRef.current) {
        markPendingTurnResolved("llm.response.delta")
      }
      if (eventGenerationId && rejectedGenerationIdsRef.current.has(eventGenerationId)) {
        return
      }
      if (eventGenerationId) {
        activeGenerationIdRef.current = eventGenerationId
      }
      resetAssistantPanels(event.turn_id || null)
      setLlmThinkingActive(false)
      const cleanDelta = (event.delta || "")
        .replace(/\*\*/g, "")
        .replace(/\*/g, "")
        .replace(/__/g, "")
        .replace(/`/g, "")
      llmResponseTextRef.current += cleanDelta
      if (mode !== "minimal") {
        setLlmResponseText((prev) => prev + cleanDelta)
      }
      if (ttsPlayingRef.current || ttsStreamActiveRef.current) {
        setTurnPhaseStable("agent_speaking")
      } else {
        setTurnPhaseStable("processing")
      }
      return
    }

    if (event.type === "llm.completed") {
      if (pendingTurnStartedAtRef.current) {
        markPendingTurnResolved("llm.completed")
      }
      if (eventGenerationId && rejectedGenerationIdsRef.current.has(eventGenerationId)) {
        return
      }
      if (eventGenerationId) {
        activeGenerationIdRef.current = eventGenerationId
      }
      resetAssistantPanels(event.turn_id || null)
      setLlmThinkingActive(false)
      if (event.provider || event.model) {
        setRouteProvider(event.provider ?? null)
        setRouteModel(event.model ?? null)
        setRouteName((prev) => (prev === "-" || prev === "routing" ? "selected" : prev))
      }
      if (event.text.trim()) {
        llmResponseTextRef.current = event.text
        if (mode !== "minimal") {
          setLlmResponseText(event.text)
          setTranscript((prev) => {
            const next = [...prev, { role: "assistant", text: event.text }]
            return next.length > 50 ? next.slice(-50) : next
          })
        }
      }
      return
    }

    if (event.type === "llm.error") {
      if (pendingTurnStartedAtRef.current) {
        markPendingTurnResolved("llm.error")
      }
      if (eventGenerationId && rejectedGenerationIdsRef.current.has(eventGenerationId)) {
        return
      }
      if (eventGenerationId) {
        activeGenerationIdRef.current = eventGenerationId
      }
      setLlmThinkingActive(false)
      setSttProgress(null)
      setSttFinalMeta(null)
      pendingSpeechAfterThinkingRef.current = false
      setPendingSpeechAfterThinking(false)
      clearSpeechWatchdog()
      await hardStopPlayback()
      setTurnPhaseStable(sessionRef.current && micRef.current ? "listening" : "idle")
      const errorMessage = event.error?.message || "unknown error"
      setLastError(`llm error: ${errorMessage}`)
      return
    }

    if (event.type === "llm.summary") {
      if (event.provider || event.model) {
        setRouteProvider(event.provider ?? null)
        setRouteModel(event.model ?? null)
        setRouteName((prev) => (prev === "-" || prev === "routing" ? "selected" : prev))
      }
      return
    }

    if (event.type === "tts.chunk") {
      if (pendingTurnStartedAtRef.current) {
        markPendingTurnResolved("tts.chunk")
      }
      if (suppressTtsUntilNextUserFinalRef.current) {
        await hardStopPlayback()
        return
      }
      if (eventGenerationId && rejectedGenerationIdsRef.current.has(eventGenerationId)) {
        return
      }
      if (
        eventGenerationId
        && activeGenerationIdRef.current
        && eventGenerationId !== activeGenerationIdRef.current
      ) {
        if (rejectedGenerationIdsRef.current.has(activeGenerationIdRef.current)) {
          activeGenerationIdRef.current = eventGenerationId
        } else {
          return
        }
      }
      if (!activeGenerationIdRef.current && eventGenerationId) {
        activeGenerationIdRef.current = eventGenerationId
      }
      thinkingPlayerRef.current?.stop()
      setLlmThinkingActive(false)
      ttsStreamActiveRef.current = true
      setTtsStreamActive(true)
      setTurnPhaseStable("agent_speaking")
      return
    }

    if (event.type === "tts.completed") {
      if (pendingTurnStartedAtRef.current) {
        markPendingTurnResolved("tts.completed")
      }
      if (eventGenerationId && rejectedGenerationIdsRef.current.has(eventGenerationId)) {
        return
      }
      setLlmThinkingActive(false)
      ttsStreamActiveRef.current = false
      setTtsStreamActive(false)
      pendingSpeechAfterThinkingRef.current = false
      setPendingSpeechAfterThinking(false)
      clearSpeechWatchdog()
      if (!ttsPlayingRef.current) {
        setTurnPhaseStable(sessionRef.current && micRef.current ? "listening" : "idle")
      } else {
        setTurnPhaseStable("agent_speaking")
      }
      interruptionInFlightRef.current = false
      return
    }

    if (event.type === "conversation.interrupted") {
      markPendingTurnCancelled("conversation.interrupted")
      setSttProgress(null)
      setSttFinalMeta(null)
      suppressTtsUntilNextUserFinalRef.current = true
      rejectGeneration(activeGenerationIdRef.current)
      setLlmThinkingActive(false)
      ttsPlayingRef.current = false
      setTtsPlaybackActive(false)
      ttsStreamActiveRef.current = false
      setTtsStreamActive(false)
      pendingSpeechAfterThinkingRef.current = false
      setPendingSpeechAfterThinking(false)
      clearSpeechWatchdog()
      setTurnPhaseStable("idle")
      await hardStopPlayback()
      interruptionInFlightRef.current = false
      return
    }

    if (event.type === "error") {
      setLlmThinkingActive(false)
      setSessionStatus(`error: ${event.message}`)
    }
  }, [
    appendEvent,
    clearSpeechWatchdog,
    hardStopPlayback,
    markPendingTurnCancelled,
    markPendingTurnResolved,
    mode,
    queuePolicy,
    rejectGeneration,
    resetAssistantPanels,
    startPendingTurn,
    startSpeechWatchdog,
  ])

  const startListening = useCallback(async () => {
    if (!sessionRef.current || isDisconnectingRef.current) return
    if (micRef.current) {
      setIsListening(true)
      return
    }
    const lifecycleToken = ++micLifecycleTokenRef.current
    if (!window.isSecureContext) {
      throw new Error("Microphone requires HTTPS (or localhost).")
    }
    traceReporterRef.current?.trackLocal("ui.start_listening", {
      session_id: sessionRef.current.sessionId,
    })
    const mic = new BrowserMicInput(
      (chunk) => sessionRef.current?.sendAudio(chunk),
      (level) => {
        setMicLevel(Math.min(100, Math.round(level * 140)))
      },
      (bands) => {
        setMicBands(bands)
      },
      (meta) => {
        traceReporterRef.current?.trackLocal("audio.input.chunk", meta, "audio.chunk")
      },
    )
    await mic.start()
    if (lifecycleToken !== micLifecycleTokenRef.current || !sessionRef.current || micRef.current) {
      await mic.stop().catch(() => undefined)
      return
    }
    micRef.current = mic
    setIsListening(true)
    if (sessionStatusRef.current === "listening" || sessionStatusRef.current === "ready") {
      setTurnPhaseStable("listening")
    }
  }, [setTurnPhaseStable])

  const stopListening = useCallback(async () => {
    const lifecycleToken = ++micLifecycleTokenRef.current
    traceReporterRef.current?.trackLocal("ui.stop_listening", {
      session_id: sessionRef.current?.sessionId ?? null,
    })
    const mic = micRef.current
    micRef.current = null

    setIsListening(false)
    setIsMicHoldActive(false)
    setMicLevel(0)
    setMicBands(zeroBands())

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
      Boolean(sessionRef.current)
      && !isDisconnectingRef.current
      && Date.now() - lastUserSpeechAtRef.current <= DEMO_MIC_STOP_COMMIT_GRACE_MS
    if (shouldCommitOnStop) {
      const clientTurnId = `ct_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
      const started = startPendingTurn(clientTurnId)
      if (started) {
        traceReporterRef.current?.trackLocal(
          "ui.pending_turn.start",
          {
            trigger: "mic_stop_commit",
            client_turn_id: clientTurnId,
            stabilization_ms: DEMO_SLOW_STT_STABILIZATION_MS,
            transcript_timeout_ms: DEMO_STT_TRANSCRIPT_TIMEOUT_MS,
          },
          "ui.state",
        )
        sessionRef.current?.commit(undefined, clientTurnId)
      }
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
  }, [setTurnPhaseStable, startPendingTurn])

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
    if (micRef.current || isListening) {
      await stopListening()
      return
    }
    try {
      await startListening()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setLastError(`mic start failed: ${message}`)
    }
  }, [isListening, startListening, stopListening])

  const handleMinimalMicPointerDown = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0 || !sessionRef.current) {
      return
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    minimalMicUserControlledRef.current = true
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
    if (micRef.current || isListening) {
      void stopListening()
    }
  }, [clearMicHoldTimer, isListening, stopListening])

  const disconnect = useCallback(async () => {
    isDisconnectingRef.current = true
    traceReporterRef.current?.trackLocal("ui.disconnect", {
      session_id: sessionRef.current?.sessionId ?? null,
    })
    clearPendingTurn()
    await stopListening()
    await sessionRef.current?.close().catch(() => undefined)
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
    pendingSpeechAfterThinkingRef.current = false
    setPendingSpeechAfterThinking(false)
    clearSpeechWatchdog()
    clearMicHoldTimer()
    clearIdleTransitionTimer()
    micHoldActiveRef.current = false
    suppressMicTapRef.current = false
    minimalMicUserControlledRef.current = false
    setIsMicHoldActive(false)
    persistSessionIdToUrl(null)
    setRequestedSessionId(null)
    setSessionId("-")
    setSessionStatus("disconnected")
    setTurnPhaseStable("idle")
    setSttLiveText("")
    setLlmThinkingText("")
    setLlmResponseText("")
    llmResponseTextRef.current = ""
    setRouteName("-")
    setRouteProvider(null)
    setRouteModel(null)
    activeAssistantTurnIdRef.current = null
    interruptionInFlightRef.current = false
    suppressTtsUntilNextUserFinalRef.current = false
    activeGenerationIdRef.current = null
    rejectedGenerationIdsRef.current.clear()
    await traceReporterRef.current?.flush(false)
    traceReporterRef.current?.stop()
    traceReporterRef.current = null
    isDisconnectingRef.current = false
  }, [
    clearIdleTransitionTimer,
    clearMicHoldTimer,
    clearPendingTurn,
    clearSpeechWatchdog,
    setTurnPhaseStable,
    stopListening,
  ])

  const connect = useCallback(async () => {
    if (sessionRef.current) return
    setLastError("")
    let resumeSessionId = requestedSessionId?.trim() || undefined
    let traceReporter: FrontendTraceReporter | null = null
    try {
      await checkEngineReadiness(baseUrl)
      if (!engineReadiness.ok && engineReadiness.checked) {
        throw new Error(engineReadiness.message)
      }

      const client = new OpenVoiceWebClient({ baseUrl })
      if (resumeSessionId) {
        try {
          const existing = await client.http.getSession(resumeSessionId)
          if (isSessionClosedOrFailed(existing.status)) {
            persistSessionIdToUrl(null)
            setRequestedSessionId(null)
            resumeSessionId = undefined
          }
        } catch {
          persistSessionIdToUrl(null)
          setRequestedSessionId(null)
          resumeSessionId = undefined
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
              pendingSpeechAfterThinkingRef.current = false
              setPendingSpeechAfterThinking(false)
              clearSpeechWatchdog()
              setTurnPhaseStable("agent_speaking")
            } else if (ttsStreamActiveRef.current) {
              setTurnPhaseStable("agent_speaking")
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
        queue_policy: mode === "minimal" ? "send_now" : queuePolicy,
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
        onEvent: (event: ConversationEvent) => {
          void handleEvent(event)
        },
      }

      let session: WebVoiceSession
      let resumedExistingSession = false
      if (resumeSessionId) {
        try {
          session = await client.connectSession({
            ...baseConnectOptions,
            sessionId: resumeSessionId,
          })
          resumedExistingSession = true
        } catch (error) {
          const reason = error instanceof Error ? error.message : String(error)
          persistSessionIdToUrl(null)
          setRequestedSessionId(null)
          traceReporter.trackLocal("ui.session.resume_discarded", {
            resume_session_id: resumeSessionId,
            reason,
          })
          session = await client.connectSession(baseConnectOptions)
          resumedExistingSession = false
        }
      } else {
        session = await client.connectSession(baseConnectOptions)
      }

      traceReporter.setSessionId(session.sessionId)
      traceReporter.trackLocal("ui.connected", {
        runtime_url: baseUrl,
        mode,
        queue_policy: mode === "minimal" ? "send_now" : queuePolicy,
        resumed_existing_session: resumedExistingSession,
      })

      sessionRef.current = session
      persistSessionIdToUrl(session.sessionId)
      setRequestedSessionId(session.sessionId)
      setSessionId(session.sessionId)
      setSessionStatus("connected")
      setTurnPhaseStable(micRef.current ? "listening" : "idle")
      setTranscript([])
      setEvents([])
      setLlmThinkingText("")
      setLlmResponseText("")
      llmResponseTextRef.current = ""
      setRouteName("-")
      setRouteProvider(null)
      setRouteModel(null)
      setTtsBands(zeroBands())
      setMicBands(zeroBands())
      ttsPlayingRef.current = false
      setTtsPlaybackActive(false)
      ttsStreamActiveRef.current = false
      setTtsStreamActive(false)
      pendingSpeechAfterThinkingRef.current = false
      setPendingSpeechAfterThinking(false)
      clearSpeechWatchdog()
      clearPendingTurn()
      activeAssistantTurnIdRef.current = null
      interruptionInFlightRef.current = false
      suppressTtsUntilNextUserFinalRef.current = false
      activeGenerationIdRef.current = null
      rejectedGenerationIdsRef.current.clear()
      seenUserFinalRef.current = ""
      localStorage.setItem("openvoice.runtimeBaseUrl", baseUrl)
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
    } catch (error) {
      setLlmThinkingActive(false)
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
    }
  }, [
    baseUrl,
    checkEngineReadiness,
    clearSpeechWatchdog,
    engineReadiness.checked,
    engineReadiness.message,
    engineReadiness.ok,
    handleEvent,
    mode,
    queuePolicy,
    requestedSessionId,
    runtimeConfig,
    setTurnPhaseStable,
    startListening,
    voiceId,
  ])

  const interrupt = useCallback(() => {
    if (!sessionRef.current) return
    traceReporterRef.current?.trackLocal("ui.interrupt", { reason: "demo" })
    interruptionInFlightRef.current = true
    suppressTtsUntilNextUserFinalRef.current = true
    rejectGeneration(activeGenerationIdRef.current)
    activeGenerationIdRef.current = null
    clearPendingTurn()
    setTurnPhaseStable("idle")
    void hardStopPlayback()
    sessionRef.current.interrupt("demo")
  }, [clearPendingTurn, hardStopPlayback, rejectGeneration, setTurnPhaseStable])

  useEffect(() => {
    if (mode !== "minimal") return
    const run = async () => {
      try {
        if (!sessionRef.current) {
          await connect()
        } else {
          sessionRef.current.updateConfig({ turnQueue: { policy: "send_now" } })
        }
        if (!minimalMicUserControlledRef.current && !micRef.current && sessionRef.current) {
          await startListening()
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        setLastError(message)
        setSessionStatus(`minimal mode failed: ${message}`)
      }
    }
    void run()
  }, [mode, connect, startListening])

  useEffect(() => {
    if (!baseUrl.trim()) return
    localStorage.setItem("openvoice.runtimeBaseUrl", baseUrl.trim())
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
      clearMicHoldTimer()
      clearIdleTransitionTimer()
      clearSpeechWatchdog()
      void disconnect()
    }
  }, [clearIdleTransitionTimer, clearMicHoldTimer, clearSpeechWatchdog, disconnect])

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
    setEvents([])
    setTranscript([])
    setLlmThinkingText("")
    setLlmResponseText("")
    llmResponseTextRef.current = ""
  }, [mode])

  const radialClass = useMemo(() => {
    if (isMicDisconnectedView) return "idle"
    if (turnPhase === "agent_speaking") return "speaking"
    if (turnPhase === "user_speaking") return "speaking"
    if (turnPhase === "processing") return "thinking"
    if (sessionStatus !== "disconnected") return "ready"
    return "idle"
  }, [isMicDisconnectedView, sessionStatus, turnPhase])

  if (mode === "minimal") {
    return (
      <main className="shell shell-minimal" aria-label="Open Voice SDK minimal mode">
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
              className={`pending-text minimal-pending minimal-pending-inline${sttProgressMessage ? " is-visible" : ""}`}
              aria-live="polite"
            >
              {sttProgressMessage || " "}
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
                  {sttLiveText || " "}
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
                    <p className="minimal-detail-value">{turnPhase}</p>
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
        <TabsTrigger active={mode === "minimal"} onClick={() => setMode("minimal")}>Minimal</TabsTrigger>
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
          <Button disabled={!isListening} onClick={() => void stopListening()}>Stop listening</Button>
          <Button disabled={!sessionRef.current} onClick={interrupt}>Interrupt</Button>
        </div>
        {!isListening && sessionRef.current ? (
          <p className="error-text">Mic is not streaming yet. Click `Start listening` or allow microphone permission.</p>
        ) : null}
        {lastError ? <p className="error-text">{lastError}</p> : null}
        {pendingTurnMessage ? <p className="pending-text">{pendingTurnMessage}</p> : null}
        {sttProgressMessage ? <p className="pending-text">{sttProgressMessage}</p> : null}
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
          {sttProgressMessage ? <p className="subcopy">{sttProgressMessage}</p> : null}
          {sttFinalMetaMessage ? <p className="subcopy">{sttFinalMetaMessage}</p> : null}
        </Card>
        <Card className="stage">
          <h2>LLM Thinking</h2>
          <div className="stream-card">{llmThinkingText || " "}</div>
        </Card>
        <Card className="stage">
          <h2>LLM Response</h2>
          <div className="stream-card">{llmResponseText || " "}</div>
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
