from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from open_voice_runtime.app.dependencies import RuntimeDependencies
from open_voice_runtime.app.bootstrap import bootstrap_runtime
from open_voice_runtime.session.models import SessionCreateRequest
from open_voice_runtime.transport.http.presenter import (
    engine_descriptor_payload,
    session_history_entry_payload,
    session_state_payload,
    session_turn_payload,
)
from open_voice_runtime.transport.websocket.handler import RealtimeConnectionHandler


@dataclass(slots=True)
class RuntimeServer:
    dependencies: RuntimeDependencies

    def health(self) -> dict[str, str]:
        return {"status": "ok"}

    async def list_engines(self) -> dict[str, list[dict[str, object]]]:
        return {
            kind: [engine_descriptor_payload(item) for item in entries]
            for kind, entries in self.dependencies.engine_catalog.items()
        }

    async def create_session(
        self,
        request: SessionCreateRequest | None = None,
    ) -> dict[str, object]:
        state = await self.dependencies.session_manager.create(request or SessionCreateRequest())
        return session_state_payload(state)

    async def get_session(self, session_id: str) -> dict[str, object]:
        state = await self.dependencies.session_manager.get(session_id)
        return session_state_payload(state)

    async def list_sessions(self, *, limit: int | None = None) -> list[dict[str, object]]:
        states = await self.dependencies.session_manager.list(limit=limit)
        return [session_history_entry_payload(state) for state in states]

    async def list_session_turns(
        self,
        session_id: str,
        *,
        limit: int | None = None,
    ) -> list[dict[str, object]]:
        turns = await self.dependencies.session_manager.list_turns(session_id, limit=limit)
        return [session_turn_payload(turn) for turn in turns]

    async def close_session(self, session_id: str) -> None:
        await self.dependencies.session_manager.close(session_id)

    def realtime(self) -> RealtimeConnectionHandler:
        return self.dependencies.realtime_handler

    async def ingest_frontend_trace(
        self,
        session_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        await self.dependencies.trace_sink.append_frontend_records(session_id, records)


def create_server() -> RuntimeServer:
    return RuntimeServer(dependencies=bootstrap_runtime())
