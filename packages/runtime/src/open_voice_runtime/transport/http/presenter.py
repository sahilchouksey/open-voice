from __future__ import annotations

from typing import Any

from open_voice_runtime.core.registry import EngineDescriptor
from open_voice_runtime.core.serialization import to_json_value
from open_voice_runtime.session.models import SessionState


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
