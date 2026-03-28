from __future__ import annotations

from typing import Any

from open_voice_runtime.core.registry import EngineDescriptor
from open_voice_runtime.core.serialization import to_json_value
from open_voice_runtime.session.models import SessionState
from open_voice_runtime.session.models import SessionTurn


def engine_descriptor_payload(engine: EngineDescriptor) -> dict[str, Any]:
    return {
        "id": engine.id,
        "kind": engine.kind,
        "label": engine.label,
        "default": engine.default,
        "available": engine.available,
        "status": engine.status,
        "capabilities": to_json_value(engine.capabilities),
    }


def session_state_payload(state: SessionState) -> dict[str, Any]:
    return {
        "session_id": state.session_id,
        "status": state.status.value,
        "created_at": state.created_at.isoformat(),
        "updated_at": state.updated_at.isoformat(),
        "active_turn_id": state.active_turn_id,
        "engine_selection": to_json_value(state.engine_selection),
        "metadata": to_json_value(state.metadata),
    }


def session_history_entry_payload(state: SessionState) -> dict[str, Any]:
    last_user_text: str | None = None
    last_assistant_text: str | None = None
    completed_turns = 0

    for turn in state.turns:
        if turn.completed_at is not None:
            completed_turns += 1
        if turn.user_text and turn.user_text.strip():
            last_user_text = turn.user_text
        if turn.assistant_text and turn.assistant_text.strip():
            last_assistant_text = turn.assistant_text

    title = _session_title(state, last_user_text)

    return {
        "session_id": state.session_id,
        "status": state.status.value,
        "title": title,
        "created_at": state.created_at.isoformat(),
        "updated_at": state.updated_at.isoformat(),
        "active_turn_id": state.active_turn_id,
        "turn_count": len(state.turns),
        "completed_turn_count": completed_turns,
        "last_user_text": last_user_text,
        "last_assistant_text": last_assistant_text,
    }


def _session_title(state: SessionState, fallback_user_text: str | None) -> str:
    metadata_title = state.metadata.get("title") if isinstance(state.metadata, dict) else None
    if isinstance(metadata_title, str) and metadata_title.strip():
        return metadata_title.strip()

    if fallback_user_text:
        stripped = fallback_user_text.strip()
        if len(stripped) <= 80:
            return stripped
        return f"{stripped[:77].rstrip()}..."

    return f"Session {state.session_id[:8]}"


def session_turn_payload(turn: SessionTurn) -> dict[str, Any]:
    return {
        "turn_id": turn.turn_id,
        "user_text": turn.user_text,
        "assistant_text": turn.assistant_text,
        "created_at": turn.created_at.isoformat(),
        "completed_at": turn.completed_at.isoformat() if turn.completed_at is not None else None,
    }
