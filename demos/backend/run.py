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
from open_voice_runtime.transport.http.fastapi import install_http_routes
from open_voice_runtime.transport.websocket.fastapi import install_realtime_route

os.environ.setdefault(
    "OPEN_VOICE_KOKORO_ONNX_ASSET_DIR",
    str(ROOT / "packages" / "runtime" / ".models" / "kokoro-onnx"),
)
os.environ.setdefault("OPEN_VOICE_OPENCODE_DIRECTORY", str(ROOT))
os.environ.setdefault("OPEN_VOICE_OPENCODE_ENABLE_EXA", "1")


def create_app() -> FastAPI:
    app = FastAPI(title="Open Voice Demo Backend", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    runtime = create_server()
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
