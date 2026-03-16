from __future__ import annotations

import logging
import os
from pathlib import Path

# Configure logging at module level
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from open_voice_runtime.app.server import create_server
from open_voice_runtime.transport.http.fastapi import install_http_routes
from open_voice_runtime.transport.websocket.fastapi import install_realtime_route


def create_demo_app() -> FastAPI:
    root = Path(__file__).resolve().parent
    web_examples = root.parent / "web-sdk" / "examples"
    os.environ.setdefault(
        "OPEN_VOICE_KOKORO_ONNX_ASSET_DIR",
        str(root / ".models" / "kokoro-onnx"),
    )
    os.environ.setdefault("OPEN_VOICE_OPENCODE_ENABLE_EXA", "1")
    os.environ.setdefault("OPEN_VOICE_TRACE_ENABLED", "1")

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

    @app.get("/")
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/demo/demo.html", status_code=307)

    app.mount("/demo", StaticFiles(directory=web_examples, html=True), name="demo")
    return app


def main() -> None:
    host = os.getenv("OPEN_VOICE_DEMO_HOST", "0.0.0.0")
    port = int(os.getenv("OPEN_VOICE_DEMO_PORT", "8011"))
    uvicorn.run(
        "demo_backend:create_demo_app",
        factory=True,
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
