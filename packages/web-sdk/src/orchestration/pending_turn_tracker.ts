export type PendingTurnPhase =
  | "idle"
  | "commit_sent"
  | "awaiting_backend"
  | "slow"
  | "degraded"
  | "timeout"
  | "resolved"
  | "cancelled"

export interface PendingTurnSnapshot {
  phase: PendingTurnPhase
  clientTurnId: string | null
  startedAt: number | null
  elapsedMs: number
}

export class PendingTurnTracker {
  private snapshot: PendingTurnSnapshot = {
    phase: "idle",
    clientTurnId: null,
    startedAt: null,
    elapsedMs: 0,
  }

  getSnapshot(now = Date.now()): PendingTurnSnapshot {
    if (this.snapshot.startedAt === null) {
      return this.snapshot
    }
    return {
      ...this.snapshot,
      elapsedMs: Math.max(0, now - this.snapshot.startedAt),
    }
  }

  markCommitSent(clientTurnId: string | null, now = Date.now()): void {
    this.snapshot = {
      phase: "commit_sent",
      clientTurnId,
      startedAt: now,
      elapsedMs: 0,
    }
  }

  markAwaitingBackend(now = Date.now()): void {
    if (this.snapshot.startedAt === null) {
      this.snapshot = {
        phase: "awaiting_backend",
        clientTurnId: null,
        startedAt: now,
        elapsedMs: 0,
      }
      return
    }
    this.snapshot = {
      ...this.snapshot,
      phase: "awaiting_backend",
      elapsedMs: Math.max(0, now - this.snapshot.startedAt),
    }
  }

  markResolved(now = Date.now()): void {
    if (this.snapshot.startedAt === null) {
      this.snapshot = { phase: "resolved", clientTurnId: null, startedAt: null, elapsedMs: 0 }
      return
    }
    this.snapshot = {
      ...this.snapshot,
      phase: "resolved",
      elapsedMs: Math.max(0, now - this.snapshot.startedAt),
    }
  }

  markCancelled(now = Date.now()): void {
    if (this.snapshot.startedAt === null) {
      this.snapshot = { phase: "cancelled", clientTurnId: null, startedAt: null, elapsedMs: 0 }
      return
    }
    this.snapshot = {
      ...this.snapshot,
      phase: "cancelled",
      elapsedMs: Math.max(0, now - this.snapshot.startedAt),
    }
  }

  reset(): void {
    this.snapshot = {
      phase: "idle",
      clientTurnId: null,
      startedAt: null,
      elapsedMs: 0,
    }
  }
}
