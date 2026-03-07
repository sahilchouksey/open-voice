import type { ClientMessage, ConversationEvent } from "../protocol"

export interface RealtimeSocketOptions {
  baseUrl: string
  webSocket?: typeof WebSocket
}

export interface ConnectOptions {
  sessionId?: string
}

export type ConversationListener = (event: ConversationEvent) => void

export class RealtimeConversationSocket {
  readonly baseUrl: string
  readonly socketCtor: typeof WebSocket
  socket?: WebSocket
  listeners = new Set<ConversationListener>()

  constructor(opts: RealtimeSocketOptions) {
    this.baseUrl = opts.baseUrl
    this.socketCtor = opts.webSocket ?? WebSocket
  }

  connect(opts: ConnectOptions = {}): Promise<void> {
    return new Promise((resolve, reject) => {
      const url = new URL(this.baseUrl)
      if (url.protocol === "http:") url.protocol = "ws:"
      if (url.protocol === "https:") url.protocol = "wss:"
      url.pathname = "/v1/realtime/conversation"
      if (opts.sessionId) url.searchParams.set("session_id", opts.sessionId)

      const socket = new this.socketCtor(url.toString())
      socket.addEventListener("open", () => {
        this.socket = socket
        resolve()
      })
      socket.addEventListener("message", (event) => {
        if (typeof event.data !== "string") return
        const payload = JSON.parse(event.data) as ConversationEvent
        for (const listener of this.listeners) listener(payload)
      })
      socket.addEventListener("error", () => reject(new Error("Realtime socket failed")), {
        once: true,
      })
      socket.addEventListener("close", () => {
        if (this.socket === socket) this.socket = undefined
      })
    })
  }

  onEvent(listener: ConversationListener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  send(message: ClientMessage): void {
    if (!this.socket || this.socket.readyState !== this.socketCtor.OPEN) {
      throw new Error("Realtime socket is not connected")
    }
    this.socket.send(JSON.stringify(message))
  }

  close(code?: number, reason?: string): void {
    this.socket?.close(code, reason)
    this.socket = undefined
  }
}
