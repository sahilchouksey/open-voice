import type { ConversationEvent } from "../protocol"
import { GenerationEventGate } from "../interruption/generation_gate"

export interface GenerationOwnershipDecision {
  acceptForState: boolean
  acceptForAudio: boolean
}

export class GenerationOwner {
  private readonly gate: GenerationEventGate

  constructor(gate = new GenerationEventGate()) {
    this.gate = gate
  }

  decide(event: ConversationEvent): GenerationOwnershipDecision {
    const accept = this.gate.shouldAccept(event)
    if (accept) {
      this.gate.observe(event)
    }
    return {
      acceptForState: accept,
      acceptForAudio: accept,
    }
  }

  rejectActiveGeneration(): void {
    this.gate.rejectActiveGeneration()
  }

  reset(): void {
    this.gate.reset()
  }
}
