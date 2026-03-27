import type { AudioInputAdapter, InputAudioChunk } from "./audio/input"
import type { RuntimeSessionConfig } from "./config"
import { toRuntimeConfigPayload } from "./config"
import type { AudioOutputAdapter } from "./interruption/audio_output"
import { SessionAudioController as SessionAudioControllerImpl } from "./interruption/audio_output"
import type {
  AgentGenerateReplyMessage,
  AgentSayMessage,
  ClientMessage,
  ConversationEvent,
  SessionCloseMessage,
  UserTurnCommitMessage,
} from "./protocol"
import {
  createVoiceSessionState,
  reduceVoiceSessionEvent,
  type VoiceSessionState,
} from "./state"
import { RealtimeConversationSocket } from "./transport/websocket"

export class WebVoiceSession {
  readonly sessionId: string
  readonly socket: RealtimeConversationSocket
  input?: AudioInputAdapter
  state: VoiceSessionState
  private readonly stateListeners = new Set<(state: VoiceSessionState) => void>()
  private readonly audioController?: SessionAudioController

  constructor(
    sessionId: string,
    socket: RealtimeConversationSocket,
    opts: { audioOutput?: AudioOutputAdapter } = {},
  ) {
    this.sessionId = sessionId
    this.socket = socket
    this.state = createVoiceSessionState(sessionId)
    if (opts.audioOutput) {
      this.audioController = new SessionAudioControllerImpl(opts.audioOutput)
    }
    this.socket.onEvent((event) => {
      if (this.audioController) {
        void this.audioController.onEvent(event)
      }
      this.state = reduceVoiceSessionEvent(this.state, event)
      for (const listener of this.stateListeners) listener(this.state)
    })
  }

  onEvent(listener: (event: ConversationEvent) => void): () => void {
    return this.socket.onEvent(listener)
  }

  onStateChange(listener: (state: VoiceSessionState) => void): () => void {
    this.stateListeners.add(listener)
    listener(this.state)
    return () => this.stateListeners.delete(listener)
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

  commit(sequence?: number, clientTurnId?: string): void {
    this.socket.send({
      type: "audio.commit",
      session_id: this.sessionId,
      sequence,
      client_turn_id: clientTurnId,
    })
  }

  commitUserTurn(sequence?: number, clientTurnId?: string): void {
    const message: UserTurnCommitMessage = {
      type: "user_turn.commit",
      session_id: this.sessionId,
      sequence,
      client_turn_id: clientTurnId,
    }
    this.socket.send(message)
  }

  say(
    text: string,
    opts: { interruptCurrent?: boolean; reason?: string } = {},
  ): void {
    if (opts.interruptCurrent) {
      this.interrupt(opts.reason ?? "say")
    }
    const message: AgentSayMessage = {
      type: "agent.say",
      session_id: this.sessionId,
      text,
    }
    this.socket.send(message)
  }

  generateReply(opts: {
    userText: string
    instructions?: string
    allowInterruptions?: boolean
    interruptCurrent?: boolean
    reason?: string
  }): void {
    if (opts.interruptCurrent) {
      this.interrupt(opts.reason ?? "generate_reply")
    }
    const message: AgentGenerateReplyMessage = {
      type: "agent.generate_reply",
      session_id: this.sessionId,
      user_text: opts.userText,
    }
    if (opts.instructions !== undefined) message.instructions = opts.instructions
    if (opts.allowInterruptions !== undefined) {
      message.allow_interruptions = opts.allowInterruptions
    }
    this.socket.send(message)
  }

  interrupt(reason?: string): void {
    if (this.audioController) {
      void this.audioController.interrupt(reason)
    }
    this.socket.send({
      type: "conversation.interrupt",
      session_id: this.sessionId,
      reason,
    })
  }

  updateConfig(config: RuntimeSessionConfig): void {
    const payload = toRuntimeConfigPayload(config)
    if (!payload) return
    this.socket.send({
      type: "config.update",
      session_id: this.sessionId,
      config: payload,
    })
  }

  updateOptions(options: RuntimeSessionConfig): void {
    this.updateConfig(options)
  }

  async close(): Promise<void> {
    if (this.input) await this.input.stop()
    if (this.audioController) {
      await this.audioController.interrupt("close")
      this.audioController.reset()
    }
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
