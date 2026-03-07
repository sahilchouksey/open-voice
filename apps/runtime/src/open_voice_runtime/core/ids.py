from __future__ import annotations

from uuid import uuid4


def _prefixed_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def new_session_id() -> str:
    return _prefixed_id("sess")


def new_turn_id() -> str:
    return _prefixed_id("turn")


def new_event_id() -> str:
    return _prefixed_id("evt")
