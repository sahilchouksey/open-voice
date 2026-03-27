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

  constructor(private readonly output: AudioOutputAdapter) {}

  private enqueue(operation: () => Promise<void>): Promise<void> {
    const run = this.eventChain.then(operation, operation)
    this.eventChain = run.catch(() => undefined)
    return run
  }

  shouldAccept(event: ConversationEvent): boolean {
    return this.gate.shouldAccept(event)
  }

  async onEvent(event: ConversationEvent): Promise<void> {
    return this.enqueue(async () => {
      if (!this.gate.shouldAccept(event)) {
        return
      }

      this.gate.observe(event)

      if (event.type === "conversation.interrupted") {
        await this.output.flush("conversation.interrupted")
        return
      }

      if (event.type === "llm.error") {
        this.gate.rejectActiveGeneration()
        await this.output.flush("llm.error")
        return
      }

      const ttsChunk = extractTtsChunk(event)
      if (ttsChunk) {
        await this.output.appendTtsChunk(ttsChunk)
        return
      }

      if (event.type === "tts.completed") {
        return
      }
    })
  }

  async interrupt(reason?: string): Promise<void> {
    this.gate.rejectActiveGeneration()
    await this.enqueue(async () => {
      await this.output.flush(reason ?? "interrupt")
    })
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
