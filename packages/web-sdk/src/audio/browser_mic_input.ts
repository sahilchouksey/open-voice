import type { AudioChunkHandler, InputAudioChunk } from "./input"

export interface BrowserMicInputOptions {
  sampleRateHz?: number
  channels?: number
  chunkSize?: number
  analyserBandCount?: number
  analyserBandIntervalMs?: number
  analyserFftSize?: number
  echoCancellation?: boolean
  noiseSuppression?: boolean
  autoGainControl?: boolean
  onLevel?: (level: number) => void
  onBands?: (bands: number[]) => void
  onChunkMeta?: (meta: BrowserMicChunkMeta) => void
}

export interface BrowserMicChunkMeta {
  sequence: number
  sampleRateHz: number
  channels: number
  durationMs: number
  bytes: number
}

function zeroBands(count: number): number[] {
  return Array.from({ length: count }, () => 0)
}

function normalizeDbValue(value: number): number {
  const minDb = -100
  const maxDb = -10
  const clamped = Math.max(minDb, Math.min(maxDb, value))
  const normalized = 1 - (clamped * -1) / 100
  return Math.sqrt(normalized)
}

export function computeAnalyserBands(analyser: AnalyserNode, bands: number): number[] {
  const dataArray = new Float32Array(analyser.frequencyBinCount)
  analyser.getFloatFrequencyData(dataArray)

  const normalized = dataArray.map((value) => {
    if (value === -Infinity) {
      return 0
    }
    return normalizeDbValue(value)
  })

  const totalBins = normalized.length
  const chunks: number[] = []

  for (let i = 0; i < bands; i += 1) {
    const startIndex = Math.floor((i * totalBins) / bands)
    const endIndex = Math.floor(((i + 1) * totalBins) / bands)
    const chunk = normalized.slice(startIndex, endIndex)
    if (chunk.length === 0) {
      chunks.push(0)
      continue
    }
    const avg = chunk.reduce((acc, value) => acc + value, 0) / chunk.length
    chunks.push(avg)
  }

  return chunks
}

export class BrowserMicInput {
  private sequence = 0
  private ctx: AudioContext | null = null
  private processor: ScriptProcessorNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private stream: MediaStream | null = null
  private analyser: AnalyserNode | null = null
  private bandTimer: number | null = null
  private running = false
  private captureToken = 0
  private readonly sampleRateHz: number
  private readonly channels: number
  private readonly chunkSize: number
  private readonly analyserBandCount: number
  private readonly analyserBandIntervalMs: number
  private readonly analyserFftSize: number
  private readonly echoCancellation: boolean
  private readonly noiseSuppression: boolean
  private readonly autoGainControl: boolean
  private readonly onLevel?: (level: number) => void
  private readonly onBands?: (bands: number[]) => void
  private readonly onChunkMeta?: (meta: BrowserMicChunkMeta) => void

  constructor(options: BrowserMicInputOptions = {}) {
    this.sampleRateHz = options.sampleRateHz ?? 24000
    this.channels = options.channels ?? 1
    this.chunkSize = options.chunkSize ?? 4096
    this.analyserBandCount = options.analyserBandCount ?? 9
    this.analyserBandIntervalMs = options.analyserBandIntervalMs ?? 80
    this.analyserFftSize = options.analyserFftSize ?? 2048
    this.echoCancellation = options.echoCancellation ?? true
    this.noiseSuppression = options.noiseSuppression ?? true
    this.autoGainControl = options.autoGainControl ?? true
    this.onLevel = options.onLevel
    this.onBands = options.onBands
    this.onChunkMeta = options.onChunkMeta
    this.onBands?.(zeroBands(this.analyserBandCount))
  }

  async start(handler: AudioChunkHandler): Promise<void> {
    const captureToken = ++this.captureToken
    this.running = true
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: { ideal: this.sampleRateHz },
        channelCount: this.channels,
        echoCancellation: this.echoCancellation,
        noiseSuppression: this.noiseSuppression,
        autoGainControl: this.autoGainControl,
      },
    })

    this.ctx = new AudioContext({ sampleRate: this.sampleRateHz })
    this.source = this.ctx.createMediaStreamSource(this.stream)
    this.processor = this.ctx.createScriptProcessor(this.chunkSize, this.channels, this.channels)
    this.analyser = this.ctx.createAnalyser()
    this.analyser.fftSize = this.analyserFftSize
    this.analyser.smoothingTimeConstant = 0

    this.processor.onaudioprocess = (event) => {
      if (!this.running || captureToken !== this.captureToken) {
        return
      }
      const channel = event.inputBuffer.getChannelData(0)
      let peak = 0
      const pcm = new Int16Array(channel.length)
      for (let i = 0; i < channel.length; i += 1) {
        const sample = Math.max(-1, Math.min(1, channel[i]))
        peak = Math.max(peak, Math.abs(sample))
        pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
      }

      if (!this.running || captureToken !== this.captureToken) {
        return
      }

      const sampleRate = this.ctx?.sampleRate ?? this.sampleRateHz
      const chunk: InputAudioChunk = {
        data: pcm.buffer,
        sequence: this.sequence,
        encoding: "pcm_s16le",
        sampleRateHz: sampleRate,
        channels: this.channels,
        durationMs: (pcm.length / sampleRate) * 1000,
      }

      this.sequence += 1
      this.onLevel?.(peak)
      this.onChunkMeta?.({
        sequence: chunk.sequence,
        sampleRateHz: chunk.sampleRateHz,
        channels: chunk.channels,
        durationMs: chunk.durationMs ?? 0,
        bytes: pcm.byteLength,
      })
      void Promise.resolve(handler(chunk)).catch(() => undefined)
    }

    this.source.connect(this.processor)
    this.source.connect(this.analyser)
    this.processor.connect(this.ctx.destination)

    if (this.onBands) {
      this.bandTimer = window.setInterval(() => {
        if (!this.analyser) {
          return
        }
        this.onBands?.(computeAnalyserBands(this.analyser, this.analyserBandCount))
      }, this.analyserBandIntervalMs)
    }
  }

  async stop(): Promise<void> {
    this.running = false
    this.captureToken += 1
    if (this.bandTimer !== null) {
      window.clearInterval(this.bandTimer)
      this.bandTimer = null
    }
    if (this.processor) {
      this.processor.onaudioprocess = null
    }
    this.processor?.disconnect()
    this.analyser?.disconnect()
    this.source?.disconnect()
    this.stream?.getTracks().forEach((track) => track.stop())
    await this.ctx?.close()
    this.processor = null
    this.analyser = null
    this.source = null
    this.stream = null
    this.ctx = null
    this.onLevel?.(0)
    this.onBands?.(zeroBands(this.analyserBandCount))
  }
}
