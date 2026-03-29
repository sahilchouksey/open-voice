import type {
  LlmConfigPayload,
  LlmToolPayload,
  RouteTargetPayload,
  RuntimeConfigPayload,
} from "./protocol"

export interface RouteTargetConfig {
  llmEngineId?: string
  provider?: string
  model?: string
  profileId?: string
}

export interface RuntimeSessionConfig {
  defaultLlmEngineId?: string
  routeTargets?: RouteTargetConfig[]
  router?: RouterConfig
  llm?: LlmSessionConfig
  turnQueue?: TurnQueueConfig
  interruption?: InterruptionConfig
  endpointing?: EndPointingConfig
  endPointing?: EndPointingConfig
  turnDetection?: Record<string, unknown>
  retry?: RetryConfig
  client?: ClientBehaviorConfig
  raw?: Record<string, unknown>
}

export interface RetryConfig {
  enabled?: boolean
  afterMs?: number
}

export interface RouterConfig {
  timeoutMs?: number
  mode?: "disabled" | "fallback_only" | "enabled"
}

export interface TurnQueueConfig {
  policy?: "send_now" | "enqueue" | "inject_next_loop"
}

export interface InterruptionConfig {
  mode?: "immediate" | "adaptive" | "disabled"
  minDuration?: number // Minimum speech duration in seconds
  minWords?: number // Minimum words before interrupt (0 to disable)
  cooldownMs?: number // Cooldown between interrupts in milliseconds
  autoInterrupt?: AutoInterruptConfig
}

export interface AutoInterruptConfig {
  enabled?: boolean
  vadThreshold?: number // VAD probability threshold to trigger interrupt
  minDurationMs?: number // Minimum speech duration before interrupt
}

export interface EndPointingConfig {
  mode?: "fixed" | "dynamic"
  minDelay?: number // Minimum time after speech to declare turn complete (seconds)
  maxDelay?: number // Maximum time to wait before terminating turn (seconds)
}

export interface ClientBehaviorConfig {
  echoFiltering?: EchoFilterConfig
  autoCommit?: AutoCommitConfig
}

export interface EchoFilterConfig {
  enabled?: boolean
}

export interface AutoCommitConfig {
  enabled?: boolean
  vadEndOfSpeechDelayMs?: number // Delay after VAD end-of-speech before committing
  minSpeechDurationMs?: number // Minimum speech duration to trigger auto-commit
}

export interface LlmToolConfig {
  name: string
  description?: string
  kind?: "function" | "mcp"
  parameters?: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export interface LlmSessionConfig {
  systemPrompt?: string
  additionalInstructions?: string
  tools?: LlmToolConfig[]
  enable_fast_ack?: boolean
  opencode_mode?: string
  opencode_force_system_override?: boolean
  [key: string]: unknown
}

export function toRuntimeConfigPayload(
  config?: RuntimeSessionConfig,
): RuntimeConfigPayload | undefined {
  if (!config) return undefined

  const payload: RuntimeConfigPayload = {}
  if (config.defaultLlmEngineId !== undefined) {
    payload.default_llm_engine_id = config.defaultLlmEngineId
  }
  if (config.routeTargets !== undefined) {
    payload.route_targets = config.routeTargets.map(toRouteTargetPayload)
  }
  if (config.router !== undefined) {
    const routerPayload: Record<string, unknown> = {}
    if (config.router.timeoutMs !== undefined) routerPayload.timeout_ms = config.router.timeoutMs
    if (config.router.mode !== undefined) routerPayload.mode = config.router.mode
    payload.router = routerPayload
  }
  if (config.llm !== undefined) {
    payload.llm = toLlmConfigPayload(config.llm)
  }
  if (config.turnQueue?.policy !== undefined) {
    payload.turn_queue = { policy: config.turnQueue.policy }
  }
  if (config.interruption !== undefined) {
    payload.interruption = toInterruptionPayload(config.interruption)
  }
  if (config.endpointing !== undefined) {
    payload.endpointing = toEndPointingPayload(config.endpointing)
  }
  if (config.endPointing !== undefined) {
    payload.endpointing = toEndPointingPayload(config.endPointing)
  }
  if (config.turnDetection !== undefined) {
    payload.turn_detection = config.turnDetection
  }
  if (config.retry !== undefined) {
    const retryPayload: Record<string, unknown> = {}
    if (config.retry.enabled !== undefined) retryPayload.enabled = config.retry.enabled
    if (config.retry.afterMs !== undefined) retryPayload.after_ms = config.retry.afterMs
    payload.retry = retryPayload
  }
  if (config.client !== undefined) {
    payload.client = toClientBehaviorPayload(config.client)
  }
  if (config.raw !== undefined) {
    Object.assign(payload, config.raw)
  }

  return Object.keys(payload).length > 0 ? payload : undefined
}

function toInterruptionPayload(config: InterruptionConfig): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  if (config.mode !== undefined) payload.mode = config.mode
  if (config.minDuration !== undefined) payload.min_duration = config.minDuration
  if (config.minWords !== undefined) payload.min_words = config.minWords
  if (config.cooldownMs !== undefined) payload.cooldown_ms = config.cooldownMs
  if (config.autoInterrupt !== undefined) {
    const autoPayload: Record<string, unknown> = {}
    if (config.autoInterrupt.enabled !== undefined) autoPayload.enabled = config.autoInterrupt.enabled
    if (config.autoInterrupt.vadThreshold !== undefined) autoPayload.vad_threshold = config.autoInterrupt.vadThreshold
    if (config.autoInterrupt.minDurationMs !== undefined) autoPayload.min_duration_ms = config.autoInterrupt.minDurationMs
    payload.auto_interrupt = autoPayload
  }
  return payload
}

