import type { AudioInputAdapter, InputAudioChunk } from "./audio/input"
import type {
  ClientMessage,
  ConversationEvent,
  SessionCloseMessage,
} from "./protocol"
import { RealtimeConversationSocket } from "./transport/websocket"

export class WebVoiceSession {
  readonly sessionId: string
  readonly socket: RealtimeConversationSocket
  input?: AudioInputAdapter

  constructor(sessionId: string, socket: RealtimeConversationSocket) {
    this.sessionId = sessionId
    this.socket = socket
  }

  onEvent(listener: (event: ConversationEvent) => void): () => void {
    return this.socket.onEvent(listener)
  }

  async attachInput(input: AudioInputAdapter): Promise<void> {
    this.input = input
    await input.start(async (chunk) => this.sendAudio(chunk))
  }

  send(message: ClientMessage): void {
    this.socket.send(message)
  }

  sendAudio(chunk: InputAudioChunk): void {
    this.socket.send({
      type: "audio.append",
      session_id: this.sessionId,
      chunk: {
        chunk_id: `${this.sessionId}:${chunk.sequence}`,
        sequence: chunk.sequence,
        encoding: chunk.encoding,
        sample_rate_hz: chunk.sampleRateHz,
        channels: chunk.channels,
        duration_ms: chunk.durationMs,
        transport: "inline-base64",
        data_base64: toBase64(chunk.data),
      },
    })
  }

  commit(sequence?: number): void {
    this.socket.send({
      type: "audio.commit",
      session_id: this.sessionId,
      sequence,
    })
  }

  interrupt(reason?: string): void {
    this.socket.send({
      type: "conversation.interrupt",
      session_id: this.sessionId,
      reason,
    })
  }

  async close(): Promise<void> {
    if (this.input) await this.input.stop()
    const msg: SessionCloseMessage = { type: "session.close", session_id: this.sessionId }
    this.socket.send(msg)
    this.socket.close()
  }
}

function toBase64(data: ArrayBuffer): string {
  const bytes = new Uint8Array(data)
  let text = ""
  for (const byte of bytes) text += String.fromCharCode(byte)
  return btoa(text)
}
