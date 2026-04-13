import { BrowserMicInput as SdkBrowserMicInput, computeAnalyserBands } from "@open-voice/web-sdk"
import type { TtsChunk, AudioOutputAdapter } from "@open-voice/web-sdk"
import {
  AUDIO_BAND_COUNT,
  DEMO_CAPTURE_BUFFER_SIZE,
  DEMO_UI_BAND_INTERVAL_MS,
} from "../constants/config"
import { zeroBands } from "../utils"

export class ThinkingAudioPlayer {
  private readonly audio: HTMLAudioElement
  private readonly gapMs: number
  private restartTimer: number | null = null
  private playing = false

  constructor(url: string, opts?: { gapMs?: number; volume?: number }) {
    this.audio = new Audio(url)
    this.audio.preload = "auto"
    this.audio.volume = opts?.volume ?? 0.2
    this.gapMs = opts?.gapMs ?? 180
    this.audio.addEventListener("ended", () => {
      if (!this.playing) return
      this.clearTimer()
      this.restartTimer = window.setTimeout(() => {
        if (!this.playing) return
        this.audio.currentTime = 0
        void this.audio.play().catch(() => undefined)
      }, this.gapMs)
    })
  }

  private clearTimer() {
    if (this.restartTimer !== null) {
      window.clearTimeout(this.restartTimer)
      this.restartTimer = null
    }
  }

  async start() {
    if (this.playing) return
    this.playing = true
    this.audio.currentTime = 0
    await this.audio.play().catch(() => {
      this.playing = false
    })
  }

  stop() {
    this.playing = false
    this.clearTimer()
    this.audio.pause()
    this.audio.currentTime = 0
  }

  isPlaying() {
    return this.playing
  }
}

export class DemoMicInput {
  private readonly mic: SdkBrowserMicInput
  private running = false
  private captureToken = 0

  constructor(
    sendChunk: (chunk: {
      data: ArrayBuffer
      sequence: number
      encoding: "pcm_s16le"
      sampleRateHz: number
      channels: number
      durationMs: number
    }) => void | Promise<void>,
    onLevel: (value: number) => void,
    onBands: (bands: number[]) => void,
    onChunkMeta?: (meta: {
      sequence: number
      sampleRateHz: number
      channels: number
      durationMs: number
      bytes: number
    }) => void,
  ) {
    this.mic = new SdkBrowserMicInput({
      sampleRateHz: 16000,
      channels: 1,
      chunkSize: DEMO_CAPTURE_BUFFER_SIZE,
      analyserBandCount: AUDIO_BAND_COUNT,
      analyserBandIntervalMs: DEMO_UI_BAND_INTERVAL_MS,
      analyserFftSize: 2048,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      onLevel,
      onBands,
      onChunkMeta,
    })
    this.sendChunk = sendChunk
  }

  private readonly sendChunk: (chunk: {
    data: ArrayBuffer
    sequence: number
    encoding: "pcm_s16le"
    sampleRateHz: number
    channels: number
    durationMs: number
  }) => void | Promise<void>

  async start(): Promise<void> {
    const captureToken = ++this.captureToken
    this.running = true
    await this.mic.start(async (chunk) => {
      if (!this.running || captureToken !== this.captureToken) {
        return
      }
      await this.sendChunk({
        data: chunk.data,
        sequence: chunk.sequence,
        encoding: "pcm_s16le",
        sampleRateHz: chunk.sampleRateHz,
        channels: chunk.channels,
        durationMs: chunk.durationMs ?? 0,
      })
    })
  }

  async stop(): Promise<void> {
    this.running = false
    this.captureToken += 1
    await this.mic.stop()
  }
}

class PcmLevelTap {
  private ctx: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private gain: GainNode | null = null
  private bandTimer: number | null = null

  constructor(private readonly onBands: (bands: number[]) => void, private readonly bandCount = 7) {
    this.onBands(zeroBands(this.bandCount))
  }

