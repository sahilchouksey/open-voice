import type { AudioInputAdapter } from "../audio/input"
import type { RuntimeSessionConfig } from "../config"
import type { AudioOutputAdapter } from "../interruption/audio_output"
import type { ConversationEvent } from "../protocol"
import type { VoiceSessionState } from "../state"
import type { VoiceSessionStore } from "../store/session_store"
import { OpenVoiceWebClient, type ConnectSessionOptions } from "../client"
import type {
  VoiceAgentConnectOptions,
  VoiceAgentConnectionState,
  VoiceAgentEventContext,
  VoiceAgentSignal,
  VoiceAgentSignalListener,
} from "./types"
import type { WebVoiceSession } from "../session"

function noop(): void {
  // no-op helper
}

export class VoiceAgent {
  private session: WebVoiceSession | null = null
  private connectionState: VoiceAgentConnectionState = "disconnected"
  private previousState: VoiceSessionState | null = null
  private readonly signalListeners = new Set<VoiceAgentSignalListener>()
  private sessionStateUnsubscribe: (() => void) | null = null
  private sessionEventUnsubscribe: (() => void) | null = null
  private sessionStoreUnsubscribe: (() => void) | null = null

  constructor(private readonly client: OpenVoiceWebClient) {}

  async connect(options: VoiceAgentConnectOptions = {}): Promise<WebVoiceSession> {
    this.setConnectionState("connecting")
    const connectOptions: ConnectSessionOptions = {
      sessionId: options.sessionId,
      engineSelection: options.engineSelection,
      metadata: options.metadata,
      runtimeConfig: options.runtimeConfig,
      input: options.input,
      audioOutput: options.audioOutput,
      onEvent: options.onEvent,
      traceReporter: options.traceReporter,
      autoStart: options.autoStart,
      verifyEngines: options.verifyEngines,
    }
    const session = await this.client.connectSession(connectOptions)
    this.detachSessionSubscriptions()
    this.session = session
    this.previousState = session.state
    this.sessionStateUnsubscribe = session.onStateChange((state) => {
      this.previousState = state
    })
    this.sessionEventUnsubscribe = session.onEvent((event) => {
      this.emitSignalsFromEvent({
        previousState: this.previousState,
        state: session.state,
        event,
      })
    })
    this.sessionStoreUnsubscribe = session.store.subscribe((state) => {
      const previous = this.previousState
      this.emitSignalsFromState(previous, state)
      this.previousState = state
    })
    this.setConnectionState("connected")
    return session
  }

  async disconnect(): Promise<void> {
    if (!this.session) {
      return
    }
    const current = this.session
    this.session = null
    this.detachSessionSubscriptions()
    try {
      await current.close()
      this.setConnectionState("closed")
    } catch (error) {
      this.emitSignal({
        type: "sdk.error",
        message: error instanceof Error ? error.message : String(error),
        code: null,
        timestampMs: Date.now(),
        sessionId: current.sessionId,
      })
      this.setConnectionState("failed")
      throw error
    }
  }

  attachInput(input: AudioInputAdapter): Promise<void> {
    const session = this.requireSession()
    return session.attachInput(input)
  }

  async interrupt(reason?: string): Promise<void> {
    await this.requireSession().interrupt(reason)
    this.emitSignal({
      type: "interrupt.lifecycle",
      stage: "requested",
      reason: reason ?? null,
      timestampMs: Date.now(),
      sessionId: this.session?.sessionId ?? null,
    })
  }

  updateConfig(config: RuntimeSessionConfig): void {
    this.requireSession().updateConfig(config)
  }

  say(text: string, opts: { interruptCurrent?: boolean; reason?: string } = {}): void {
    this.requireSession().say(text, opts)
  }

  generateReply(opts: {
    userText: string
    instructions?: string
    allowInterruptions?: boolean
    interruptCurrent?: boolean
    reason?: string
  }): void {
    this.requireSession().generateReply(opts)
  }

  commit(sequence?: number, clientTurnId?: string): void {
    this.requireSession().commit(sequence, clientTurnId)
  }

  commitUserTurn(sequence?: number, clientTurnId?: string): void {
    this.requireSession().commitUserTurn(sequence, clientTurnId)
  }

  sendAudio(chunk: Parameters<WebVoiceSession["sendAudio"]>[0]): void {
    this.requireSession().sendAudio(chunk)
  }

  onEvent(listener: (event: ConversationEvent) => void): () => void {
    return this.requireSession().onEvent(listener)
  }

