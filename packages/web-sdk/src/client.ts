import type { AudioInputAdapter } from "./audio/input"
import type { AudioOutputAdapter } from "./interruption/audio_output"
import type { RuntimeSessionConfig } from "./config"
import { toRuntimeConfigPayload } from "./config"
import type {
  EngineCatalogResponse,
  EngineSelection,
  SessionStartMessage,
} from "./protocol"
import { WebVoiceSession } from "./session"
import { RuntimeHttpClient, type RuntimeHttpClientOptions, type SessionCreateRequest } from "./transport/http"
import { RealtimeConversationSocket, type ConversationListener } from "./transport/websocket"
import type { FrontendTraceReporter } from "./diagnostics/trace"

export interface OpenVoiceWebClientOptions extends RuntimeHttpClientOptions {
  webSocket?: typeof WebSocket
}

export interface ConnectSessionOptions {
  sessionId?: string
  engineSelection?: EngineSelection
  metadata?: Record<string, unknown>
  runtimeConfig?: RuntimeSessionConfig
  input?: AudioInputAdapter
  audioOutput?: AudioOutputAdapter
  onEvent?: ConversationListener
  autoStart?: boolean
  verifyEngines?: boolean
  traceReporter?: FrontendTraceReporter
}

export class OpenVoiceWebClient {
  readonly http: RuntimeHttpClient
  readonly webSocket: typeof WebSocket | undefined

  constructor(opts: OpenVoiceWebClientOptions) {
    this.http = new RuntimeHttpClient(opts)
    this.webSocket = opts.webSocket
  }

  async connectSession(opts: ConnectSessionOptions = {}): Promise<WebVoiceSession> {
    if (opts.verifyEngines !== false) {
      const catalog = await this.http.listEngines()
      this.ensureRealtimeAudioEnginesReady(catalog)
    }

    const runtimeConfig = toRuntimeConfigPayload(opts.runtimeConfig)
    const state = opts.sessionId
      ? await this.http.getSession(opts.sessionId)
      : await this.http.createSession({
          engine_selection: opts.engineSelection,
          metadata: opts.metadata,
          runtime_config: runtimeConfig,
        } satisfies SessionCreateRequest)

    const socket = new RealtimeConversationSocket({
      baseUrl: this.http.baseUrl,
      webSocket: this.webSocket,
      onInboundEvent: (event) => {
        opts.traceReporter?.trackInboundEvent(event)
      },
      onOutboundMessage: (message) => {
        opts.traceReporter?.trackOutboundMessage(message.type, message)
      },
    })

    await socket.connect({ sessionId: state.session_id })

    const session = new WebVoiceSession(state.session_id, socket, {
      audioOutput: opts.audioOutput,
    })
    if (opts.onEvent) session.onEvent(opts.onEvent)

    if (opts.autoStart !== false) {
      const start: SessionStartMessage = {
        type: "session.start",
        session_id: state.session_id,
        engine_selection: opts.engineSelection,
        metadata: opts.metadata,
        config: runtimeConfig,
      }
      session.send(start)
    }

    if (opts.input) await session.attachInput(opts.input)
    return session
  }

  ensureRealtimeAudioEnginesReady(catalog: EngineCatalogResponse): void {
    const missing: string[] = []

    const defaultStt = catalog.stt.find((entry) => entry.default) ?? catalog.stt[0]
    if (!defaultStt || !defaultStt.available) {
      const detail = defaultStt
        ? `stt:${defaultStt.id} (${defaultStt.status || "unavailable"})`
        : "stt:none"
      missing.push(detail)
    }

    const vadEntries = catalog.vad ?? []
    if (vadEntries.length > 0) {
      const defaultVad = vadEntries.find((entry) => entry.default) ?? vadEntries[0]
      if (!defaultVad || !defaultVad.available) {
        const detail = defaultVad
          ? `vad:${defaultVad.id} (${defaultVad.status || "unavailable"})`
          : "vad:none"
        missing.push(detail)
      }
    }

    if (missing.length === 0) {
      return
    }

    throw new Error(
      `Runtime realtime audio is unavailable (${missing.join(", ")}). Install runtime audio dependencies (for example: moonshine-voice and silero-vad) and restart backend.`,
    )
  }
}
