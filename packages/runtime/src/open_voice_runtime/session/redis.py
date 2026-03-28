from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from open_voice_runtime.core.errors import ErrorCode, OpenVoiceError
from open_voice_runtime.session.manager import SessionManager
from open_voice_runtime.session.models import (
    EngineSelection,
    SessionCreateRequest,
    SessionState,
    SessionStatus,
    SessionTransition,
    SessionTurn,
)
from open_voice_runtime.session.state_machine import transition_session

logger = logging.getLogger(__name__)


def _serialize_state(state: SessionState) -> str:
    return json.dumps(
        {
            "session_id": state.session_id,
            "status": state.status.value,
            "created_at": state.created_at.isoformat(),
            "updated_at": state.updated_at.isoformat(),
            "active_turn_id": state.active_turn_id,
            "engine_selection": {
                "stt": state.engine_selection.stt,
                "router": state.engine_selection.router,
                "llm": state.engine_selection.llm,
                "tts": state.engine_selection.tts,
            },
            "turns": [
                {
                    "turn_id": t.turn_id,
                    "user_text": t.user_text,
                    "assistant_text": t.assistant_text,
                    "created_at": t.created_at.isoformat(),
                    "completed_at": (t.completed_at.isoformat() if t.completed_at else None),
                }
                for t in state.turns
            ],
            "metadata": state.metadata,
        },
        ensure_ascii=False,
    )


def _deserialize_state(data: str) -> SessionState:
    payload = json.loads(data)
    turns = [
        SessionTurn(
            turn_id=t["turn_id"],
            user_text=t.get("user_text"),
            assistant_text=t.get("assistant_text"),
            created_at=datetime.fromisoformat(t["created_at"]),
            completed_at=(
                datetime.fromisoformat(t["completed_at"]) if t.get("completed_at") else None
            ),
        )
        for t in payload.get("turns", [])
    ]
    return SessionState(
        session_id=payload["session_id"],
        status=SessionStatus(payload["status"]),
        created_at=datetime.fromisoformat(payload["created_at"]),
        updated_at=datetime.fromisoformat(payload["updated_at"]),
        engine_selection=EngineSelection(
            stt=payload["engine_selection"].get("stt"),
            router=payload["engine_selection"].get("router"),
            llm=payload["engine_selection"].get("llm"),
            tts=payload["engine_selection"].get("tts"),
        ),
        active_turn_id=payload.get("active_turn_id"),
        turns=turns,
        metadata=dict(payload.get("metadata", {})),
    )


class RedisSessionManager(SessionManager):
    def __init__(self, url: str, *, namespace: str = "ov") -> None:
        self._url = url
        self._key_prefix = f"{namespace}:session:"
        self._index_key = f"{namespace}:sessions"
        self._redis: Any = None

    async def _ensure_redis(self) -> Any:
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as aioredis
        except ImportError as exc:
            raise OpenVoiceError(
                code=ErrorCode.PROVIDER_ERROR,
                message=(
                    "redis package is required for RedisSessionManager. "
                    "Install with: pip install 'open-voice-runtime[redis]'"
                ),
                retryable=False,
            ) from exc
        self._redis = aioredis.from_url(self._url, decode_responses=True)
        await self._redis.ping()
        logger.info("RedisSessionManager connected to %s", self._url)
        return self._redis

    def _key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"

    async def create(self, request: SessionCreateRequest) -> SessionState:
        redis = await self._ensure_redis()
        state = SessionState.create(request)
        await redis.set(self._key(state.session_id), _serialize_state(state))
        await redis.sadd(self._index_key, state.session_id)
        logger.info("RedisSessionManager created session=%s", state.session_id)
        return state

    async def get(self, session_id: str) -> SessionState:
        redis = await self._ensure_redis()
        raw = await redis.get(self._key(session_id))
        if raw is None:
            raise OpenVoiceError(
                code=ErrorCode.SESSION_NOT_FOUND,
                message=f"Session '{session_id}' was not found.",
                retryable=False,
                details={"session_id": session_id},
            )
        return _deserialize_state(raw)

    async def list(self, *, limit: int | None = None) -> list[SessionState]:
        redis = await self._ensure_redis()
        session_ids = await redis.smembers(self._index_key)
        states: list[SessionState] = []
        for session_id in session_ids:
            raw = await redis.get(self._key(session_id))
            if raw is None:
                await redis.srem(self._index_key, session_id)
                continue
            states.append(_deserialize_state(raw))

        states.sort(key=lambda state: state.updated_at, reverse=True)
        if limit is None or limit <= 0:
            return states
        return states[:limit]

    async def list_turns(self, session_id: str, *, limit: int | None = None) -> list[SessionTurn]:
        state = await self.get(session_id)
        turns = list(state.turns)
        if limit is None or limit <= 0:
            return turns
        return turns[-limit:]

    async def update(self, session_id: str, event: SessionTransition) -> SessionState:
        session = await self.get(session_id)
        updated = transition_session(session, event)
        redis = await self._ensure_redis()
        await redis.set(self._key(session_id), _serialize_state(updated))
        return updated

    async def persist(self, state: SessionState) -> None:
        redis = await self._ensure_redis()
        await redis.set(self._key(state.session_id), _serialize_state(state))

    async def close(self, session_id: str) -> None:
        session = await self.get(session_id)
        if session.status.value != "closed":
            transition_session(session, SessionTransition(to_status=SessionStatus.CLOSED))
        redis = await self._ensure_redis()
        await redis.delete(self._key(session_id))
        await redis.srem(self._index_key, session_id)
        logger.info("RedisSessionManager closed session=%s", session_id)
