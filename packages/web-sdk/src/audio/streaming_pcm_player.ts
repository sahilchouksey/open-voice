import type { AudioOutputAdapter, TtsChunk } from "../interruption/audio_output"

export interface StreamingPcmPlayerOptions {
  outputSampleRateHz?: number
}

export class StreamingPcmPlayer implements AudioOutputAdapter {
  private audioContext: AudioContext | null = null
  private nextStartTime = 0
  private readonly outputSampleRateHz?: number

  constructor(opts: StreamingPcmPlayerOptions = {}) {
    this.outputSampleRateHz = opts.outputSampleRateHz
  }

  async appendTtsChunk(chunk: TtsChunk): Promise<void> {
    const sampleRate = this.outputSampleRateHz ?? chunk.sampleRateHz
    if (!this.audioContext || this.audioContext.state === "closed") {
      this.audioContext = new AudioContext({ sampleRate })
      this.nextStartTime = this.audioContext.currentTime
    }
    if (this.audioContext.state === "suspended") {
      await this.audioContext.resume()
    }

    const int16 = new Int16Array(
      chunk.data.buffer,
      chunk.data.byteOffset,
      chunk.data.byteLength / 2,
    )
    const buffer = this.audioContext.createBuffer(1, int16.length, sampleRate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < int16.length; i += 1) {
      channel[i] = int16[i] / 0x8000
    }

    const source = this.audioContext.createBufferSource()
    source.buffer = buffer
    source.connect(this.audioContext.destination)
    const when = Math.max(this.nextStartTime, this.audioContext.currentTime + 0.01)
    source.start(when)
    this.nextStartTime = when + buffer.duration
  }

  async flush(): Promise<void> {
    if (this.audioContext) {
      await this.audioContext.close()
      this.audioContext = null
    }
    this.nextStartTime = 0
  }
}
