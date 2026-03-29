export interface SttTrackState {
  turnId: string | null
  generationId: string | null
}

export interface SttTrackRotationOptions {
  canRotate: boolean
  relaxedRotation?: boolean
}

export class SttTrackStateManager {
  private state: SttTrackState = {
    turnId: null,
    generationId: null,
  }

  getState(): SttTrackState {
    return { ...this.state }
  }

  reset(): void {
    this.state.turnId = null
    this.state.generationId = null
  }

  checkAndUpdate(
    partialTurnId: string | null,
    partialGenerationId: string | null,
    options: SttTrackRotationOptions
  ): boolean {
    const { canRotate, relaxedRotation = false } = options
    const allowRotation = canRotate || relaxedRotation

    if (
      this.state.turnId &&
      partialTurnId &&
      partialTurnId !== this.state.turnId
    ) {
      if (!allowRotation) {
        return false
      }
      this.state.turnId = null
      this.state.generationId = null
    }

    if (
      this.state.generationId &&
      partialGenerationId &&
      partialGenerationId !== this.state.generationId
    ) {
      if (!allowRotation) {
        return false
      }
      this.state.turnId = null
      this.state.generationId = null
    }

    if (!this.state.turnId && partialTurnId) {
      this.state.turnId = partialTurnId
    }
    if (!this.state.generationId && partialGenerationId) {
      this.state.generationId = partialGenerationId
    }

    return true
  }
}
