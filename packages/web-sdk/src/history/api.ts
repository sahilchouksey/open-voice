import type { SessionHistoryEntry, SessionState, SessionTurnEntry } from "../protocol"
import { RuntimeHttpClient } from "../transport/http"

export interface SessionListOptions {
  limit?: number
}

export interface SessionTurnListOptions {
  limit?: number
}

export class VoiceHistoryApi {
  constructor(private readonly httpClient: RuntimeHttpClient) {}

  listSessions(options: SessionListOptions = {}): Promise<SessionHistoryEntry[]> {
    return this.httpClient.listSessions(options.limit)
  }

  getSession(sessionId: string): Promise<SessionState> {
    return this.httpClient.getSession(sessionId)
  }

  listSessionTurns(
    sessionId: string,
    options: SessionTurnListOptions = {},
  ): Promise<SessionTurnEntry[]> {
    return this.httpClient.listSessionTurns(sessionId, options.limit)
  }

  closeSession(sessionId: string): Promise<void> {
    return this.httpClient.closeSession(sessionId)
  }
}
