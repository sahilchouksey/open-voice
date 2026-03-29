import type { ConversationEvent, SessionHistoryEntry } from "@open-voice/web-sdk"
import type { TranscriptItem, Mode } from "../types"
import {
  MINIMAL_CAPTIONS_STORAGE_KEY,
  MINIMAL_DETAIL_STORAGE_KEY,
  MINIMAL_CHAT_HISTORY_STORAGE_KEY,
  LOCAL_SESSION_HISTORY_STORAGE_KEY,
  SESSION_HISTORY_LIMIT,
  SESSION_TRANSCRIPT_LIMIT,
  DEMO_UI_BAND_INTERVAL_MS,
} from "../constants/config"

export function zeroBands(count = 9): number[] {
  return Array.from({ length: count }, () => 0)
}

export function normalizeSpeechCompare(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

export function isLikelyAssistantEcho(partialText: string, assistantText: string): boolean {
  const partial = normalizeSpeechCompare(partialText)
  const assistant = normalizeSpeechCompare(assistantText)
  if (partial.length < 4 || assistant.length < 4) return false
  return assistant.includes(partial)
}

export function envFlag(value: unknown, defaultValue = false): boolean {
  if (typeof value === "boolean") {
    return value
  }
  if (typeof value !== "string") {
    return defaultValue
  }

  const normalized = value.trim().toLowerCase()
  if (["1", "true", "yes", "on", "enabled"].includes(normalized)) {
    return true
  }
  if (["0", "false", "no", "off", "disabled", ""].includes(normalized)) {
    return false
  }
  return defaultValue
}

export const FRONTEND_DIAGNOSTICS_ENABLED = envFlag(
  import.meta.env.VITE_OPEN_VOICE_FRONTEND_DIAGNOSTICS,
  false,
)

export function isLoopbackHost(hostname: string): boolean {
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1"
}

export function resolveInitialRuntimeBaseUrl(): string {
  const fromQuery = new URLSearchParams(location.search).get("runtime")?.trim()
  const fromStorage = localStorage.getItem("openvoice.runtimeBaseUrl")?.trim()
  const fallback = location.origin
  const candidate = fromQuery || fromStorage

  if (!candidate) return fallback

  try {
    const runtimeUrl = new URL(candidate, location.origin)
    if (!isLoopbackHost(location.hostname) && isLoopbackHost(runtimeUrl.hostname)) {
      return fallback
    }
    return runtimeUrl.origin
  } catch {
    return fallback
  }
}

export function parseModeValue(value: string | null): Mode | null {
  if (value === "minimal" || value === "detailed") {
    return value
  }
  return null
}

export function resolveInitialMode(): Mode {
  const params = new URLSearchParams(location.search)
  return parseModeValue(params.get("tab")) ?? parseModeValue(params.get("mode")) ?? "detailed"
}

export function resolveSessionIdFromUrl(): string | null {
  const params = new URLSearchParams(location.search)
  const raw = params.get("session")?.trim() ?? params.get("session_id")?.trim() ?? ""
  return raw || null
}

export function persistSessionIdToUrl(sessionId: string | null): void {
  const url = new URL(window.location.href)
  if (sessionId && sessionId.trim()) {
    url.searchParams.set("session", sessionId.trim())
  } else {
    url.searchParams.delete("session")
  }
  url.searchParams.delete("session_id")
  window.history.replaceState(window.history.state, "", url)
}

export function isSessionClosedOrFailed(status: unknown): boolean {
  return status === "closed" || status === "failed"
}

export function resolveStoredFlag(storageKey: string, fallback: boolean): boolean {
  const raw = localStorage.getItem(storageKey)
  if (raw === "1" || raw === "true") return true
  if (raw === "0" || raw === "false") return false
  return fallback
}

export function trimText(text: string, maxChars = 160): string {
  const normalized = text.trim().replace(/\s+/g, " ")
  if (normalized.length <= maxChars) {
    return normalized
  }
  return `${normalized.slice(0, maxChars - 3).trimEnd()}...`
}

export function makeHistoryTitle(item: SessionHistoryEntry): string {
  const fromLastUser = typeof item.last_user_text === "string" ? trimText(item.last_user_text, 80) : ""
  const fromTitle = typeof item.title === "string" ? item.title.trim() : ""
  if (fromLastUser) return fromLastUser
  if (fromTitle) return fromTitle
  return item.session_id.slice(0, 8)
}

export function buildSessionHistoryEntry(item: SessionHistoryEntry) {
  return {
    sessionId: item.session_id,
    title: makeHistoryTitle(item),
    status: item.status,
    updatedAt: item.updated_at,
    turnCount: item.turn_count,
    completedTurnCount: item.completed_turn_count,
    lastUserText: typeof item.last_user_text === "string" ? item.last_user_text : null,
    lastAssistantText: typeof item.last_assistant_text === "string" ? item.last_assistant_text : null,
    transcript: [],
  }
}

export function transcriptSummaryText(item: TranscriptItem): string {
  return trimText(item.text, 140)
}

export function readStoredSessionHistory() {
  const raw = localStorage.getItem(LOCAL_SESSION_HISTORY_STORAGE_KEY)
  if (!raw) {
    return []
  }

  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) {
      return []
    }

    const rows = []
    for (const item of parsed) {
      if (!item || typeof item !== "object") {
        continue
      }
      const sessionId = typeof item.sessionId === "string" ? item.sessionId : ""
      if (!sessionId) {
        continue
      }
      const transcript = Array.isArray((item as { transcript?: unknown }).transcript)
        ? (item as { transcript: unknown[] }).transcript
            .map((entry) => {
              if (!entry || typeof entry !== "object") return null
              const role = (entry as { role?: unknown }).role
              const text = (entry as { text?: unknown }).text
              if ((role === "user" || role === "assistant") && typeof text === "string") {
                return { role, text } as TranscriptItem
              }
              return null
            })
            .filter((entry): entry is TranscriptItem => entry !== null)
        : []

      rows.push({
        sessionId,
        title: typeof item.title === "string" && item.title.trim() ? item.title : `Session ${sessionId.slice(0, 8)}`,
        status: typeof item.status === "string" ? item.status : "unknown",
        updatedAt: typeof item.updatedAt === "string" ? item.updatedAt : new Date(0).toISOString(),
        turnCount: typeof item.turnCount === "number" ? item.turnCount : 0,
        completedTurnCount: typeof item.completedTurnCount === "number" ? item.completedTurnCount : 0,
        lastUserText: typeof item.lastUserText === "string" ? item.lastUserText : null,
        lastAssistantText: typeof item.lastAssistantText === "string" ? item.lastAssistantText : null,
        transcript,
      })
    }

    rows.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    return rows.slice(0, SESSION_HISTORY_LIMIT)
  } catch {
    return []
  }
}

