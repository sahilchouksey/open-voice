from __future__ import annotations

from collections.abc import Mapping

from open_voice_runtime.core.errors import SessionStateError
from open_voice_runtime.session.models import (
    SessionState,
    SessionStatus,
    SessionTransition,
    utc_now,
)


ALLOWED_SESSION_TRANSITIONS: Mapping[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.CREATED: frozenset(
        {SessionStatus.LOADING, SessionStatus.CLOSED, SessionStatus.FAILED}
    ),
    SessionStatus.LOADING: frozenset(
        {SessionStatus.READY, SessionStatus.FAILED, SessionStatus.CLOSED}
    ),
    SessionStatus.READY: frozenset(
        {
            SessionStatus.LISTENING,
            SessionStatus.THINKING,
            SessionStatus.CLOSED,
            SessionStatus.FAILED,
        }
    ),
    SessionStatus.LISTENING: frozenset(
        {
            SessionStatus.THINKING,
            SessionStatus.INTERRUPTED,
            SessionStatus.CLOSED,
            SessionStatus.FAILED,
        }
    ),
    SessionStatus.THINKING: frozenset(
        {
            SessionStatus.LISTENING,
            SessionStatus.SPEAKING,
            SessionStatus.INTERRUPTED,
            SessionStatus.CLOSED,
            SessionStatus.FAILED,
        }
    ),
    SessionStatus.SPEAKING: frozenset(
        {
            SessionStatus.LISTENING,
            SessionStatus.INTERRUPTED,
            SessionStatus.CLOSED,
            SessionStatus.FAILED,
        }
    ),
    SessionStatus.INTERRUPTED: frozenset(
        {
            SessionStatus.LISTENING,
            SessionStatus.THINKING,
            SessionStatus.CLOSED,
            SessionStatus.FAILED,
        }
    ),
    SessionStatus.CLOSED: frozenset(),
    SessionStatus.FAILED: frozenset({SessionStatus.CLOSED}),
}


def allowed_transitions(status: SessionStatus) -> frozenset[SessionStatus]:
    return ALLOWED_SESSION_TRANSITIONS[status]


def can_transition(current: SessionStatus, target: SessionStatus) -> bool:
    return target in allowed_transitions(current)


def transition_session(state: SessionState, transition: SessionTransition) -> SessionState:
    if not can_transition(state.status, transition.to_status):
        raise SessionStateError(
            "Invalid session transition.",
            details={
                "from": state.status.value,
                "to": transition.to_status.value,
                "allowed": sorted(item.value for item in allowed_transitions(state.status)),
                "reason": transition.reason,
            },
        )

    state.status = transition.to_status
    state.updated_at = utc_now()
    state.metadata.update(transition.metadata)
    if transition.reason is not None:
        state.metadata["last_transition_reason"] = transition.reason
    return state
