import type { VadStateEvent } from "../protocol"

const FILLER_PARTIAL_TOKENS = new Set([
  "uh",
  "um",
  "er",
  "ah",
  "hmm",
  "mm",
  "mhm",
  "hm",
])

export interface InterruptionPolicyConfig {
  minWords?: number
  cooldownMs?: number
}

export class InterruptionPolicy {
  private lastInterruptAt = 0
  private readonly minWords: number
  private readonly cooldownMs: number

  constructor(config: InterruptionPolicyConfig = {}) {
    this.minWords = Math.max(1, config.minWords ?? 1)
    this.cooldownMs = Math.max(0, config.cooldownMs ?? 300)
  }

  canInterrupt(now = Date.now()): boolean {
    return now - this.lastInterruptAt >= this.cooldownMs
  }

  markInterrupted(now = Date.now()): void {
    this.lastInterruptAt = now
  }

  isInterruptWorthyPartial(text: string): boolean {
    const trimmed = text.trim().toLowerCase()
    if (trimmed.length < 2) {
      return false
    }
    const tokens = trimmed
      .split(/\s+/)
      .map((token) => token.replace(/[^a-z0-9']/g, ""))
      .filter((token) => token.length > 0)
      .filter((token) => !FILLER_PARTIAL_TOKENS.has(token))
    return tokens.length >= this.minWords
  }

  isInterruptWorthyVad(event: VadStateEvent): boolean {
    return event.kind === "start_of_speech" || Boolean(event.speaking)
  }

  shouldInterruptFromPartial(text: string, now = Date.now()): boolean {
    return this.canInterrupt(now) && this.isInterruptWorthyPartial(text)
  }

  shouldInterruptFromVad(event: VadStateEvent, now = Date.now()): boolean {
    return this.canInterrupt(now) && this.isInterruptWorthyVad(event)
  }
}
