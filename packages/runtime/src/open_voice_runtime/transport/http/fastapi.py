from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from open_voice_runtime.core.errors import OpenVoiceError, TransportProtocolError
from open_voice_runtime.transport.http.parser import parse_session_create_request

from open_voice_runtime.app.server import RuntimeServer


def install_http_routes(app: FastAPI, server: RuntimeServer) -> None:
    @app.get("/health")
    async def get_health() -> dict[str, str]:
        return server.health()

    @app.get("/v1/engines")
    async def list_engines() -> dict[str, list[dict[str, object]]]:
        return await server.list_engines()

    @app.post("/v1/sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(request: Request) -> object:
        try:
            payload = await request.json()
            body = parse_session_create_request(payload)
            return await server.create_session(body)
        except json.JSONDecodeError as error:
            return JSONResponse(
                status_code=400,
                content={
                    "code": "transport_protocol_error",
                    "message": "Session create request body must be valid JSON.",
                    "retryable": False,
                    "details": {},
                },
            )
        except TransportProtocolError as error:
            return JSONResponse(status_code=400, content=error.to_payload())

    @app.get("/v1/sessions/{session_id}")
    async def get_session(session_id: str) -> object:
        try:
            return await server.get_session(session_id)
        except OpenVoiceError as error:
            return JSONResponse(status_code=404, content=error.to_payload())

    @app.get("/v1/sessions/{session_id}/turns")
    async def list_session_turns(session_id: str, limit: int | None = None) -> object:
        safe_limit = None
        if isinstance(limit, int):
            safe_limit = max(1, min(limit, 200))
        try:
            return await server.list_session_turns(session_id, limit=safe_limit)
        except OpenVoiceError as error:
            return JSONResponse(status_code=404, content=error.to_payload())

    @app.get("/v1/sessions")
    async def list_sessions(limit: int | None = None) -> object:
        safe_limit = None
        if isinstance(limit, int):
            safe_limit = max(1, min(limit, 100))
        return await server.list_sessions(limit=safe_limit)

    @app.delete("/v1/sessions/{session_id}")
    async def close_session(session_id: str) -> Response:
        try:
            await server.close_session(session_id)
        except OpenVoiceError as error:
            return JSONResponse(status_code=404, content=error.to_payload())
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/diagnostics/trace/frontend", status_code=status.HTTP_202_ACCEPTED)
    async def ingest_frontend_trace(request: Request) -> dict[str, object]:
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return {
                "accepted": False,
                "reason": "invalid_json",
            }

        if not isinstance(payload, dict):
            return {
                "accepted": False,
                "reason": "invalid_payload",
            }

        session_id = payload.get("session_id")
        records = payload.get("records")
        if not isinstance(session_id, str) or not isinstance(records, list):
            return {
                "accepted": False,
                "reason": "invalid_shape",
            }

        filtered_records: list[dict[str, Any]] = [
            item for item in records if isinstance(item, dict)
        ]
        await server.ingest_frontend_trace(session_id, filtered_records)
        return {
            "accepted": True,
            "count": len(filtered_records),
        }
