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
    PHASE = "phase"
    REASONING_DELTA = "reasoning_delta"
    RESPONSE_DELTA = "response_delta"
    TOOL_UPDATE = "tool_update"
    USAGE = "usage"
    SUMMARY = "summary"
    COMPLETED = "completed"


class LlmPhase(str, Enum):
    THINKING = "thinking"
    GENERATING = "generating"
    DONE = "done"


class LlmOutputLane(str, Enum):
    SPEECH = "speech"
    DISPLAY = "display"


class LlmToolKind(str, Enum):
    FUNCTION = "function"
    MCP = "mcp"


@dataclass(frozen=True, slots=True)
class LlmCapabilities:
    streaming: bool = True
    tool_calls: bool = False
    provider_managed_sessions: bool = True


@dataclass(slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LlmToolDefinition:
    name: str
    description: str | None = None
    kind: LlmToolKind = LlmToolKind.FUNCTION
    parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LlmSessionConfig:
    system_prompt: str | None = None
    additional_instructions: str | None = None
    opencode_mode: str | None = None
    opencode_force_system_override: bool = False
    tools: tuple[LlmToolDefinition, ...] = ()
    enable_fast_ack: bool = True


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
    system_prompt: str | None = None
    tools: tuple[LlmToolDefinition, ...] = ()
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LlmResponse:
    text: str
    finish_reason: str | None = None
    provider: str | None = None
    model: str | None = None
    cost: float | None = None
    usage: TokenUsage | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LlmEvent:
    kind: LlmEventKind
    text: str = ""
    index: int | None = None
    part_id: str | None = None
    lane: LlmOutputLane | None = None
    phase: LlmPhase | None = None
    call_id: str | None = None
    tool_name: str | None = None
    tool_input: Any | None = None
    tool_metadata: dict[str, Any] = field(default_factory=dict)
    tool_output: Any | None = None
    tool_error: Any | None = None
    provider: str | None = None
    model: str | None = None
    cost: float | None = None
    finish_reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    usage: TokenUsage | None = None
