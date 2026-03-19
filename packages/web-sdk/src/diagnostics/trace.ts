import type { ConversationEvent } from "../protocol"

export interface TraceRecord {
  ts: string
  mono_ns: number
  source: "demo-frontend"
  session_id: string
  turn_id?: string | null
  generation_id?: string | null
  dir: "in" | "out" | "local"
  kind: string
  type: string
  payload: unknown
}

export interface TraceReporterOptions {
  runtimeBaseUrl: string
  sessionId?: string | null
  enabled?: boolean
  flushIntervalMs?: number
  maxBatchSize?: number
}

function nowIso(): string {
  return new Date().toISOString()
}

function monoNs(): number {
  if (typeof performance !== "undefined" && typeof performance.now === "function") {
    return Math.floor(performance.now() * 1e6)
  }
  return Date.now() * 1_000_000
}

function sanitizePayload(payload: unknown): unknown {
  if (!payload || typeof payload !== "object") {
    return payload
  }
  const data = payload as { type?: unknown; chunk?: unknown }
  if (!data.chunk || typeof data.chunk !== "object") {
    return payload
  }
  const chunk = data.chunk as { data_base64?: unknown }
  if (typeof chunk.data_base64 !== "string") {
    return payload
  }
  const redactedChunk = {
    ...(data.chunk as Record<string, unknown>),
    data_base64: "[omitted]",
    data_base64_bytes: Math.floor((chunk.data_base64.length * 3) / 4),
  }
  return {
    ...(payload as Record<string, unknown>),
    chunk: redactedChunk,
  }
}

export class FrontendTraceReporter {
  private readonly runtimeBaseUrl: string
  private sessionId: string | null
  private readonly enabled: boolean
  private readonly flushIntervalMs: number
  private readonly maxBatchSize: number
  private queue: TraceRecord[] = []
  private timer: number | null = null
  private inflight = false

  constructor(opts: TraceReporterOptions) {
    this.runtimeBaseUrl = opts.runtimeBaseUrl
    this.sessionId = opts.sessionId ?? null
    this.enabled = opts.enabled ?? true
    this.flushIntervalMs = opts.flushIntervalMs ?? 600
    this.maxBatchSize = opts.maxBatchSize ?? 80
  }

  setSessionId(sessionId: string): void {
    this.sessionId = sessionId
    for (const record of this.queue) {
      record.session_id = sessionId
    }
  }

  start(): void {
    if (!this.enabled || this.timer !== null) {
      return
    }
    this.timer = window.setInterval(() => {
      void this.flush(false)
    }, this.flushIntervalMs)
  }

  stop(): void {
    if (this.timer !== null) {
      window.clearInterval(this.timer)
      this.timer = null
    }
  }

  trackLocal(type: string, payload: unknown, kind = "ui.action"): void {
    if (!this.enabled) {
      return
    }
    this.push({
      ts: nowIso(),
      mono_ns: monoNs(),
      source: "demo-frontend",
      session_id: this.sessionId ?? "pending",
      dir: "local",
      kind,
      type,
      payload: sanitizePayload(payload),
    })
  }

  trackInboundEvent(event: ConversationEvent): void {
    if (!this.enabled) {
      return
    }
    if (!this.sessionId && event.session_id) {
      this.setSessionId(event.session_id)
    }
    this.push({
      ts: nowIso(),
      mono_ns: monoNs(),
      source: "demo-frontend",
      session_id: this.sessionId ?? event.session_id,
      turn_id: event.turn_id,
      generation_id: event.generation_id,
      dir: "in",
      kind: "ws.message",
      type: event.type,
      payload: sanitizePayload(event),
    })
  }

  trackOutboundMessage(type: string, payload: unknown): void {
    if (!this.enabled) {
      return
    }
    if (!this.sessionId && payload && typeof payload === "object") {
      const maybeSessionId = (payload as { session_id?: unknown }).session_id
      if (typeof maybeSessionId === "string") {
        this.setSessionId(maybeSessionId)
      }
    }
    this.push({
      ts: nowIso(),
      mono_ns: monoNs(),
      source: "demo-frontend",
      session_id: this.sessionId ?? "pending",
      dir: "out",
      kind: "ws.message",
      type,
      payload: sanitizePayload(payload),
    })
  }

  private push(record: TraceRecord): void {
    this.queue.push(record)
    if (this.queue.length >= this.maxBatchSize) {
      void this.flush(false)
    }
  }

  async flush(useBeacon: boolean): Promise<void> {
    if (!this.enabled || this.inflight || this.queue.length === 0 || !this.sessionId) {
      return
    }

    this.inflight = true
    const batch = this.queue.splice(0, this.maxBatchSize)
    try {
      const endpoint = `${this.runtimeBaseUrl.replace(/\/$/, "")}/v1/diagnostics/trace/frontend`
      const body = JSON.stringify({
        session_id: this.sessionId,
        records: batch,
      })

      if (useBeacon && typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
        const blob = new Blob([body], { type: "application/json" })
        navigator.sendBeacon(endpoint, blob)
      } else {
        await fetch(endpoint, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body,
          keepalive: true,
        })
      }
    } catch {
      this.queue.unshift(...batch)
    } finally {
      this.inflight = false
    }
  }
}