  onStateChange(listener: (state: VoiceSessionState) => void): () => void {
    return this.requireSession().onStateChange(listener)
  }

  onSignal(listener: VoiceAgentSignalListener): () => void {
    this.signalListeners.add(listener)
    return () => {
      this.signalListeners.delete(listener)
    }
  }

  getStore(): VoiceSessionStore {
    return this.requireSession().store
  }

  getState(): VoiceSessionState {
    return this.requireSession().state
  }

  getSession(): WebVoiceSession | null {
    return this.session
  }

  private requireSession(): WebVoiceSession {
    if (!this.session) {
      throw new Error("VoiceAgent is not connected")
    }
    return this.session
  }

  private setConnectionState(nextState: VoiceAgentConnectionState): void {
    const previousState = this.connectionState
    if (previousState === nextState) {
      return
    }
    this.connectionState = nextState
    this.emitSignal({
      type: "connection.state",
      state: nextState,
      previousState,
      timestampMs: Date.now(),
      sessionId: this.session?.sessionId ?? null,
    })
  }

  private emitSignal(signal: VoiceAgentSignal): void {
    for (const listener of [...this.signalListeners]) {
      try {
        listener(signal)
      } catch {
        noop()
      }
    }
  }

  private emitSignalsFromState(previous: VoiceSessionState | null, next: VoiceSessionState): void {
    if (!previous) {
      return
    }

    const now = Date.now()
    const sessionId = next.sessionId

    if (previous.turnPhase !== next.turnPhase) {
      this.emitSignal({
        type: "turn.phase.changed",
        phase: next.turnPhase,
        previousPhase: previous.turnPhase,
        timestampMs: now,
        sessionId,
      })
    }

    if (
      previous.pendingTurn.phase !== next.pendingTurn.phase
      || previous.pendingTurn.elapsedMs !== next.pendingTurn.elapsedMs
      || previous.pendingTurn.clientTurnId !== next.pendingTurn.clientTurnId
    ) {
      this.emitSignal({
        type: "pending_turn.state",
        phase: next.pendingTurn.phase,
        elapsedMs: next.pendingTurn.elapsedMs,
        clientTurnId: next.pendingTurn.clientTurnId ?? null,
        timestampMs: now,
        sessionId,
      })
    }

    if (
      previous.stt.status !== next.stt.status
      || previous.stt.waitedMs !== next.stt.waitedMs
      || previous.stt.attempt !== next.stt.attempt
    ) {
      this.emitSignal({
        type: "stt.progress",
        status: next.stt.status ?? null,
        waitedMs: next.stt.waitedMs ?? null,
        attempt: next.stt.attempt ?? null,
        timestampMs: now,
        sessionId,
      })
    }

    if (
      previous.route.routeName !== next.route.routeName
      || previous.route.provider !== next.route.provider
      || previous.route.model !== next.route.model
    ) {
      this.emitSignal({
        type: "route.updated",
        routeName: next.route.routeName ?? null,
        provider: next.route.provider ?? null,
        model: next.route.model ?? null,
        timestampMs: now,
        sessionId,
      })
    }

    if (
      previous.queue.pendingTurns !== next.queue.pendingTurns
      || previous.queue.policy !== next.queue.policy
      || previous.queue.lastSource !== next.queue.lastSource
    ) {
      this.emitSignal({
        type: "queue.updated",
        pendingTurns: next.queue.pendingTurns,
        policy: next.queue.policy ?? null,
        source: next.queue.lastSource ?? null,
        timestampMs: now,
        sessionId,
      })
    }

    if (
      previous.tts.currentSpokenSegment !== next.tts.currentSpokenSegment
      || previous.tts.currentSpokenGenerationId !== next.tts.currentSpokenGenerationId
    ) {
      this.emitSignal({
        type: "assistant.speaking.segment",
        text: next.tts.currentSpokenSegment ?? null,
        generationId: next.tts.currentSpokenGenerationId ?? null,
        timestampMs: now,
        sessionId,
      })
    }

    if (
      previous.tts.status !== next.tts.status
      || previous.tts.durationMs !== next.tts.durationMs
    ) {
      this.emitSignal({
        type: "assistant.speaking.state",
        state: next.tts.status,
        durationMs: next.tts.durationMs ?? null,
        timestampMs: now,
        sessionId,
      })
    }

    if (
      previous.metrics.queueDelayMs !== next.metrics.queueDelayMs
      || previous.metrics.turnToFirstLlmDeltaMs !== next.metrics.turnToFirstLlmDeltaMs
      || previous.metrics.turnToCompleteMs !== next.metrics.turnToCompleteMs
      || previous.metrics.cancelled !== next.metrics.cancelled
      || previous.metrics.reason !== next.metrics.reason
    ) {
      this.emitSignal({
        type: "metrics.turn",
        queueDelayMs: next.metrics.queueDelayMs ?? null,
        turnToFirstLlmDeltaMs: next.metrics.turnToFirstLlmDeltaMs ?? null,
        turnToCompleteMs: next.metrics.turnToCompleteMs ?? null,
        cancelled: next.metrics.cancelled,
        reason: next.metrics.reason ?? null,
        timestampMs: now,
        sessionId,
      })
    }
  }

