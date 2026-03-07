from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LlmRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class LlmEventKind(str, Enum):
    TOKEN = "token"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class LlmCapabilities:
    streaming: bool = True
    tool_calls: bool = False
    provider_managed_sessions: bool = True


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(slots=True)
class LlmMessage:
    role: LlmRole
    content: str
    name: str | None = None


@dataclass(slots=True)
class LlmRequest:
    session_id: str
    turn_id: str
    messages: list[LlmMessage]
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LlmResponse:
    text: str
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LlmEvent:
    kind: LlmEventKind
    text: str
    index: int | None = None
    usage: TokenUsage | None = None
