export type SessionStatus =
  | "created"
  | "loading"
  | "ready"
  | "listening"
  | "transcribing"
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

export interface SessionHistoryEntry {
  session_id: string
  status: SessionStatus
  title: string
  created_at: string
  updated_at: string
  active_turn_id?: string | null
  turn_count: number
  completed_turn_count: number
  last_user_text?: string | null
  last_assistant_text?: string | null
}

export interface SessionTurnEntry {
  turn_id: string
  user_text?: string | null
  assistant_text?: string | null
  created_at: string
  completed_at?: string | null
}

export interface EngineDescriptor {
  id: string
  kind: "stt" | "vad" | "router" | "llm" | "tts"
  label: string
  default?: boolean
  available: boolean
  status: string
  capabilities?: Record<string, unknown>
}

export interface EngineCatalogResponse {
  stt: EngineDescriptor[]
  vad?: EngineDescriptor[]
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

export interface RouteTargetPayload {
  llm_engine_id?: string
  provider?: string
  model?: string
  profile_id?: string
}

export interface LlmToolPayload {
  name: string
  description?: string
  kind?: "function" | "mcp"
  parameters?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export interface LlmConfigPayload {
  system_prompt?: string
  additional_instructions?: string
  tools?: LlmToolPayload[]
}

export interface InterruptionPayload {
  mode?: "immediate" | "adaptive" | "disabled"
  min_duration?: number
  min_words?: number
  cooldown_ms?: number
}

export interface EndPointingPayload {
  mode?: "fixed" | "dynamic"
  min_delay?: number
  max_delay?: number
}

export interface RuntimeConfigPayload {
  default_llm_engine_id?: string
  route_targets?: RouteTargetPayload[]
  router?: {
    timeout_ms?: number
    mode?: string
    [key: string]: unknown
  }
  llm?: LlmConfigPayload
  turn_queue?: {
    policy?: "send_now" | "enqueue" | "inject_next_loop"
  }
  interruption?: InterruptionPayload
  endpointing?: EndPointingPayload
  turn_detection?: {
    mode?: string
    transcript_timeout_ms?: number
    stabilization_ms?: number
    min_silence_duration_ms?: number
    min_speech_duration_ms?: number
    activation_threshold?: number
    vad_chunk_size?: number
    [key: string]: unknown
  }
  [key: string]: unknown
}

export interface SessionStartMessage {
  type: "session.start"
  session_id?: string
  engine_selection?: EngineSelection
  metadata?: Record<string, unknown>
  config?: RuntimeConfigPayload
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
  client_turn_id?: string
}

export interface UserTurnCommitMessage {
  type: "user_turn.commit"
  session_id: string
  sequence?: number
  client_turn_id?: string
}

export interface AgentSayMessage {
  type: "agent.say"
  session_id: string
  text: string
}

export interface AgentGenerateReplyMessage {
  type: "agent.generate_reply"
  session_id: string
  user_text: string
  instructions?: string
  allow_interruptions?: boolean
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
  config: RuntimeConfigPayload
}

export interface SessionCloseMessage {
  type: "session.close"
  session_id: string
}

export type ClientMessage =
  | SessionStartMessage
  | AudioAppendMessage
  | AudioCommitMessage
  | UserTurnCommitMessage
  | AgentSayMessage
  | AgentGenerateReplyMessage
  | ConversationInterruptMessage
  | EngineSelectMessage
  | ConfigUpdateMessage
  | SessionCloseMessage

export interface BaseConversationEvent {
  event_id: string
  type: string
  session_id: string
  turn_id?: string | null
  generation_id?: string | null
  timestamp: string
}

export interface SessionCreatedEvent extends BaseConversationEvent {
  type: "session.created"
  status: "created"
}

export interface SessionReadyEvent extends BaseConversationEvent {
  type: "session.ready"
  status: "ready"
}

export interface SessionStatusEvent extends BaseConversationEvent {
  type: "session.status"
  status: SessionStatus
  reason?: string | null
}

export interface VadStateEvent extends BaseConversationEvent {
  type: "vad.state"
  kind: "start_of_speech" | "inference" | "end_of_speech"
  sequence: number
  speaking?: boolean | null
  probability?: number | null
  timestamp_ms?: number | null
  speech_duration_ms?: number | null
  silence_duration_ms?: number | null
}

export interface SttFinalEvent extends BaseConversationEvent {
  type: "stt.final"
  text: string
  confidence?: number | null
  revision?: number | null
  finality?: "stable" | "revised" | "duplicate" | null
  deferred?: boolean | null
  previous_text?: string | null
}

export type SttStatus =
  | "queued"
  | "running"
  | "completed"
  | "timeout"
  | "failed"
  | (string & {})

export interface SttStatusEvent extends BaseConversationEvent {
  type: "stt.status"
  status: SttStatus
  waited_ms?: number | null
  attempt?: number | null
}

export interface RouteSelectedEvent extends BaseConversationEvent {
  type: "route.selected"
  router_id: string
  route_name: string
  llm_engine_id?: string | null
  provider?: string | null
  model?: string | null
  profile_id?: string | null
  reason?: string | null
  confidence?: number | null
}

export interface TokenUsagePayload {
  input_tokens?: number
  output_tokens?: number
  reasoning_tokens?: number
  cache_read_tokens?: number
  cache_write_tokens?: number
  total_tokens?: number
}

export interface LlmPhaseEvent extends BaseConversationEvent {
  type: "llm.phase"
  phase: "thinking" | "generating" | "done"
}

export interface LlmReasoningDeltaEvent extends BaseConversationEvent {
  type: "llm.reasoning.delta"
  part_id?: string | null
  delta: string
}

export interface LlmResponseDeltaEvent extends BaseConversationEvent {
  type: "llm.response.delta"
  part_id?: string | null
  delta: string
  lane: "speech" | "display"
}

export interface LlmToolUpdateEvent extends BaseConversationEvent {
  type: "llm.tool.update"
  call_id?: string | null
  tool_name: string
  status?: string | null
  tool_input?: unknown
  tool_metadata?: Record<string, unknown>
  tool_output?: unknown
  tool_error?: unknown
  is_mcp: boolean
}

export interface LlmUsageEvent extends BaseConversationEvent {
  type: "llm.usage"
  usage?: TokenUsagePayload | null
  cost?: number | null
}

export interface LlmSummaryEvent extends BaseConversationEvent {
  type: "llm.summary"
  provider?: string | null
  model?: string | null
  usage?: TokenUsagePayload | null
  cost?: number | null
}

export interface LlmCompletedEvent extends BaseConversationEvent {
  type: "llm.completed"
  text: string
  finish_reason?: string | null
  provider?: string | null
  model?: string | null
}

export interface LlmErrorEvent extends BaseConversationEvent {
  type: "llm.error"
  error: {
    code: string
    message: string
    retryable: boolean
    details?: Record<string, unknown>
  }
}

export interface TtsChunkEvent extends BaseConversationEvent {
  type: "tts.chunk"
  chunk: Record<string, unknown>
  text_segment?: string | null
}

export interface TtsCompletedEvent extends BaseConversationEvent {
  type: "tts.completed"
  duration_ms?: number | null
}

export interface ConversationInterruptedEvent extends BaseConversationEvent {
  type: "conversation.interrupted"
  reason?: string | null
}

export interface TurnAcceptedEvent extends BaseConversationEvent {
  type: "turn.accepted"
  client_turn_id: string
}

export interface TurnQueuedEvent extends BaseConversationEvent {
  type: "turn.queued"
  queue_size: number
  source?: string | null
  policy?: string | null
}

export interface TurnMetricsEvent extends BaseConversationEvent {
  type: "turn.metrics"
  queue_delay_ms?: number | null
  stt_to_route_ms?: number | null
  route_to_llm_first_delta_ms?: number | null
  llm_first_delta_to_tts_first_chunk_ms?: number | null
  stt_to_tts_first_chunk_ms?: number | null
  turn_to_first_llm_delta_ms?: number | null
  turn_to_complete_ms?: number | null
  cancelled: boolean
  reason?: string | null
}

export interface ErrorEvent extends BaseConversationEvent {
  type: "error"
  code: string
  message: string
  retryable: boolean
  details?: Record<string, unknown>
}

export interface SessionClosedEvent extends BaseConversationEvent {
  type: "session.closed"
  status: "closed"
}

export type ConversationEvent =
  | SessionCreatedEvent
  | SessionReadyEvent
  | SessionStatusEvent
  | VadStateEvent
  | SttFinalEvent
  | SttStatusEvent
  | RouteSelectedEvent
  | LlmPhaseEvent
  | LlmReasoningDeltaEvent
  | LlmResponseDeltaEvent
  | LlmToolUpdateEvent
  | LlmUsageEvent
  | LlmSummaryEvent
  | LlmCompletedEvent
  | LlmErrorEvent
  | TtsChunkEvent
  | TtsCompletedEvent
  | ConversationInterruptedEvent
  | TurnAcceptedEvent
  | TurnQueuedEvent
  | TurnMetricsEvent
  | ErrorEvent
  | SessionClosedEvent