  private ensureContext(sampleRateHz: number) {
    if (!this.ctx || this.ctx.state === "closed") {
      this.ctx = new AudioContext({ sampleRate: sampleRateHz })
      this.analyser = this.ctx.createAnalyser()
      this.analyser.fftSize = 256
      this.analyser.smoothingTimeConstant = 0

      this.gain = this.ctx.createGain()
      this.gain.gain.value = 0
      this.gain.connect(this.analyser)
      this.analyser.connect(this.ctx.destination)

      this.bandTimer = window.setInterval(() => {
        if (!this.analyser) return
        this.onBands(computeAnalyserBands(this.analyser, this.bandCount))
      }, DEMO_UI_BAND_INTERVAL_MS)
    }
  }

  async appendPcm16(data: Uint8Array, sampleRateHz: number): Promise<void> {
    this.ensureContext(sampleRateHz)
    if (!this.ctx || !this.gain) return
    if (this.ctx.state === "suspended") {
      await this.ctx.resume()
    }

    const int16 = new Int16Array(data.buffer, data.byteOffset, data.byteLength / 2)
    const buffer = this.ctx.createBuffer(1, int16.length, this.ctx.sampleRate)
    const channel = buffer.getChannelData(0)
    for (let i = 0; i < int16.length; i += 1) {
      channel[i] = int16[i] / 0x8000
    }

    const source = this.ctx.createBufferSource()
    source.buffer = buffer
    source.connect(this.gain)
    source.start()
  }

  async close(): Promise<void> {
    if (this.bandTimer !== null) {
      window.clearInterval(this.bandTimer)
      this.bandTimer = null
    }
    this.gain?.disconnect()
    this.analyser?.disconnect()
    this.gain = null
    this.analyser = null
    if (this.ctx) {
      await this.ctx.close()
      this.ctx = null
    }
    this.onBands(zeroBands(this.bandCount))
  }
}

export class VisualizedPcmPlayer implements AudioOutputAdapter {
  private readonly tap: PcmLevelTap
  private audioContext: AudioContext | null = null
  private nextStartTime = 0
  private activeSources = new Set<AudioBufferSourceNode>()

  constructor(
    onBands: (bands: number[]) => void,
    private readonly onPlaybackActiveChange: (active: boolean) => void,
  ) {
    this.tap = new PcmLevelTap(onBands, AUDIO_BAND_COUNT)
  }

  private markPlaybackActive(active: boolean) {
    console.log("[AudioOutput] markPlaybackActive:", active)
    this.onPlaybackActiveChange(active)
  }

  private async ensureContext(sampleRateHz: number) {
    if (!this.audioContext || this.audioContext.state === "closed") {
      this.audioContext = new AudioContext({ sampleRate: sampleRateHz })
      this.nextStartTime = this.audioContext.currentTime
    }
    if (this.audioContext.state === "suspended") {
      await this.audioContext.resume()
    }
  }

  async appendTtsChunk(chunk: TtsChunk): Promise<void> {
    console.log("[AudioOutput] appendTtsChunk called, data length:", chunk.data.byteLength, "sampleRate:", chunk.sampleRateHz)
    await this.ensureContext(chunk.sampleRateHz)
    if (!this.audioContext) {
      console.log("[AudioOutput] no audioContext, returning early")
      return
    }

    const sampleRate = chunk.sampleRateHz
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
    this.nextStartTime = when + buffer.duration

    this.activeSources.add(source)
    this.markPlaybackActive(true)

    source.addEventListener("ended", () => {
      this.activeSources.delete(source)
      if (this.activeSources.size === 0) {
        this.markPlaybackActive(false)
      }
    })

    source.start(when)
    await this.tap.appendPcm16(chunk.data, chunk.sampleRateHz)
  }

  async flush(reason?: string): Promise<void> {
    console.log("[AudioOutput] flush called, reason:", reason, "activeSources:", this.activeSources.size)
    for (const source of this.activeSources) {
      try {
        source.stop()
      } catch {
        // Ignore stop errors for already-ended nodes.
      }
      source.disconnect()
    }
    this.activeSources.clear()
    this.markPlaybackActive(false)

    if (this.audioContext) {
      await this.audioContext.close()
      this.audioContext = null
    }
    this.nextStartTime = 0

    await this.tap.close()
  }
}
