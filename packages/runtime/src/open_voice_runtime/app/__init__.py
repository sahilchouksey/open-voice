"""Application bootstrap and transport wiring."""

__all__ = ["RuntimeServer", "create_asgi_app", "create_server"]


def create_asgi_app(*args, **kwargs):
    from open_voice_runtime.app.asgi import create_asgi_app as _create_asgi_app

    return _create_asgi_app(*args, **kwargs)


def create_server(*args, **kwargs):
    from open_voice_runtime.app.server import create_server as _create_server

    return _create_server(*args, **kwargs)


def __getattr__(name: str):
    if name == "RuntimeServer":
        from open_voice_runtime.app.server import RuntimeServer

        return RuntimeServer
    raise AttributeError(name)
