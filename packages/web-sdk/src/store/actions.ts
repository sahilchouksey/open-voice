import type { ClientMessage, ConversationEvent } from "../protocol"
import type { TranscriptEntry } from "../state"

export type VoiceStoreAction =
  | { type: "event.inbound"; event: ConversationEvent }
  | { type: "message.outbound"; message: ClientMessage; timestampMs: number }
  | { type: "clock.tick"; timestampMs: number }
  | { type: "session.reset"; sessionId: string }
  | { type: "transcript.set"; entries: TranscriptEntry[] }
