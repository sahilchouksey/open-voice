export interface EventSequencerOptions {
  debug?: boolean
}

const DEBUG_THRESHOLD_MS = 20
const DEBUG_PREFIX = "[OV-DEBUG] EventSequencer"

export class EventSequencer {
  private chain: Promise<void> = Promise.resolve()
  private readonly debug: boolean

  constructor(opts: EventSequencerOptions = {}) {
    this.debug = opts.debug ?? false
  }

  push(task: () => void | Promise<void>): void {
    if (this.debug) {
      const enqueueTime = performance.now()
      this.chain = this.chain
        .then(async () => {
          const waitTime = performance.now() - enqueueTime
          if (waitTime > DEBUG_THRESHOLD_MS) {
            console.warn(`${DEBUG_PREFIX}: queued ${waitTime.toFixed(2)}ms`)
          }
          await task()
        })
        .catch(() => undefined)
    } else {
      this.chain = this.chain
        .then(async () => {
          await task()
        })
        .catch(() => undefined)
    }
  }

  reset(): void {
    this.chain = Promise.resolve()
  }
}
