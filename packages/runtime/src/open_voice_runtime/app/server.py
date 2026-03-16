from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from open_voice_runtime.app.dependencies import RuntimeDependencies
from open_voice_runtime.app.bootstrap import bootstrap_runtime
from open_voice_runtime.session.models import SessionCreateRequest
from open_voice_runtime.transport.http.presenter import (
    engine_descriptor_payload,
    session_state_payload,
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
