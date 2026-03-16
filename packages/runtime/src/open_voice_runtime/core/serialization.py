from __future__ import annotations

import base64
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from open_voice_runtime.audio.types import AudioChunk, AudioFormat


def to_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, AudioFormat):
        return {
            "sample_rate_hz": value.sample_rate_hz,
            "channels": value.channels,
            "encoding": value.encoding.value,
        }
    if isinstance(value, AudioChunk):
        return {
            "data_base64": base64.b64encode(value.data).decode("ascii"),
            "encoding": value.format.encoding.value,
            "sample_rate_hz": value.format.sample_rate_hz,
            "channels": value.format.channels,
            "sequence": value.sequence,
            "duration_ms": value.duration_ms,
        }
    if is_dataclass(value):
        return {field.name: to_json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [to_json_value(item) for item in value]
    return value