function toClientBehaviorPayload(config: ClientBehaviorConfig): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  if (config.echoFiltering !== undefined) {
    payload.echo_filtering = { enabled: config.echoFiltering.enabled ?? true }
  }
  if (config.autoCommit !== undefined) {
    const commitPayload: Record<string, unknown> = {}
    if (config.autoCommit.enabled !== undefined) commitPayload.enabled = config.autoCommit.enabled
    if (config.autoCommit.vadEndOfSpeechDelayMs !== undefined) commitPayload.vad_eos_delay_ms = config.autoCommit.vadEndOfSpeechDelayMs
    if (config.autoCommit.minSpeechDurationMs !== undefined) commitPayload.min_speech_duration_ms = config.autoCommit.minSpeechDurationMs
    payload.auto_commit = commitPayload
  }
  return payload
}

function toEndPointingPayload(config: EndPointingConfig): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  if (config.mode !== undefined) payload.mode = config.mode
  if (config.minDelay !== undefined) payload.min_delay = config.minDelay
  if (config.maxDelay !== undefined) payload.max_delay = config.maxDelay
  return payload
}

function toRouteTargetPayload(target: RouteTargetConfig): RouteTargetPayload {
  const payload: RouteTargetPayload = {}
  if (target.llmEngineId !== undefined) payload.llm_engine_id = target.llmEngineId
  if (target.provider !== undefined) payload.provider = target.provider
  if (target.model !== undefined) payload.model = target.model
  if (target.profileId !== undefined) payload.profile_id = target.profileId
  return payload
}

function toLlmConfigPayload(config: LlmSessionConfig): LlmConfigPayload {
  const payload: LlmConfigPayload = {}
  for (const [key, value] of Object.entries(config)) {
    if (value !== undefined) {
      ;(payload as Record<string, unknown>)[key] = value
    }
  }
  if (config.systemPrompt !== undefined) payload.system_prompt = config.systemPrompt
  if (config.additionalInstructions !== undefined) {
    payload.additional_instructions = config.additionalInstructions
  }
  if (config.tools !== undefined) {
    payload.tools = config.tools.map(toLlmToolPayload)
  }
  return payload
}

function toLlmToolPayload(tool: LlmToolConfig): LlmToolPayload {
  const payload: LlmToolPayload = {
    name: tool.name,
  }
  if (tool.description !== undefined) payload.description = tool.description
  if (tool.kind !== undefined) payload.kind = tool.kind
  if (tool.parameters !== undefined) payload.parameters = tool.parameters
  if (tool.metadata !== undefined) payload.metadata = tool.metadata
  return payload
}
