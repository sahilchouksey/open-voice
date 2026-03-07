import type { AudioInputAdapter } from "./audio/input"
import type {
  EngineSelection,
  SessionStartMessage,
} from "./protocol"
import { WebVoiceSession } from "./session"
import { RuntimeHttpClient, type RuntimeHttpClientOptions, type SessionCreateRequest } from "./transport/http"
import { RealtimeConversationSocket, type ConversationListener } from "./transport/websocket"

export interface OpenVoiceWebClientOptions extends RuntimeHttpClientOptions {
  webSocket?: typeof WebSocket
}

export interface ConnectSessionOptions {
  sessionId?: string
  engineSelection?: EngineSelection
  metadata?: Record<string, unknown>
  input?: AudioInputAdapter
  onEvent?: ConversationListener
}

export class OpenVoiceWebClient {
  readonly http: RuntimeHttpClient
  readonly webSocket: typeof WebSocket | undefined

  constructor(opts: OpenVoiceWebClientOptions) {
    this.http = new RuntimeHttpClient(opts)
    this.webSocket = opts.webSocket
  }

  async connectSession(opts: ConnectSessionOptions = {}): Promise<WebVoiceSession> {
    const state = opts.sessionId
      ? await this.http.getSession(opts.sessionId)
      : await this.http.createSession({
          engine_selection: opts.engineSelection,
          metadata: opts.metadata,
        } satisfies SessionCreateRequest)

    const socket = new RealtimeConversationSocket({
      baseUrl: this.http.baseUrl,
      webSocket: this.webSocket,
    })

    await socket.connect({ sessionId: state.session_id })

    const session = new WebVoiceSession(state.session_id, socket)
    if (opts.onEvent) session.onEvent(opts.onEvent)

    const start: SessionStartMessage = {
      type: "session.start",
      session_id: state.session_id,
      engine_selection: opts.engineSelection,
      metadata: opts.metadata,
    }
    session.send(start)

    if (opts.input) await session.attachInput(opts.input)
    return session
  }
}
