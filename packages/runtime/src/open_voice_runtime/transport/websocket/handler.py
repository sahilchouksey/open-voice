from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from open_voice_runtime.core.errors import OpenVoiceError, TransportProtocolError
from open_voice_runtime.observability.trace_sink import TraceSink
from open_voice_runtime.transport.websocket.session import RealtimeConversationSession

logger = logging.getLogger(__name__)


def _trace_payload(payload: dict[str, Any]) -> dict[str, Any]:
    event_type = payload.get("type")
    if event_type != "audio.append":
        return payload
    chunk = payload.get("chunk")
    if not isinstance(chunk, dict):
        return payload
    data_base64 = chunk.get("data_base64")
    if not isinstance(data_base64, str):
        return payload
    redacted_chunk = {
        **chunk,
        "data_base64": "[omitted]",
        "data_base64_bytes": int((len(data_base64) * 3) / 4),
    }
    return {**payload, "chunk": redacted_chunk}


def _log_payload_snapshot(payload: dict[str, Any]) -> str:
    event_type = payload.get("type")
    if event_type == "stt.partial":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "text": payload.get("text"),
            },
            ensure_ascii=True,
        )
    if event_type == "stt.final":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "text": payload.get("text"),
                "revision": payload.get("revision"),
                "finality": payload.get("finality"),
                "deferred": payload.get("deferred"),
            },
            ensure_ascii=True,
        )
    if event_type == "stt.status":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "status": payload.get("status"),
                "waited_ms": payload.get("waited_ms"),
                "attempt": payload.get("attempt"),
            },
            ensure_ascii=True,
        )
    if event_type == "route.selected":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "route_name": payload.get("route_name"),
                "provider": payload.get("provider"),
                "model": payload.get("model"),
                "reason": payload.get("reason"),
            },
            ensure_ascii=True,
        )
    if event_type == "session.status":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "status": payload.get("status"),
                "reason": payload.get("reason"),
            },
            ensure_ascii=True,
        )
    if event_type == "llm.phase":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "phase": payload.get("phase"),
            },
            ensure_ascii=True,
        )
    if event_type == "llm.reasoning.delta":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "part_id": payload.get("part_id"),
                "delta": payload.get("delta"),
            },
            ensure_ascii=True,
        )
    if event_type == "llm.response.delta":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "part_id": payload.get("part_id"),
                "lane": payload.get("lane"),
                "delta": payload.get("delta"),
            },
            ensure_ascii=True,
        )
    if event_type == "llm.completed":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "text": payload.get("text"),
                "finish_reason": payload.get("finish_reason"),
            },
            ensure_ascii=True,
        )
    if event_type == "conversation.interrupted":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "reason": payload.get("reason"),
            },
            ensure_ascii=True,
        )
    if event_type == "turn.queued":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "queue_size": payload.get("queue_size"),
                "source": payload.get("source"),
                "policy": payload.get("policy"),
                "generation_id": payload.get("generation_id"),
            },
            ensure_ascii=True,
        )
    if event_type == "turn.metrics":
        return json.dumps(
            {
                "type": event_type,
                "turn_id": payload.get("turn_id"),
                "queue_delay_ms": payload.get("queue_delay_ms"),
                "stt_to_route_ms": payload.get("stt_to_route_ms"),
                "route_to_llm_first_delta_ms": payload.get("route_to_llm_first_delta_ms"),
                "llm_first_delta_to_tts_first_chunk_ms": payload.get(
                    "llm_first_delta_to_tts_first_chunk_ms"
                ),
                "stt_to_tts_first_chunk_ms": payload.get("stt_to_tts_first_chunk_ms"),
                "turn_to_first_llm_delta_ms": payload.get("turn_to_first_llm_delta_ms"),
                "turn_to_complete_ms": payload.get("turn_to_complete_ms"),
                "cancelled": payload.get("cancelled"),
                "reason": payload.get("reason"),
                "generation_id": payload.get("generation_id"),
            },
            ensure_ascii=True,
        )
    return ""


class RealtimeSocketDisconnect(Exception):
    """Raised when the client disconnects from the realtime socket."""


class RealtimeSocket(Protocol):
    async def accept(self) -> None: ...

    async def receive_json(self) -> dict[str, Any]: ...

    async def send_json(self, payload: dict[str, Any]) -> None: ...

    async def close(self, code: int = 1000, reason: str | None = None) -> None: ...


