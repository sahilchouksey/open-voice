export type AgentUiState = "initializing" | "listening" | "thinking" | "speaking"

export function toAgentUiState(turnPhase: string, sessionStatus: string): AgentUiState {
  if (sessionStatus === "disconnected") return "initializing"
  if (turnPhase === "agent_speaking") return "speaking"
  if (turnPhase === "processing") return "thinking"
  return "listening"
}

export function normalizeLevel(micLevel: number): number {
  const v = Number.isFinite(micLevel) ? micLevel / 100 : 0
  return Math.max(0.08, Math.min(1, v))
}
