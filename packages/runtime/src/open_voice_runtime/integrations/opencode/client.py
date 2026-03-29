from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency guard
    httpx = None  # type: ignore[assignment]


DEFAULT_OPENCODE_BASE_URL = "http://127.0.0.1:4096"


def opencode_backend_available() -> bool:
    return httpx is not None


def opencode_cli_available() -> bool:
    return shutil.which("opencode") is not None


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() not in {"0", "false", "no", "off"}


def _discover_opencode_directory() -> str | None:
    explicit = os.getenv("OPEN_VOICE_OPENCODE_DIRECTORY")
    if explicit is not None:
        value = explicit.strip()
        return value or None

    current = Path(os.getcwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".opencode" / "opencode.json").is_file():
            return str(candidate)

    return str(current)


@dataclass(slots=True)
class OpencodeConfig:
    base_url: str = field(
        default_factory=lambda: os.getenv("OPENCODE_BASE_URL", DEFAULT_OPENCODE_BASE_URL).rstrip(
            "/"
        )
    )
    directory: str | None = field(default_factory=_discover_opencode_directory)
    workspace: str | None = field(
        default_factory=lambda: os.getenv("OPEN_VOICE_OPENCODE_WORKSPACE")
    )
    request_timeout: float = 30.0
    startup_timeout: float = 30.0
    enable_exa: bool = field(
        default_factory=lambda: _env_bool("OPEN_VOICE_OPENCODE_ENABLE_EXA", True)
    )


@dataclass(frozen=True, slots=True)
class OpencodeModelRef:
    provider_id: str
    model_id: str


class OpencodeClient:
    def __init__(self, config: OpencodeConfig | None = None) -> None:
        self._config = config or OpencodeConfig()
        self._client = httpx.AsyncClient(timeout=self._config.request_timeout) if httpx else None
        self._process: subprocess.Popen[Any] | None = None
        parsed = urlparse(self._config.base_url)
        self._host = parsed.hostname or "127.0.0.1"
        self._port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self._can_spawn_local = self._host in {"127.0.0.1", "localhost"}

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        await self.stop()

    async def ensure_running(self) -> None:
        self._require_httpx()
        if await self.is_healthy():
            return
        if not self._can_spawn_local or not opencode_cli_available():
            raise RuntimeError(
                "OpenCode server is not reachable. Set OPENCODE_BASE_URL to a running server "
                "or install the opencode CLI for local spawning."
            )

        env = os.environ.copy()
        if self._config.enable_exa:
            env.setdefault("OPENCODE_ENABLE_EXA", "1")
        else:
            env.setdefault("OPENCODE_ENABLE_EXA", "0")

        self._process = subprocess.Popen(
            [
                "opencode",
                "serve",
                "--hostname",
                self._host,
                "--port",
                str(self._port),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )

        deadline = asyncio.get_running_loop().time() + self._config.startup_timeout
        while asyncio.get_running_loop().time() < deadline:
            if await self.is_healthy():
                return
            await asyncio.sleep(0.1)

        await self.stop()
        raise RuntimeError("Failed to start OpenCode server.")

    async def stop(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is not None:
            self._process = None
            return

        self._process.terminate()
        try:
            await asyncio.to_thread(self._process.wait, 3)
        except Exception:
            self._process.kill()
            with suppress(Exception):
                await asyncio.to_thread(self._process.wait, 2)
        finally:
            self._process = None

    async def is_healthy(self) -> bool:
        self._require_httpx()
        assert self._client is not None
        try:
            response = await self._client.get(
                f"{self._config.base_url}/config/providers",
                params=self._params(),
            )
            if response.status_code == 200:
                return True
        except Exception:
            return False

        try:
            response = await self._client.get(
                f"{self._config.base_url}/config",
                params=self._params(),
            )
        except Exception:
            return False
        return response.status_code == 200

    async def create_session(self, permission: list[dict[str, str]] | None = None) -> str:
        await self.ensure_running()
        assert self._client is not None
        body: dict[str, Any] = {}
        if permission:
            body["permission"] = permission
        response = await self._client.post(
            f"{self._config.base_url}/session",
            params=self._params(),
            json=body,
        )
        payload = await self._json_response(response)
        session_id = payload.get("id")
        if isinstance(session_id, str) and session_id:
            return session_id
        raise RuntimeError("OpenCode session create response did not include an id.")

    async def prompt_async(
        self,
        session_id: str,
        *,
        model: OpencodeModelRef,
        system: str,
        user_text: str,
        mode: str | None = None,
    ) -> None:
        await self.ensure_running()
        assert self._client is not None
        body: dict[str, Any] = {
            "model": {
                "providerID": model.provider_id,
                "modelID": model.model_id,
            },
            "system": system,
            "parts": [{"type": "text", "text": user_text}],
        }
        if isinstance(mode, str) and mode.strip():
            body["agent"] = mode.strip()

        response = await self._client.post(
            f"{self._config.base_url}/session/{session_id}/prompt_async",
            params=self._params(),
            json=body,
        )
        await self._consume_empty_response(response)

    async def iter_events(
        self,
        stop: asyncio.Event,
        ready: asyncio.Event,
    ) -> AsyncIterator[dict[str, Any]]:
        await self.ensure_running()
        assert self._client is not None
        async with self._client.stream(
            "GET",
            f"{self._config.base_url}/event",
            params=self._params(),
            timeout=None,
        ) as response:
            await self._consume_stream_headers(response)
            ready.set()

            lines: list[str] = []
            async for raw in response.aiter_lines():
                if stop.is_set():
                    return
                if raw == "":
                    if not lines:
                        continue
                    payload = "\n".join(lines)
                    lines.clear()
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(data, dict):
                        yield data
                    continue
                if raw.startswith(":"):
                    continue
                if raw.startswith("data:"):
                    lines.append(raw[5:].lstrip())

    async def latest_assistant_message(self, session_id: str) -> dict[str, Any] | None:
        await self.ensure_running()
        assert self._client is not None
        response = await self._client.get(
            f"{self._config.base_url}/session/{session_id}/message",
            params={**self._params(), "limit": 20},
        )
        if response.status_code >= 400:
            raise RuntimeError(await self._response_error(response))
        payload = response.json()
        if not isinstance(payload, list):
            return None

        latest: dict[str, Any] | None = None
        for item in payload:
            if not isinstance(item, dict):
                continue
            info = item.get("info")
            if not isinstance(info, dict):
                continue
            if info.get("role") != "assistant":
                continue
            latest = item
        return latest

    async def _json_response(self, response: Any) -> dict[str, Any]:
        if response.status_code >= 400:
            raise RuntimeError(await self._response_error(response))
        payload = response.json()
        if isinstance(payload, dict):
            return payload
        raise RuntimeError("Expected JSON object response from OpenCode.")

    async def _consume_empty_response(self, response: Any) -> None:
        if response.status_code < 400:
            return
        raise RuntimeError(await self._response_error(response))

    async def _consume_stream_headers(self, response: Any) -> None:
        if response.status_code < 400:
            return
        raise RuntimeError(await self._response_error(response))

    async def _response_error(self, response: Any) -> str:
        body = await response.aread()
        text = body.decode("utf-8", errors="replace").strip()
        if text:
            return f"OpenCode request failed ({response.status_code}): {text}"
        return f"OpenCode request failed ({response.status_code})."

    def _params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self._config.directory:
            params["directory"] = self._config.directory
        if self._config.workspace:
            params["workspace"] = self._config.workspace
        return params

    def _require_httpx(self) -> None:
        if httpx is not None:
            return
        raise RuntimeError("httpx is required for the OpenCode integration.")