@dataclass(slots=True)
class RealtimeConnectionHandler:
    session: RealtimeConversationSession
    trace_sink: TraceSink | None = None

    async def _trace(
        self,
        *,
        session_id: str | None,
        direction: str,
        kind: str,
        event_type: str,
        payload: Any,
        turn_id: str | None = None,
        generation_id: str | None = None,
    ) -> None:
        if session_id is None:
            return
        if self.trace_sink is None or not self.trace_sink.enabled:
            return
        await self.trace_sink.append_runtime_event(
            session_id=session_id,
            direction=direction,
            kind=kind,
            event_type=event_type,
            payload=payload,
            turn_id=turn_id,
            generation_id=generation_id,
        )

    async def handle(self, socket: RealtimeSocket) -> None:
        await socket.accept()
        logger.info("Realtime websocket accepted")

        send_guard = asyncio.Lock()
        closed = asyncio.Event()
        last_session_id: str | None = None

        async def emit(payload: dict[str, Any]) -> None:
            nonlocal last_session_id
            if closed.is_set():
                return
            async with send_guard:
                if closed.is_set():
                    return
                payload_session_id = (
                    payload.get("session_id") if isinstance(payload, dict) else None
                )
                if isinstance(payload_session_id, str):
                    last_session_id = payload_session_id
                logger.info("Realtime websocket send type=%s", payload.get("type"))
                snapshot = _log_payload_snapshot(payload)
                if snapshot:
                    logger.info("Realtime websocket send payload=%s", snapshot)
                await self._trace(
                    session_id=payload_session_id if isinstance(payload_session_id, str) else None,
                    direction="out",
                    kind="ws.message",
                    event_type=str(payload.get("type")) if isinstance(payload, dict) else "unknown",
                    payload=_trace_payload(payload) if isinstance(payload, dict) else payload,
                    turn_id=payload.get("turn_id") if isinstance(payload, dict) else None,
                    generation_id=(
                        payload.get("generation_id") if isinstance(payload, dict) else None
                    ),
                )
                try:
                    await socket.send_json(payload)
                except Exception:
                    closed.set()
                    logger.info("Realtime websocket send ignored after disconnect")

        while True:
            payload: dict[str, Any] = {}
            try:
                payload = await socket.receive_json()
            except RealtimeSocketDisconnect:
                closed.set()
                logger.info("Realtime websocket disconnected by client")
                await self._trace(
                    session_id=last_session_id,
                    direction="local",
                    kind="lifecycle",
                    event_type="socket.disconnected",
                    payload={"detail": "client_disconnect"},
                )
                return

            try:
                payload_session_id = (
                    payload.get("session_id") if isinstance(payload, dict) else None
                )
                if isinstance(payload_session_id, str):
                    last_session_id = payload_session_id
                logger.info("Realtime websocket recv type=%s", payload.get("type"))
                await self._trace(
                    session_id=payload_session_id if isinstance(payload_session_id, str) else None,
                    direction="in",
                    kind="ws.message",
                    event_type=str(payload.get("type")) if isinstance(payload, dict) else "unknown",
                    payload=_trace_payload(payload) if isinstance(payload, dict) else payload,
                    turn_id=payload.get("turn_id") if isinstance(payload, dict) else None,
                    generation_id=(
                        payload.get("generation_id") if isinstance(payload, dict) else None
                    ),
                )
                events = await self.session.apply(payload, emit=emit)
            except TransportProtocolError as error:
                logger.warning("Realtime websocket protocol error: %s", error.message)
                await self._trace(
                    session_id=payload.get("session_id") if isinstance(payload, dict) else None,
                    direction="local",
                    kind="error",
                    event_type="transport.protocol_error",
                    payload={"message": error.message},
                )
                await socket.close(code=1003, reason=error.message)
                return
            except OpenVoiceError as error:
                logger.warning("Realtime websocket open voice error: %s", error.message)
                await self._trace(
                    session_id=payload.get("session_id") if isinstance(payload, dict) else None,
                    direction="local",
                    kind="error",
                    event_type="runtime.openvoice_error",
                    payload={"message": error.message, "code": error.code},
                )
                await socket.close(code=1008, reason=error.message)
                return

            for event in events:
                await emit(event)
