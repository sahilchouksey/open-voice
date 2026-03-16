from __future__ import annotations

from open_voice_runtime.app.server import RuntimeServer, create_server
from open_voice_runtime.transport.http.fastapi import install_http_routes
from open_voice_runtime.transport.websocket.fastapi import install_realtime_route


def create_asgi_app(server: RuntimeServer | None = None):
    from fastapi import FastAPI

    app = FastAPI(title="Open Voice Runtime", version="0.1.0")
    runtime = server or create_server()

    install_http_routes(app, runtime)
    install_realtime_route(app, runtime)
    return app
