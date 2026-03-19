import type { ConversationEvent } from "../protocol"

const GENERATION_SCOPED_EVENT_TYPES = new Set<string>([
  "llm.phase",
  "llm.reasoning.delta",
  "llm.response.delta",
  "llm.tool.update",
  "llm.usage",
  "llm.summary",
  "llm.completed",
  "tts.chunk",
  "tts.completed",
])

const GENERATION_START_EVENT_TYPES = new Set<string>([
  "stt.final",
  "llm.phase",
  "llm.reasoning.delta",
  "llm.response.delta",
  "llm.tool.update",
  "tts.chunk",
])

export class GenerationEventGate {
  private activeGenerationId: string | null = null
  private readonly rejectedGenerationIds = new Set<string>()

  constructor(private readonly maxRejected = 32) {}

  get currentGenerationId(): string | null {
    return this.activeGenerationId
  }

  shouldAccept(event: ConversationEvent): boolean {
    const generationId = event.generation_id ?? null
    if (!generationId) return true

    if (this.rejectedGenerationIds.has(generationId)) {
      return false
    }

    const activeGenerationId = this.activeGenerationId

    if (!activeGenerationId && GENERATION_SCOPED_EVENT_TYPES.has(event.type)) {
      return GENERATION_START_EVENT_TYPES.has(event.type)
    }
    if (activeGenerationId && generationId !== activeGenerationId) {
      if (
        this.rejectedGenerationIds.has(activeGenerationId) &&
        GENERATION_START_EVENT_TYPES.has(event.type)
      ) {
        return true
      }
      return !GENERATION_SCOPED_EVENT_TYPES.has(event.type)
    }

    return true
  }

  observe(event: ConversationEvent): void {
    if (!this.shouldAccept(event)) {
      return
    }

    if (event.type === "conversation.interrupted") {
      this.rejectActiveGeneration()
      return
    }

    const generationId = event.generation_id ?? null
    if (!generationId) {
      return
    }

    const activeGenerationId = this.activeGenerationId
    const hasActiveGeneration = Boolean(activeGenerationId)
    const generationChanged = hasActiveGeneration && activeGenerationId !== generationId

    if (generationChanged && event.type === "stt.final") {
      this.rejectActiveGeneration()
      this.activeGenerationId = generationId
      return
    }

    if (
      generationChanged &&
      activeGenerationId &&
      this.rejectedGenerationIds.has(activeGenerationId) &&
      GENERATION_START_EVENT_TYPES.has(event.type)
    ) {
      this.activeGenerationId = generationId
      return
    }

    if (!hasActiveGeneration || GENERATION_START_EVENT_TYPES.has(event.type)) {
      this.activeGenerationId = generationId
    }
  }

  rejectActiveGeneration(): void {
    if (!this.activeGenerationId) {
      return
    }
    this.rememberRejected(this.activeGenerationId)
  }

  reset(): void {
    this.activeGenerationId = null
    this.rejectedGenerationIds.clear()
  }

  private rememberRejected(generationId: string): void {
    this.rejectedGenerationIds.add(generationId)
    if (this.rejectedGenerationIds.size <= this.maxRejected) {
      return
    }
    const oldest = this.rejectedGenerationIds.values().next().value
    if (oldest) {
      this.rejectedGenerationIds.delete(oldest)
    }
  }
}
