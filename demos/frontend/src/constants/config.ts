export const AUDIO_BAND_COUNT = 9

export const DEMO_MIN_SPEECH_DURATION_MS = 220
export const DEMO_VAD_ACTIVATION_THRESHOLD = 0.55
export const DEMO_UI_VAD_PROBABILITY_THRESHOLD = 0.6
export const DEMO_INTERRUPT_COOLDOWN_MS = 60
export const DEMO_INTERRUPT_MIN_DURATION_SECONDS = 0.03
export const DEMO_INTERRUPT_MIN_WORDS = 1
export const DEMO_LOCAL_BARGE_IN_PEAK_THRESHOLD = 0.15
export const DEMO_LOCAL_BARGE_IN_CONSECUTIVE_FRAMES = 12
export const DEMO_LOCAL_BARGE_IN_COOLDOWN_MS = 1000
export const DEMO_LOCAL_BARGE_IN_FLOOR_ALPHA = 0.08
export const DEMO_LOCAL_BARGE_IN_FLOOR_MULTIPLIER = 2.2
export const DEMO_LOCAL_BARGE_IN_FLOOR_BIAS = 0.04
export const DEMO_ENABLE_STT_PARTIAL_AUTO_INTERRUPT = true
export const DEMO_ENABLE_LOCAL_AUDIO_AUTO_INTERRUPT = false
export const DEMO_ENABLE_VAD_AUTO_INTERRUPT = false
export const DEMO_STT_TRANSCRIPT_TIMEOUT_MS = 1200
export const DEMO_MIN_SILENCE_DURATION_MS = 260
export const DEMO_POST_RELEASE_PROCESSING_GRACE_MS = 1400
export const DEMO_IDLE_TRANSITION_DELAY_MS = 220
export const DEMO_SLOW_STT_STABILIZATION_MS = 160
export const DEMO_MIC_STOP_COMMIT_GRACE_MS = 900
export const DEMO_AUTO_COMMIT_MIN_INTERVAL_MS = 400
export const DEMO_ROUTER_TIMEOUT_MS = 7000
export const DEMO_CAPTURE_BUFFER_SIZE = 4096
export const DEMO_GENERATION_WATCHDOG_TIMEOUT_MS = 30000
export const DEMO_STT_FINAL_TIMEOUT_MS = 800
export const DEMO_LLM_FIRST_DELTA_TIMEOUT_MS = 45000
export const DEMO_LLM_TOTAL_TIMEOUT_MS = 90000
export const DEMO_ROUTER_MODE: "disabled" | "fallback_only" | "enabled" = "fallback_only"
export const DEMO_PHASE_DEBOUNCE_MS = 180
export const DEMO_FORCE_SEND_NOW_DEFAULT = true
export const DEMO_DISABLE_STT_STABILIZATION = false
export const DEMO_EVENT_TRACE_MAX_ITEMS = 80
export const DEMO_EVENT_TRACE_FLUSH_MS = 120
export const DEMO_UI_STT_TEXT_THROTTLE_MS = 16
export const DEMO_UI_LLM_DELTA_THROTTLE_MS = 16
export const DEMO_UI_MIC_LEVEL_THROTTLE_MS = 16
export const DEMO_UI_BAND_INTERVAL_MS = 16

export const DEMO_SEND_NOW_RUNTIME_OWNED_COMMIT = true

export const MINIMAL_CAPTIONS_STORAGE_KEY = "openvoice.minimal.captions"
export const MINIMAL_DETAIL_STORAGE_KEY = "openvoice.minimal.detail"
export const MINIMAL_CHAT_HISTORY_STORAGE_KEY = "openvoice.minimal.chat_history"
export const LOCAL_SESSION_HISTORY_STORAGE_KEY = "openvoice.session_history.v1"
export const SESSION_HISTORY_LIMIT = 5
export const SESSION_TRANSCRIPT_LIMIT = 50

export const MINIMAL_VISUALIZER_STYLE: "radial" | "grid" = "grid"

export const OPENCODE_MODE = "voice"

export const VOICE_LLM_TOOLS = [
  {
    name: "websearch",
    kind: "mcp" as const,
    description: "Search the web for current information and relevant sources.",
  },
]

export const OPEN_VOICE_SYSTEM_PROMPT = [
  "You are Open Voice, a realtime voice-first assistant for conversation and web research.",
  "Prioritize natural spoken responses that are concise, clear, and interruption-friendly.",
  "If a newer user utterance arrives, immediately abandon stale context and continue from the latest user intent.",
  "For current events or other time-sensitive questions, always search the web before answering.",
  "Never guess or rely on stale memory for news, politics, markets, sports, weather, or other live facts.",
  "Use tools when needed, but never expose internal routing, model, or tool implementation details.",
  "Never read full URLs aloud.",
  "If a source must be spoken, say only the domain name.",
  "Never include protocol, path, query params, tracking codes, or full link strings in spoken output.",
  "If the user asks for a link, explain what they will find there while speaking only the domain name.",
  "If the user explicitly requests the exact link text, say it can be shown on screen but not spoken aloud.",
].join(" ")
