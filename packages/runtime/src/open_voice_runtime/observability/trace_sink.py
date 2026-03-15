from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


class TraceSink:
    def __init__(self, *, enabled: bool, base_dir: Path) -> None:
        self._enabled = enabled
        self._base_dir = base_dir
        self._locks: dict[Path, asyncio.Lock] = {}
        self._seq_by_path: dict[Path, int] = {}

    @classmethod
    def from_env(cls) -> "TraceSink":
        enabled = _truthy(os.getenv("OPEN_VOICE_TRACE_ENABLED"))
        base_dir = Path(
            os.getenv(
                "OPEN_VOICE_TRACE_DIR",
                "/home/xix3r/Documents/fun/open-voice/temp/traces",
            )
        )
        return cls(enabled=enabled, base_dir=base_dir)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def append_runtime_event(
        self,
        *,
        session_id: str,
        direction: str,
        kind: str,
        event_type: str,
        payload: Any,
        turn_id: str | None = None,
        generation_id: str | None = None,
    ) -> None:
        if not self._enabled:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "mono_ns": time.monotonic_ns(),
            "source": "runtime-ws",
            "session_id": session_id,
            "turn_id": turn_id,
            "generation_id": generation_id,
            "dir": direction,
            "kind": kind,
            "type": event_type,
            "payload": payload,
        }
        await self._append_records(session_id, "runtime.websocket", [record])

    async def append_frontend_records(
        self,
        session_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        if not self._enabled or not records:
            return

        normalized: list[dict[str, Any]] = []
        for item in records:
            normalized.append(
                {
                    "ts": _optional_str(item.get("ts")) or datetime.now(timezone.utc).isoformat(),
                    "mono_ns": item.get("mono_ns")
                    if isinstance(item.get("mono_ns"), int)
                    else time.monotonic_ns(),
                    "source": _optional_str(item.get("source")) or "demo-frontend",
                    "session_id": session_id,
                    "turn_id": _optional_str(item.get("turn_id")),
                    "generation_id": _optional_str(item.get("generation_id")),
                    "dir": _optional_str(item.get("dir")) or "local",
                    "kind": _optional_str(item.get("kind")) or "ui.action",
                    "type": _optional_str(item.get("type")) or "frontend.trace",
                    "payload": item.get("payload"),
                }
            )

        await self._append_records(session_id, "demo.frontend", normalized)

    async def _append_records(
        self,
        session_id: str,
        stream_name: str,
        records: list[dict[str, Any]],
    ) -> None:
        if not records:
            return

        file_path = self._trace_file_path(session_id, stream_name)
        lock = self._locks.setdefault(file_path, asyncio.Lock())

        async with lock:
            sequence = self._seq_by_path.get(file_path, 0)
            lines: list[str] = []
            for record in records:
                sequence += 1
                payload = dict(record)
                payload["seq"] = sequence
                lines.append(json.dumps(payload, ensure_ascii=True, default=repr))
            self._seq_by_path[file_path] = sequence
            await asyncio.to_thread(self._write_lines, file_path, lines)

    def _trace_file_path(self, session_id: str, stream_name: str) -> Path:
        date_folder = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self._base_dir / date_folder / session_id / f"{stream_name}.ndjson"

    @staticmethod
    def _write_lines(file_path: Path, lines: list[str]) -> None:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with file_path.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line)
                handle.write("\n")
