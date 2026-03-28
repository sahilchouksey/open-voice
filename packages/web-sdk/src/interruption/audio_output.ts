import type { ConversationEvent } from "../protocol"
import { GenerationEventGate } from "./generation_gate"

export interface TtsChunk {
  data: Uint8Array
  sampleRateHz: number
  generationId?: string | null
  event: ConversationEvent
}

export interface AudioOutputAdapter {
  appendTtsChunk(chunk: TtsChunk): Promise<void>
  flush(reason?: string): Promise<void>
}

export function extractTtsChunk(event: ConversationEvent): TtsChunk | null {
  if (event.type !== "tts.chunk") return null
  const payload = event.chunk as { data_base64?: unknown; sample_rate_hz?: unknown }
  if (typeof payload.data_base64 !== "string") return null
  const data = base64ToBytes(payload.data_base64)
  return {
    data,
    sampleRateHz: typeof payload.sample_rate_hz === "number" ? payload.sample_rate_hz : 24000,
    generationId: event.generation_id,
    event,
  }
}

export class SessionAudioController {
  private readonly gate = new GenerationEventGate()
  private eventChain: Promise<void> = Promise.resolve()
  private interruptionEpoch = 0

  constructor(private readonly output: AudioOutputAdapter) {}

  private enqueue(operation: () => Promise<void>): Promise<void> {
    const epoch = this.interruptionEpoch
    const guarded = async () => {
      if (epoch !== this.interruptionEpoch) {
        return
      }
      await operation()
    }
    const run = this.eventChain.then(guarded, guarded)
    this.eventChain = run.catch(() => undefined)
    return run
  }

  private async preemptAndFlush(reason: string): Promise<void> {
    this.gate.rejectActiveGeneration()
    this.interruptionEpoch += 1

    const pending = this.eventChain.catch(() => undefined)
    this.eventChain = Promise.resolve()

    await this.output.flush(reason).catch(() => undefined)
    await pending
    await this.output.flush(`${reason}.settle`).catch(() => undefined)
  }

  shouldAccept(event: ConversationEvent): boolean {
    return this.gate.shouldAccept(event)
  }

  async onEvent(event: ConversationEvent): Promise<void> {
    if (event.type === "conversation.interrupted") {
      this.gate.observe(event)
      await this.preemptAndFlush("conversation.interrupted")
      return
    }

    if (event.type === "llm.error") {
      this.gate.observe(event)
      await this.preemptAndFlush("llm.error")
      return
    }

    return this.enqueue(async () => {
      try {
        if (!this.gate.shouldAccept(event)) {
          return
        }

        this.gate.observe(event)

        const ttsChunk = extractTtsChunk(event)
        if (ttsChunk) {
          await this.output.appendTtsChunk(ttsChunk)
          return
        }

        if (event.type === "tts.completed") {
          return
        }
      } catch {
        this.gate.rejectActiveGeneration()
        await this.output.flush("audio.output.error_recovery")
      }
    })
  }

  async interrupt(reason?: string): Promise<void> {
    await this.preemptAndFlush(reason ?? "interrupt")
  }

  reset(): void {
    this.gate.reset()
  }
}

function base64ToBytes(text: string): Uint8Array {
  const binary = atob(text)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return bytes
}
