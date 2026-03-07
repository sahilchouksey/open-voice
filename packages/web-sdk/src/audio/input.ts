export type AudioEncoding = "pcm_s16le" | "pcm_f32le"

export interface InputAudioChunk {
  data: ArrayBuffer
  sequence: number
  encoding: AudioEncoding
  sampleRateHz: number
  channels: number
  durationMs?: number
}

export type AudioChunkHandler = (chunk: InputAudioChunk) => void | Promise<void>

export interface AudioInputAdapter {
  start(handler: AudioChunkHandler): Promise<void>
  stop(): Promise<void>
}

export interface AudioInputConfig {
  sampleRateHz: number
  channels: number
  encoding: AudioEncoding
  chunkDurationMs: number
}