export function writeStoredSessionHistory(items: any[]): void {
  const payload = items.slice(0, SESSION_HISTORY_LIMIT)
  localStorage.setItem(LOCAL_SESSION_HISTORY_STORAGE_KEY, JSON.stringify(payload))
}

export function dedupeAndSortHistory(items: any[]): any[] {
  const bySessionId = new Map()
  for (const item of items) {
    const existing = bySessionId.get(item.sessionId)
    if (!existing) {
      bySessionId.set(item.sessionId, item)
      continue
    }
    const keepIncoming = item.updatedAt.localeCompare(existing.updatedAt) >= 0
    bySessionId.set(item.sessionId, keepIncoming ? item : existing)
  }
  return Array.from(bySessionId.values())
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt))
    .slice(0, SESSION_HISTORY_LIMIT)
}

export function latestTranscriptByRole(transcript: TranscriptItem[], role: TranscriptItem["role"]): string | null {
  for (let index = transcript.length - 1; index >= 0; index -= 1) {
    const item = transcript[index]
    if (item.role === role && item.text.trim()) {
      return item.text
    }
  }
  return null
}

export function transcriptFromHistoryTurns(turns: Array<{
  turn_id: string
  user_text?: string | null
  assistant_text?: string | null
}>): TranscriptItem[] {
  const transcript: TranscriptItem[] = []
  for (const turn of turns) {
    if (typeof turn.user_text === "string" && turn.user_text.trim()) {
      transcript.push({ role: "user", text: turn.user_text })
    }
    if (typeof turn.assistant_text === "string" && turn.assistant_text.trim()) {
      transcript.push({ role: "assistant", text: turn.assistant_text })
    }
  }
  if (transcript.length <= SESSION_TRANSCRIPT_LIMIT) {
    return transcript
  }
  return transcript.slice(-SESSION_TRANSCRIPT_LIMIT)
}

export function formatEventForPanel(event: ConversationEvent): string {
  if (event.type !== "tts.chunk") {
    return JSON.stringify(event, null, 2)
  }

  const chunk = event.chunk as {
    data_base64?: unknown
    encoding?: unknown
    sample_rate_hz?: unknown
    channels?: unknown
    sequence?: unknown
    duration_ms?: unknown
  }
  const raw = typeof chunk.data_base64 === "string" ? chunk.data_base64 : null
  const payload = {
    ...event,
    chunk: {
      ...chunk,
      data_base64: raw ? "[omitted]" : chunk.data_base64,
      data_base64_bytes: raw ? Math.floor((raw.length * 3) / 4) : undefined,
    },
  }
  return JSON.stringify(payload, null, 2)
}
