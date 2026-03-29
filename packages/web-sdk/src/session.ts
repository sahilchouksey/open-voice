import type { AudioInputAdapter, InputAudioChunk } from "./audio/input"
import type { RuntimeSessionConfig } from "./config"
import { toRuntimeConfigPayload } from "./config"
import type { AudioOutputAdapter } from "./interruption/audio_output"
import { SessionAudioController as SessionAudioControllerImpl } from "./interruption/audio_output"
import { EventSequencer } from "./orchestration/event_sequencer"
import { GenerationOwner } from "./orchestration/generation_owner"
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
  type VoiceSessionState,
} from "./state"
import {
  createVoiceSessionStore,
  toClockTickAction,
  toOutboundMessageAction,
  toInboundEventAction,
  type VoiceSessionStore,
} from "./store/session_store"
import { RealtimeConversationSocket } from "./transport/websocket"

export interface WebVoiceSessionOptions {
  audioOutput?: AudioOutputAdapter
  debug?: boolean
}

const EVENT_DEBUG_THRESHOLD_MS = 50
const EVENT_DEBUG_PREFIX = "[OV-DEBUG] WebVoiceSession"

export class WebVoiceSession {
  readonly sessionId: string
  readonly socket: RealtimeConversationSocket
  input?: AudioInputAdapter
  state: VoiceSessionState
  readonly store: VoiceSessionStore
  private readonly stateListeners = new Set<(state: VoiceSessionState) => void>()
  private readonly audioController?: SessionAudioControllerImpl
  private readonly eventSequencer: EventSequencer
  private readonly generationOwner = new GenerationOwner()
  private pendingTurnClock: number | null = null
  private readonly debug: boolean

  constructor(
    sessionId: string,
    socket: RealtimeConversationSocket,
    opts: WebVoiceSessionOptions = {},
  ) {
    this.sessionId = sessionId
    this.socket = socket
    this.debug = opts.debug ?? false
    this.state = createVoiceSessionState(sessionId)
    this.store = createVoiceSessionStore(sessionId)
    this.store.subscribe((nextState) => {
      this.state = nextState
      this.ensurePendingTurnClock(nextState)
      for (const listener of this.stateListeners) listener(this.state)
    })
    if (opts.audioOutput) {
      this.audioController = new SessionAudioControllerImpl(opts.audioOutput)
    }
    this.eventSequencer = new EventSequencer({ debug: this.debug })
    this.socket.onEvent((event) => {
      const receiveTime = this.debug ? performance.now() : 0
      this.eventSequencer.push(async () => {
        if (this.debug) {
          const processTime = performance.now() - receiveTime
          if (processTime > EVENT_DEBUG_THRESHOLD_MS) {
            console.warn(`${EVENT_DEBUG_PREFIX}: slow event ${event.type} ${processTime.toFixed(2)}ms`)
          }
        }
        const ownership = this.generationOwner.decide(event)
        if (ownership.acceptForAudio && this.audioController) {
          await this.audioController.onEvent(event)
        }
        if (ownership.acceptForState) {
          this.store.dispatch(toInboundEventAction(event))
        }
      })
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
    this.store.dispatch(toOutboundMessageAction(message))
    this.socket.send(message)
  }

  sendAudio(chunk: InputAudioChunk): void {
    this.send({
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
    this.send({
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
    this.send(message)
  }

  say(
    text: string,
    opts: { interruptCurrent?: boolean; reason?: string } = {},
  ): void {
    if (opts.interruptCurrent) {
      void this.interrupt(opts.reason ?? "say")
    }
    const message: AgentSayMessage = {
      type: "agent.say",
      session_id: this.sessionId,
      text,
    }
    this.send(message)
  }

  generateReply(opts: {
    userText: string
    instructions?: string
    allowInterruptions?: boolean
    interruptCurrent?: boolean
    reason?: string
  }): void {
    const shouldInterruptCurrent = opts.interruptCurrent ?? true
    if (shouldInterruptCurrent) {
      void this.interrupt(opts.reason ?? "generate_reply")
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
    this.send(message)
  }

  async interrupt(reason?: string): Promise<void> {
    this.generationOwner.rejectActiveGeneration()
    this.send({
      type: "conversation.interrupt",
      session_id: this.sessionId,
      reason,
    })
    if (this.audioController) {
      await this.audioController.interrupt(reason)
    }
  }

  updateConfig(config: RuntimeSessionConfig): void {
    const payload = toRuntimeConfigPayload(config)
    if (!payload) return
    this.send({
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
    this.clearPendingTurnClock()
    this.eventSequencer.reset()
    this.generationOwner.reset()
    const msg: SessionCloseMessage = { type: "session.close", session_id: this.sessionId }
    this.send(msg)
    this.socket.close()
  }

  private ensurePendingTurnClock(state: VoiceSessionState): void {
    if (state.pendingTurn.phase === "idle") {
      this.clearPendingTurnClock()
      return
    }
    if (this.pendingTurnClock !== null) {
      return
    }
    this.pendingTurnClock = window.setInterval(() => {
      this.store.dispatch(toClockTickAction())
    }, 300)
  }

  private clearPendingTurnClock(): void {
    if (this.pendingTurnClock === null) {
      return
    }
    window.clearInterval(this.pendingTurnClock)
    this.pendingTurnClock = null
  }
}

function toBase64(data: ArrayBuffer): string {
  const bytes = new Uint8Array(data)
  let text = ""
  for (const byte of bytes) text += String.fromCharCode(byte)
  return btoa(text)
}
