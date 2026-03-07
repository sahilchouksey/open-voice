import type { EngineCatalogResponse, SessionState } from "../protocol"

export interface RuntimeHttpClientOptions {
  baseUrl: string
  fetch?: typeof fetch
}

export interface SessionCreateRequest {
  engine_selection?: {
    stt?: string
    router?: string
    llm?: string
    tts?: string
  }
  metadata?: Record<string, unknown>
}

export class RuntimeHttpClient {
  readonly baseUrl: string
  readonly fetcher: typeof fetch

  constructor(opts: RuntimeHttpClientOptions) {
    this.baseUrl = opts.baseUrl.replace(/\/$/, "")
    this.fetcher = opts.fetch ?? fetch
  }

  async health(): Promise<{ status: string }> {
    return this.request<{ status: string }>("/health")
  }

  async listEngines(): Promise<EngineCatalogResponse> {
    return this.request<EngineCatalogResponse>("/v1/engines")
  }

  async createSession(body: SessionCreateRequest = {}): Promise<SessionState> {
    return this.request<SessionState>("/v1/sessions", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    })
  }

  async getSession(sessionId: string): Promise<SessionState> {
    return this.request<SessionState>(`/v1/sessions/${sessionId}`)
  }

  async closeSession(sessionId: string): Promise<void> {
    await this.request<void>(`/v1/sessions/${sessionId}`, { method: "DELETE" })
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const res = await this.fetcher(`${this.baseUrl}${path}`, init)
    if (!res.ok) throw new Error(`Runtime HTTP request failed: ${res.status}`)
    if (res.status === 204) return undefined as T
    return (await res.json()) as T
  }
}
