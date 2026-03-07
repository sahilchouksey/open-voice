export type SessionStatus =
  | "created"
  | "loading"
  | "ready"
  | "listening"
  | "thinking"
  | "speaking"
  | "interrupted"
  | "closed"
  | "failed"

export interface EngineSelection {
  stt?: string
  router?: string
  llm?: string
  tts?: string
}

export interface SessionState {
  session_id: string
  status: SessionStatus
  created_at: string
  updated_at: string
  active_turn_id?: string | null
  engine_selection: EngineSelection
  metadata?: Record<string, unknown>
}

export interface EngineDescriptor {
  id: string
  kind: "stt" | "router" | "llm" | "tts"
  label: string
  default?: boolean
  capabilities?: Record<string, unknown>
}

export interface EngineCatalogResponse {
  stt: EngineDescriptor[]
  router: EngineDescriptor[]
  llm: EngineDescriptor[]
  tts: EngineDescriptor[]
}

export interface AudioChunkMessage {
  chunk_id: string
  sequence: number
  encoding: "pcm_s16le" | "pcm_f32le"
  sample_rate_hz: number
  channels: number
  duration_ms?: number
  transport: "inline-base64" | "binary-frame"
  data_base64?: string
}

export interface SessionStartMessage {
  type: "session.start"
  session_id?: string
  engine_selection?: EngineSelection
  metadata?: Record<string, unknown>
}

export interface AudioAppendMessage {
  type: "audio.append"
  session_id: string
  chunk: AudioChunkMessage
}

export interface AudioCommitMessage {
  type: "audio.commit"
  session_id: string
  sequence?: number
}

export interface ConversationInterruptMessage {
  type: "conversation.interrupt"
  session_id: string
  reason?: string
}

export interface EngineSelectMessage {
  type: "engine.select"
  session_id: string
  engine_selection: EngineSelection
}

export interface ConfigUpdateMessage {
  type: "config.update"
  session_id: string
  config: Record<string, unknown>
}

export interface SessionCloseMessage {
  type: "session.close"
  session_id: string
}

export type ClientMessage =
  | SessionStartMessage
  | AudioAppendMessage
  | AudioCommitMessage
  | ConversationInterruptMessage
  | EngineSelectMessage
  | ConfigUpdateMessage
  | SessionCloseMessage

export interface BaseConversationEvent {
  event_id: string
  type: string
  session_id: string
  turn_id?: string | null
  timestamp: string
}

export type ConversationEvent = BaseConversationEvent & Record<string, unknown>
