from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeAlias

from open_voice_runtime.session.models import EngineSelection


class ClientMessageType(str, Enum):
    SESSION_START = "session.start"
    AUDIO_APPEND = "audio.append"
    AUDIO_COMMIT = "audio.commit"
    USER_TURN_COMMIT = "user_turn.commit"
    AGENT_SAY = "agent.say"
    AGENT_GENERATE_REPLY = "agent.generate_reply"
    CONVERSATION_INTERRUPT = "conversation.interrupt"
    ENGINE_SELECT = "engine.select"
    CONFIG_UPDATE = "config.update"
    SESSION_CLOSE = "session.close"


class AudioTransport(str, Enum):
    INLINE_BASE64 = "inline-base64"
    BINARY_FRAME = "binary-frame"


@dataclass(slots=True)
class AudioChunkPayload:
    chunk_id: str
    sequence: int
    encoding: str
    sample_rate_hz: int
    channels: int
    duration_ms: float | None = None
    transport: AudioTransport = AudioTransport.INLINE_BASE64
    data_base64: str | None = None


@dataclass(slots=True)
class SessionStartMessage:
    type: ClientMessageType = ClientMessageType.SESSION_START
    session_id: str | None = None
    engine_selection: EngineSelection = field(default_factory=EngineSelection)
    metadata: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AudioAppendMessage:
    session_id: str
    chunk: AudioChunkPayload
    type: ClientMessageType = ClientMessageType.AUDIO_APPEND


@dataclass(slots=True)
class AudioCommitMessage:
    session_id: str
    sequence: int | None = None
    type: ClientMessageType = ClientMessageType.AUDIO_COMMIT


@dataclass(slots=True)
class UserTurnCommitMessage:
    session_id: str
    sequence: int | None = None
    type: ClientMessageType = ClientMessageType.USER_TURN_COMMIT


@dataclass(slots=True)
class AgentSayMessage:
    session_id: str
    text: str
    type: ClientMessageType = ClientMessageType.AGENT_SAY


@dataclass(slots=True)
class AgentGenerateReplyMessage:
    session_id: str
    user_text: str
    instructions: str | None = None
    allow_interruptions: bool | None = None
    type: ClientMessageType = ClientMessageType.AGENT_GENERATE_REPLY


@dataclass(slots=True)
class ConversationInterruptMessage:
    session_id: str
    reason: str | None = None
    type: ClientMessageType = ClientMessageType.CONVERSATION_INTERRUPT


@dataclass(slots=True)
class EngineSelectMessage:
    session_id: str
    engine_selection: EngineSelection
    type: ClientMessageType = ClientMessageType.ENGINE_SELECT


@dataclass(slots=True)
class ConfigUpdateMessage:
    session_id: str
    config: dict[str, Any]
    type: ClientMessageType = ClientMessageType.CONFIG_UPDATE


@dataclass(slots=True)
class SessionCloseMessage:
    session_id: str
    type: ClientMessageType = ClientMessageType.SESSION_CLOSE


ClientMessage: TypeAlias = (
    SessionStartMessage
    | AudioAppendMessage
    | AudioCommitMessage
    | UserTurnCommitMessage
    | AgentSayMessage
    | AgentGenerateReplyMessage
    | ConversationInterruptMessage
    | EngineSelectMessage
    | ConfigUpdateMessage
    | SessionCloseMessage
)
