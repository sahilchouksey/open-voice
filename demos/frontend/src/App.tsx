import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  FrontendTraceReporter,
  OpenVoiceWebClient,
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
}

const AUDIO_BAND_COUNT = 9
const AUDIO_LO_PASS = 100
const AUDIO_HI_PASS = 200

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

class BrowserMicInput {
  private sequence = 0
  private ctx: AudioContext | null = null
  private processor: ScriptProcessorNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private stream: MediaStream | null = null
  private analyser: AnalyserNode | null = null
  private bandTimer: number | null = null

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
    this.processor = this.ctx.createScriptProcessor(4096, 1, 1)
    this.analyser = this.ctx.createAnalyser()
    this.analyser.fftSize = 2048
    this.analyser.smoothingTimeConstant = 0

    this.processor.onaudioprocess = async (event) => {
      const channel = event.inputBuffer.getChannelData(0)
      let peak = 0
      const pcm = new Int16Array(channel.length)
      for (let i = 0; i < channel.length; i += 1) {
        const sample = Math.max(-1, Math.min(1, channel[i]))
        peak = Math.max(peak, Math.abs(sample))
        pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
      }
      this.onLevel(peak)
      await this.sendChunk({
        data: pcm.buffer,
        sequence: this.sequence,
        encoding: "pcm_s16le",
        sampleRateHz: this.ctx?.sampleRate ?? 24000,
        channels: 1,
        durationMs: (pcm.length / (this.ctx?.sampleRate ?? 24000)) * 1000,
      })
      this.onChunkMeta?.({
        sequence: this.sequence,
        sampleRateHz: this.ctx?.sampleRate ?? 24000,
        channels: 1,
        durationMs: (pcm.length / (this.ctx?.sampleRate ?? 24000)) * 1000,
        bytes: pcm.byteLength,
      })
      this.sequence += 1
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
    if (this.bandTimer !== null) {
      window.clearInterval(this.bandTimer)
      this.bandTimer = null
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

export function App() {
  const [mode, setMode] = useState<Mode>("detailed")
  const [baseUrl, setBaseUrl] = useState(resolveInitialRuntimeBaseUrl)
  const [voiceId, setVoiceId] = useState("af_heart")
  const [queuePolicy, setQueuePolicy] = useState<"enqueue" | "send_now" | "inject_next_loop">("send_now")

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
  const [minimalSettingsOpen, setMinimalSettingsOpen] = useState(false)
  const [minimalCaptionsEnabled, setMinimalCaptionsEnabled] = useState(false)
  const [minimalDetailEnabled, setMinimalDetailEnabled] = useState(false)
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
  const speechWatchdogTimerRef = useRef<number | null>(null)
  const sessionStatusRef = useRef(sessionStatus)
  const turnPhaseRef = useRef<TurnPhase>(turnPhase)
  const traceReporterRef = useRef<FrontendTraceReporter | null>(null)
  const minimalSettingsRef = useRef<HTMLDivElement | null>(null)

  const canConnect = !sessionRef.current
  const routeModelLabel = routeProvider || routeModel
    ? `${routeProvider ?? "-"}/${routeModel ?? "-"}`
    : "-"
  const sessionStatusLabel = useMemo(() => {
    if (sessionStatus.startsWith("error:") || sessionStatus.startsWith("connect failed:")) {
      return sessionStatus
    }
    if (turnPhase === "agent_speaking") return "speaking"
    if (turnPhase === "processing") return "thinking"
    if (turnPhase === "user_speaking") return "listening"
    return sessionStatus
  }, [sessionStatus, turnPhase])

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
      message: `Realtime engines unavailable (${reasonParts.join(", ")}). Install backend deps: moonshine-voice and silero-vad.`,
    })
  }, [])

  useEffect(() => {
    sessionStatusRef.current = sessionStatus
  }, [sessionStatus])

  useEffect(() => {
    turnPhaseRef.current = turnPhase
  }, [turnPhase])

  const resetAssistantPanels = useCallback((turnId?: string | null) => {
    const normalizedTurnId = turnId ?? null
    if (normalizedTurnId && activeAssistantTurnIdRef.current === normalizedTurnId) {
      return
    }
    activeAssistantTurnIdRef.current = normalizedTurnId
    setLlmThinkingText("")
    setLlmResponseText("")
  }, [])

  const clearSpeechWatchdog = useCallback(() => {
    if (speechWatchdogTimerRef.current !== null) {
      window.clearTimeout(speechWatchdogTimerRef.current)
      speechWatchdogTimerRef.current = null
    }
  }, [])

  const startSpeechWatchdog = useCallback(() => {
    clearSpeechWatchdog()
    speechWatchdogTimerRef.current = window.setTimeout(() => {
      if (pendingSpeechAfterThinkingRef.current && !ttsPlayingRef.current) {
        pendingSpeechAfterThinkingRef.current = false
        if (sessionRef.current) {
          setTurnPhase("listening")
        } else {
          setTurnPhase("idle")
        }
      }
    }, 1200)
  }, [clearSpeechWatchdog])

  const hardStopPlayback = useCallback(async () => {
    traceReporterRef.current?.trackLocal("audio.output.flush", { reason: "hard_stop" }, "audio")
    await sdkPlayerRef.current?.flush().catch(() => undefined)
    thinkingPlayerRef.current?.stop()
  }, [])

  const activeGridBands = useMemo(() => {
    return turnPhase === "agent_speaking" ? ttsBands : micBands
  }, [micBands, ttsBands, turnPhase])

  const runtimeConfig = useMemo<RuntimeSessionConfig>(() => {
    const effectivePolicy = mode === "minimal" ? "send_now" : queuePolicy
    return {
      turnQueue: { policy: effectivePolicy },
      llm: {
        systemPrompt: OPEN_VOICE_SYSTEM_PROMPT,
        enable_fast_ack: false,
        opencode_mode: OPENCODE_MODE,
        tools: VOICE_LLM_TOOLS,
      },
      turnDetection: {
        mode: "hybrid",
        transcript_timeout_ms: 250,
        min_silence_duration_ms: 500,
        min_speech_duration_ms: 80,
      },
    }
  }, [mode, queuePolicy])

  const appendEvent = useCallback((event: ConversationEvent) => {
    setEvents((prev) => [...prev.slice(-399), JSON.stringify(event, null, 2)])
  }, [])

  const handleEvent = useCallback(async (event: ConversationEvent) => {
    const shouldAutoBargeInterrupt = mode === "minimal" || queuePolicy === "send_now"
    appendEvent(event)

    if (event.type === "session.ready") {
      setSessionStatus("ready")
      setTurnPhase("listening")
      pendingSpeechAfterThinkingRef.current = false
      clearSpeechWatchdog()
      return
    }

    if (event.type === "session.status") {
      traceReporterRef.current?.trackLocal(
        "ui.session.status",
        {
          status: event.status,
          reason: event.reason ?? null,
        },
        "ui.state",
      )
      setSessionStatus(event.status)
      if (event.status === "thinking") setTurnPhase("processing")
      else if (event.status === "speaking") {
        setTurnPhase("agent_speaking")
        pendingSpeechAfterThinkingRef.current = false
        clearSpeechWatchdog()
      }
      else if (event.status === "listening" || event.status === "ready") {
        setTurnPhase((prev) => {
          if (
            pendingSpeechAfterThinkingRef.current
            || ttsPlayingRef.current
            || ttsStreamActiveRef.current
          ) {
            return "agent_speaking"
          }
          if (prev === "agent_speaking" || prev === "processing") return prev
          return "listening"
        })
      } else if (
        event.status === "interrupted" ||
        event.status === "closed" ||
        event.status === "failed"
      ) {
        setTurnPhase("idle")
        pendingSpeechAfterThinkingRef.current = false
        clearSpeechWatchdog()
      }
      return
    }

    if (event.type === "vad.state") {
      if (event.speaking) {
        const agentCurrentlySpeaking =
          turnPhaseRef.current === "agent_speaking"
          || sessionStatusRef.current === "speaking"
          || ttsPlayingRef.current
          || ttsStreamActiveRef.current
        if (!agentCurrentlySpeaking) {
          setTurnPhase("user_speaking")
        }
      } else if (sessionStatusRef.current === "listening" || sessionStatusRef.current === "ready") {
        setTurnPhase((prev) => {
          if (prev === "agent_speaking" || prev === "processing") return prev
          return "listening"
        })
      }
      return
    }

    if (event.type === "stt.partial") {
      const partialText = event.text || ""
      setSttLiveText(partialText)
      const agentCurrentlySpeaking =
        turnPhaseRef.current === "agent_speaking"
        || sessionStatusRef.current === "speaking"
        || ttsPlayingRef.current
        || ttsStreamActiveRef.current

      if (agentCurrentlySpeaking && isLikelyAssistantEcho(partialText, llmResponseText)) {
        return
      }

      if (
        shouldAutoBargeInterrupt
        && agentCurrentlySpeaking
        && !interruptionInFlightRef.current
        && partialText.trim().length >= 2
      ) {
        interruptionInFlightRef.current = true
        traceReporterRef.current?.trackLocal(
          "ui.auto_interrupt.stt_partial",
          { text: partialText },
          "ui.action",
        )
        void hardStopPlayback()
        sessionRef.current?.interrupt("auto_stt_partial")
      }
      setTurnPhase("user_speaking")
      return
    }

    if (event.type === "stt.final") {
      resetAssistantPanels(event.turn_id || null)
      interruptionInFlightRef.current = false
      setSttLiveText(event.text || "")
      setRouteName("routing")
      setRouteProvider(null)
      setRouteModel(null)
      setTurnPhase("processing")
      const dedupeKey = `${event.turn_id ?? "-"}:${event.text}`
      if (dedupeKey !== seenUserFinalRef.current && event.text.trim()) {
        seenUserFinalRef.current = dedupeKey
        setTranscript((prev) => [...prev, { role: "user", text: event.text }])
      }
      return
    }

    if (event.type === "route.selected") {
      setRouteName(event.route_name || "selected")
      setRouteProvider(event.provider ?? null)
      setRouteModel(event.model ?? null)
      setTurnPhase("processing")
      return
    }

    if (event.type === "llm.phase") {
      resetAssistantPanels(event.turn_id || null)
      if (!thinkingPlayerRef.current) {
        thinkingPlayerRef.current = new ThinkingAudioPlayer(thinkingCueUrl)
      }
      if (event.phase === "thinking") {
        setTurnPhase("processing")
        pendingSpeechAfterThinkingRef.current = false
        clearSpeechWatchdog()
        void thinkingPlayerRef.current.start()
      } else if (event.phase === "generating") {
        pendingSpeechAfterThinkingRef.current = true
        setTurnPhase("agent_speaking")
        startSpeechWatchdog()
        thinkingPlayerRef.current.stop()
      } else {
        thinkingPlayerRef.current.stop()
      }
      return
    }

    if (event.type === "llm.reasoning.delta") {
      resetAssistantPanels(event.turn_id || null)
      setLlmThinkingText((prev) => prev + (event.delta || ""))
      setTurnPhase("processing")
      return
    }

    if (event.type === "llm.response.delta") {
      resetAssistantPanels(event.turn_id || null)
      const cleanDelta = (event.delta || "")
        .replace(/\*\*/g, "")
        .replace(/\*/g, "")
        .replace(/__/g, "")
        .replace(/`/g, "")
      setLlmResponseText((prev) => prev + cleanDelta)
      setTurnPhase("processing")
      return
    }

    if (event.type === "llm.completed") {
      resetAssistantPanels(event.turn_id || null)
      if (event.provider || event.model) {
        setRouteProvider(event.provider ?? null)
        setRouteModel(event.model ?? null)
        setRouteName((prev) => (prev === "-" || prev === "routing" ? "selected" : prev))
      }
      if (event.text.trim()) {
        setLlmResponseText(event.text)
        setTranscript((prev) => [...prev, { role: "assistant", text: event.text }])
      }
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
      thinkingPlayerRef.current?.stop()
      ttsStreamActiveRef.current = true
      setTurnPhase("agent_speaking")
      return
    }

    if (event.type === "tts.completed") {
      ttsStreamActiveRef.current = false
      pendingSpeechAfterThinkingRef.current = false
      clearSpeechWatchdog()
      if (!ttsPlayingRef.current) {
        setTurnPhase(sessionRef.current ? "listening" : "idle")
      } else {
        setTurnPhase("agent_speaking")
      }
      interruptionInFlightRef.current = false
      return
    }

    if (event.type === "conversation.interrupted") {
      ttsPlayingRef.current = false
      ttsStreamActiveRef.current = false
      pendingSpeechAfterThinkingRef.current = false
      clearSpeechWatchdog()
      setTurnPhase("idle")
      await hardStopPlayback()
      interruptionInFlightRef.current = false
      return
    }

    if (event.type === "error") {
      setSessionStatus(`error: ${event.message}`)
    }
  }, [
    appendEvent,
    clearSpeechWatchdog,
    hardStopPlayback,
    mode,
    queuePolicy,
    resetAssistantPanels,
    startSpeechWatchdog,
  ])

  const startListening = useCallback(async () => {
    if (!sessionRef.current || micRef.current) return
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
    micRef.current = mic
    setIsListening(true)
  }, [])

  const stopListening = useCallback(async () => {
    if (!micRef.current) return
    traceReporterRef.current?.trackLocal("ui.stop_listening", {
      session_id: sessionRef.current?.sessionId ?? null,
    })
    await micRef.current.stop()
    micRef.current = null
    setIsListening(false)
    setMicLevel(0)
    setMicBands(zeroBands())
    if (sessionStatusRef.current !== "speaking" && sessionStatusRef.current !== "thinking") {
      setTurnPhase("idle")
    }
  }, [])

  const disconnect = useCallback(async () => {
    traceReporterRef.current?.trackLocal("ui.disconnect", {
      session_id: sessionRef.current?.sessionId ?? null,
    })
    await stopListening()
    await sessionRef.current?.close().catch(() => undefined)
    sessionRef.current = null
    await sdkPlayerRef.current?.flush().catch(() => undefined)
    sdkPlayerRef.current = null
    setTtsBands(zeroBands())
    setMicBands(zeroBands())
    thinkingPlayerRef.current?.stop()
    ttsPlayingRef.current = false
    ttsStreamActiveRef.current = false
    pendingSpeechAfterThinkingRef.current = false
    clearSpeechWatchdog()
    setSessionId("-")
    setSessionStatus("disconnected")
    setTurnPhase("idle")
    setSttLiveText("")
    setLlmThinkingText("")
    setLlmResponseText("")
    setRouteName("-")
    setRouteProvider(null)
    setRouteModel(null)
    activeAssistantTurnIdRef.current = null
    interruptionInFlightRef.current = false
    await traceReporterRef.current?.flush(false)
    traceReporterRef.current?.stop()
    traceReporterRef.current = null
  }, [stopListening])

  const connect = useCallback(async () => {
    if (sessionRef.current) return
    setLastError("")
    let traceReporter: FrontendTraceReporter | null = null
    try {
      await checkEngineReadiness(baseUrl)
      if (!engineReadiness.ok && engineReadiness.checked) {
        throw new Error(engineReadiness.message)
      }

      const client = new OpenVoiceWebClient({ baseUrl })
      if (!sdkPlayerRef.current) {
        sdkPlayerRef.current = new VisualizedPcmPlayer(
          (bands) => {
            setTtsBands(bands)
          },
          (active) => {
            ttsPlayingRef.current = active
            if (active) {
              pendingSpeechAfterThinkingRef.current = false
              clearSpeechWatchdog()
              setTurnPhase("agent_speaking")
            } else if (ttsStreamActiveRef.current) {
              setTurnPhase("agent_speaking")
            } else if (sessionRef.current) {
              setTurnPhase("listening")
            } else {
              setTurnPhase("idle")
            }
          },
        )
      }

      traceReporter =
        traceReporterRef.current ??
        new FrontendTraceReporter({
          runtimeBaseUrl: baseUrl,
          enabled: true,
        })
      traceReporter.start()
      traceReporter.trackLocal("ui.connect_start", {
        runtime_url: baseUrl,
        mode,
        queue_policy: mode === "minimal" ? "send_now" : queuePolicy,
      })
      traceReporterRef.current = traceReporter

      const session = await client.connectSession({
        metadata: { source: "react-demo", voice_id: voiceId, language: "en-US" },
        runtimeConfig,
        audioOutput: sdkPlayerRef.current,
        autoStart: false,
        verifyEngines: false,
        traceReporter,
        onEvent: (event) => {
          void handleEvent(event)
        },
      })

      traceReporter.setSessionId(session.sessionId)
      traceReporter.trackLocal("ui.connected", {
        runtime_url: baseUrl,
        mode,
        queue_policy: mode === "minimal" ? "send_now" : queuePolicy,
      })

      sessionRef.current = session
      setSessionId(session.sessionId)
      setSessionStatus("connected")
      setTurnPhase("listening")
      setTranscript([])
      setEvents([])
      setLlmThinkingText("")
      setLlmResponseText("")
      setRouteName("-")
      setRouteProvider(null)
      setRouteModel(null)
      setTtsBands(zeroBands())
      setMicBands(zeroBands())
      ttsPlayingRef.current = false
      ttsStreamActiveRef.current = false
      pendingSpeechAfterThinkingRef.current = false
      clearSpeechWatchdog()
      activeAssistantTurnIdRef.current = null
      interruptionInFlightRef.current = false
      seenUserFinalRef.current = ""
      localStorage.setItem("openvoice.runtimeBaseUrl", baseUrl)
      session.send({
        type: "session.start",
        session_id: session.sessionId,
        metadata: { source: "react-demo", voice_id: voiceId, language: "en-US" },
        config: {
          llm: {
            enable_fast_ack: false,
            opencode_mode: OPENCODE_MODE,
            system_prompt: OPEN_VOICE_SYSTEM_PROMPT,
            tools: VOICE_LLM_TOOLS,
          },
          turn_detection: {
            mode: "hybrid",
            transcript_timeout_ms: 1000,
            min_silence_duration_ms: 2000,
            min_speech_duration_ms: 80,
          },
          turn_queue: {
            policy: mode === "minimal" ? "send_now" : queuePolicy,
          },
        },
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
    runtimeConfig,
    startListening,
    voiceId,
  ])

  const interrupt = useCallback(() => {
    if (!sessionRef.current) return
    traceReporterRef.current?.trackLocal("ui.interrupt", { reason: "demo" })
    interruptionInFlightRef.current = true
    setTurnPhase("idle")
    void hardStopPlayback()
    sessionRef.current.interrupt("demo")
  }, [hardStopPlayback])

  useEffect(() => {
    if (mode !== "minimal") return
    const run = async () => {
      try {
        if (!sessionRef.current) {
          await connect()
        } else {
          sessionRef.current.updateConfig({ turnQueue: { policy: "send_now" } })
        }
        if (!micRef.current && sessionRef.current) {
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
          message: `Realtime engines unavailable (${reasonParts.join(", ")}). Install backend deps: moonshine-voice and silero-vad.`,
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
      clearSpeechWatchdog()
      void disconnect()
    }
  }, [clearSpeechWatchdog, disconnect])

  const radialClass = useMemo(() => {
    if (turnPhase === "agent_speaking") return "speaking"
    if (turnPhase === "user_speaking") return "speaking"
    if (turnPhase === "processing") return "thinking"
    if (sessionStatus !== "disconnected") return "ready"
    return "idle"
  }, [sessionStatus, turnPhase])

  if (mode === "minimal") {
    return (
      <main className="shell shell-minimal" aria-label="Open Voice SDK minimal mode">
        <section className="minimal-stage" aria-label="Minimal voice experience">
          <article className="minimal-notebook">
            <div className="minimal-frame" aria-hidden="true">
              <span className="frame-line frame-line-top" />
              <span className="frame-line frame-line-right" />
              <span className="frame-line frame-line-bottom" />
              <span className="frame-line frame-line-left" />
              <span className="frame-corner corner-tl" />
              <span className="frame-corner corner-tr" />
              <span className="frame-corner corner-bl" />
              <span className="frame-corner corner-br" />
            </div>

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
              >
                Settings
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
