from __future__ import annotations

import struct

from open_voice_runtime.audio.types import AudioChunk, AudioEncoding, AudioFormat
from open_voice_runtime.core.errors import AudioFormatError


def audio_chunk_to_mono_floats(chunk: AudioChunk) -> list[float]:
    return audio_bytes_to_mono_floats(chunk.data, chunk.format)


def audio_bytes_to_mono_floats(data: bytes, format: AudioFormat) -> list[float]:
    if format.channels < 1:
        raise AudioFormatError("Audio format must declare at least one channel.")

    samples = _decode_samples(data, format.encoding)
    if len(samples) % format.channels != 0:
        raise AudioFormatError(
            "Audio payload sample count does not align with channel count.",
            details={"channels": format.channels, "sample_count": len(samples)},
        )

    if format.channels == 1:
        return samples

    result: list[float] = []
    for index in range(0, len(samples), format.channels):
        frame = samples[index : index + format.channels]
        result.append(sum(frame) / format.channels)
    return result


def _decode_samples(data: bytes, encoding: AudioEncoding) -> list[float]:
    if encoding is AudioEncoding.PCM_S16LE:
        if len(data) % 2 != 0:
            raise AudioFormatError("PCM s16le audio payload must have an even byte length.")
        values = struct.unpack(f"<{len(data) // 2}h", data)
        return [sample / 32768.0 for sample in values]

    if encoding is AudioEncoding.PCM_F32LE:
        if len(data) % 4 != 0:
            raise AudioFormatError("PCM f32le audio payload must have a 4-byte aligned length.")
        values = struct.unpack(f"<{len(data) // 4}f", data)
        return list(values)

    raise AudioFormatError(
        "Unsupported audio encoding for preprocessing.",
        details={"encoding": encoding.value},
    )
