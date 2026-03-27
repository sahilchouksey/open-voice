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
  turnDetection?: Record<string, unknown>
  retry?: RetryConfig
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
}

export interface EndPointingConfig {
  mode?: "fixed" | "dynamic"
  minDelay?: number // Minimum time after speech to declare turn complete (seconds)
  maxDelay?: number // Maximum time to wait before terminating turn (seconds)
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