  private emitSignalsFromEvent(context: VoiceAgentEventContext): void {
    const { event, state } = context
    const sessionId = state.sessionId
    const now = Date.now()

    if (event.type === "vad.state") {
      this.emitSignal({
        type: "vad.state",
        kind: event.kind,
        speaking: event.speaking ?? null,
        probability: event.probability ?? null,
        turnId: event.turn_id ?? null,
        generationId: event.generation_id ?? null,
        timestampMs: now,
        sessionId,
      })
      return
    }

    if (event.type === "session.status") {
      this.emitSignal({
        type: "session.status",
        status: event.status,
        reason: event.reason ?? null,
        generationId: event.generation_id ?? null,
        timestampMs: now,
        sessionId,
      })
      return
    }

    if (event.type === "stt.final") {
      this.emitSignal({
        type: "stt.final",
        text: event.text,
        turnId: event.turn_id ?? null,
        generationId: event.generation_id ?? null,
        revision: event.revision ?? null,
        finality: event.finality ?? null,
        deferred: event.deferred ?? null,
        timestampMs: now,
        sessionId,
      })
      return
    }

    if (event.type === "llm.reasoning.delta") {
      this.emitSignal({
        type: "assistant.thinking",
        delta: event.delta ?? "",
        timestampMs: now,
        sessionId,
      })
      return
    }

    if (event.type === "llm.phase") {
      this.emitSignal({
        type: "assistant.phase",
        phase: event.phase,
        turnId: event.turn_id ?? null,
        generationId: event.generation_id ?? null,
        timestampMs: now,
        sessionId,
      })
      return
    }

    if (event.type === "tts.chunk") {
      this.emitSignal({
        type: "assistant.speaking.state",
        state: "playing",
        durationMs: null,
        timestampMs: now,
        sessionId,
      })
      return
    }

    if (event.type === "tts.completed") {
      this.emitSignal({
        type: "assistant.speaking.state",
        state: "complete",
        durationMs: event.duration_ms ?? null,
        timestampMs: now,
        sessionId,
      })
      return
    }

    if (event.type === "llm.response.delta") {
      this.emitSignal({
        type: "assistant.response.delta",
        delta: event.delta ?? "",
        timestampMs: now,
        sessionId,
      })
      return
    }

    if (event.type === "llm.completed") {
      this.emitSignal({
        type: "assistant.response.final",
        text: event.text,
        provider: event.provider ?? null,
        model: event.model ?? null,
        timestampMs: now,
        sessionId,
      })
      return
    }

    if (event.type === "llm.error") {
      this.emitSignal({
        type: "assistant.error",
        message: event.error?.message ?? "unknown error",
        code: event.error?.code ?? null,
        turnId: event.turn_id ?? null,
        generationId: event.generation_id ?? null,
        timestampMs: now,
        sessionId,
      })
      return
    }

    if (event.type === "conversation.interrupted") {
      this.emitSignal({
        type: "interrupt.lifecycle",
        stage: "acknowledged",
        reason: event.reason ?? null,
        timestampMs: now,
        sessionId,
      })
      return
    }

    if (event.type === "error") {
      this.emitSignal({
        type: "sdk.error",
        message: event.message,
        code: event.code,
        details: event.details ?? null,
        timestampMs: now,
        sessionId,
      })
    }
  }

  private detachSessionSubscriptions(): void {
    this.sessionStateUnsubscribe?.()
    this.sessionStateUnsubscribe = null
    this.sessionEventUnsubscribe?.()
    this.sessionEventUnsubscribe = null
    this.sessionStoreUnsubscribe?.()
    this.sessionStoreUnsubscribe = null
  }
}
