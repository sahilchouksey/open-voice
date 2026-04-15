from __future__ import annotations

import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SRC = ROOT / "packages" / "runtime" / "src"
if str(RUNTIME_SRC) not in sys.path:
    sys.path.insert(0, str(RUNTIME_SRC))

from open_voice_runtime.app.server import create_server
from open_voice_runtime.app.dependencies import build_runtime_dependencies
from open_voice_runtime.transport.http.fastapi import install_http_routes
from open_voice_runtime.transport.websocket.fastapi import install_realtime_route


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_env_file(ROOT / "demos" / ".env")
_load_env_file(ROOT / "demos" / ".env.local")

# Ensure the runtime always resolves project-local .opencode config.
os.environ.setdefault("OPEN_VOICE_OPENCODE_DIRECTORY", str(ROOT))

# Ensure built-in websearch availability for voice mode in the demo backend.
os.environ.setdefault("OPEN_VOICE_OPENCODE_ENABLE_EXA", "1")

# Enable runtime/frontend trace capture by default in the demo so debugging
# session timing, interruption, and state transitions is deterministic.
os.environ.setdefault("OPEN_VOICE_TRACE_ENABLED", "1")
os.environ.setdefault("OPEN_VOICE_TRACE_DIR", str(ROOT / "temp" / "traces"))

# Use a demo-dedicated local OpenCode port so stale global daemons on :4096
# do not get reused with outdated flags/credentials.
os.environ.setdefault("OPENCODE_BASE_URL", "http://127.0.0.1:4098")


# ── Register Parakeet STT Engine ──────────────────────────────────────────────
BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _register_parakeet_stt(stt_registry) -> None:
    """Register Parakeet v2 STT engine if available."""
    parakeet_device = os.getenv("PARAKEET_DEVICE", "cuda")

    try:
        from parakeet_engine import ParakeetSttEngine

        engine = ParakeetSttEngine(device=parakeet_device)
        stt_registry.register(engine)
        print(f"[demo] Registered Parakeet v2 STT engine (device={parakeet_device})")
    except ImportError as e:
        print(f"[demo] Parakeet v2 not available: {e}")
        print(
            "[demo] Install dependencies: pip install nemo_toolkit['asr'] torch torchaudio soundfile silero-vad"
        )
    except Exception as e:
        print(f"[demo] Failed to register Parakeet v2: {e}")


def create_app() -> FastAPI:
    app = FastAPI(title="Open Voice Demo Backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Build runtime dependencies
    deps = build_runtime_dependencies()

    # Uncomment below to enable Parakeet v2 STT:
    # _register_parakeet_stt(deps.stt_registry)
    # from open_voice_runtime.app.catalog import build_engine_catalog
    # deps.engine_catalog = build_engine_catalog(...)

    # Create server
    from open_voice_runtime.app.server import RuntimeServer

    runtime = RuntimeServer(dependencies=deps)

    install_http_routes(app, runtime)
    install_realtime_route(app, runtime)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    host = os.getenv("OPEN_VOICE_DEMO_HOST", "0.0.0.0")
    port = int(os.getenv("OPEN_VOICE_DEMO_PORT", "8011"))
    uvicorn.run(create_app(), host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
