from __future__ import annotations

from typing import Any

from fastapi import FastAPI, WebSocket
from open_voice_runtime.transport.websocket.handler import RealtimeSocket, RealtimeSocketDisconnect

from open_voice_runtime.app.server import RuntimeServer


class FastApiRealtimeSocket(RealtimeSocket):
    def __init__(self, socket: WebSocket) -> None:
        self._socket = socket

    async def accept(self) -> None:
        await self._socket.accept()

    async def receive_json(self) -> dict[str, Any]:
        from starlette.websockets import WebSocketDisconnect

        try:
            payload = await self._socket.receive_json()
        except WebSocketDisconnect as exc:
            raise RealtimeSocketDisconnect() from exc

        if isinstance(payload, dict):
            return payload
        raise RealtimeSocketDisconnect()

    async def send_json(self, payload: dict[str, Any]) -> None:
        await self._socket.send_json(payload)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        await self._socket.close(code=code, reason=reason)


def install_realtime_route(app: FastAPI, server: RuntimeServer) -> None:
    @app.websocket("/v1/realtime/conversation")
    async def realtime_conversation(socket: WebSocket) -> None:
        await server.realtime().handle(FastApiRealtimeSocket(socket))
