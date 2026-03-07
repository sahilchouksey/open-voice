from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    ENGINE_NOT_FOUND = "engine_not_found"
    ENGINE_UNAVAILABLE = "engine_unavailable"
    INVALID_AUDIO_FORMAT = "invalid_audio_format"
    INVALID_SESSION_TRANSITION = "invalid_session_transition"
    SESSION_NOT_FOUND = "session_not_found"
    SESSION_CLOSED = "session_closed"
    INTERRUPTION_NOT_ALLOWED = "interruption_not_allowed"
    PROVIDER_ERROR = "provider_error"
    TRANSPORT_PROTOCOL_ERROR = "transport_protocol_error"


@dataclass(slots=True)
class OpenVoiceError(Exception):
    code: ErrorCode
    message: str
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }


class SessionStateError(OpenVoiceError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code=ErrorCode.INVALID_SESSION_TRANSITION,
            message=message,
            retryable=False,
            details=details or {},
        )


class EngineRegistryError(OpenVoiceError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code=ErrorCode.ENGINE_NOT_FOUND,
            message=message,
            retryable=False,
            details=details or {},
        )


class AudioFormatError(OpenVoiceError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code=ErrorCode.INVALID_AUDIO_FORMAT,
            message=message,
            retryable=False,
            details=details or {},
        )


class TransportProtocolError(OpenVoiceError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code=ErrorCode.TRANSPORT_PROTOCOL_ERROR,
            message=message,
            retryable=False,
            details=details or {},
        )
