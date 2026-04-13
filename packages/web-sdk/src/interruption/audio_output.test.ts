import { describe, expect, test } from "bun:test"

import type { AudioOutputAdapter, TtsChunk } from "./audio_output"
import { SessionAudioController } from "./audio_output"
import type { ConversationEvent, TtsChunkEvent } from "../protocol"

class FakeAudioOutput implements AudioOutputAdapter {
  readonly appended: TtsChunk[] = []
  readonly flushReasons: string[] = []

  async appendTtsChunk(chunk: TtsChunk): Promise<void> {
    this.appended.push(chunk)
  }

  async flush(reason?: string): Promise<void> {
    this.flushReasons.push(reason ?? "")
  }
}

describe("SessionAudioController", () => {
  test("interrupt() flushes current audio and rejects stale generation chunks", async () => {
    const output = new FakeAudioOutput()
    const controller = new SessionAudioController(output)

    await controller.onEvent(createTtsChunkEvent("gen-a", 0))
    expect(output.appended).toHaveLength(1)

    await controller.interrupt("manual")

    await controller.onEvent(createTtsChunkEvent("gen-a", 1))
    expect(output.appended).toHaveLength(1)

    await controller.onEvent(createLlmPhaseEvent("gen-b", "thinking"))
    await controller.onEvent(createTtsChunkEvent("gen-b", 0))

    expect(output.appended).toHaveLength(2)
    expect(output.appended[1]?.generationId).toBe("gen-b")
    expect(output.flushReasons).toContain("manual")
  })

  test("conversation.interrupted from backend flushes output and allows next generation", async () => {
    const output = new FakeAudioOutput()
    const controller = new SessionAudioController(output)

    await controller.onEvent(createTtsChunkEvent("gen-a", 0))
    expect(output.appended).toHaveLength(1)

    await controller.onEvent(createInterruptedEvent("gen-a", "send_now"))
    await controller.onEvent(createTtsChunkEvent("gen-a", 1))
    expect(output.appended).toHaveLength(1)

    await controller.onEvent(createSttFinalEvent("gen-b", "new request"))
    await controller.onEvent(createLlmPhaseEvent("gen-b", "thinking"))
    await controller.onEvent(createTtsChunkEvent("gen-b", 0))

    expect(output.appended).toHaveLength(2)
    expect(output.appended[1]?.generationId).toBe("gen-b")
    expect(output.flushReasons).toContain("conversation.interrupted")
  })
})

function createTtsChunkEvent(generationId: string, sequence: number): TtsChunkEvent {
  return {
    type: "tts.chunk",
    session_id: "sess-1",
    turn_id: "turn-1",
    generation_id: generationId,
    event_id: `evt-tts-${generationId}-${sequence}`,
    timestamp: new Date().toISOString(),
    chunk: {
      data_base64: Buffer.from(Uint8Array.from([1, 2, 3, sequence])).toString("base64"),
      sample_rate_hz: 24000,
      channels: 1,
      encoding: "pcm_s16le",
      sequence,
      duration_ms: 40,
    },
    text_segment: `chunk-${sequence}`,
  }
}

function createInterruptedEvent(generationId: string, reason: string): ConversationEvent {
  return {
    type: "conversation.interrupted",
    session_id: "sess-1",
    turn_id: "turn-1",
    generation_id: generationId,
    event_id: `evt-interrupt-${generationId}`,
    timestamp: new Date().toISOString(),
    reason,
  }
}

function createLlmPhaseEvent(
  generationId: string,
  phase: "thinking" | "generating" | "done",
): ConversationEvent {
  return {
    type: "llm.phase",
    session_id: "sess-1",
    turn_id: "turn-2",
    generation_id: generationId,
    event_id: `evt-phase-${generationId}-${phase}`,
    timestamp: new Date().toISOString(),
    phase,
  }
}

function createSttFinalEvent(generationId: string, text: string): ConversationEvent {
  return {
    type: "stt.final",
    session_id: "sess-1",
    turn_id: "turn-2",
    generation_id: generationId,
    event_id: `evt-stt-${generationId}`,
    timestamp: new Date().toISOString(),
    text,
  }
}
