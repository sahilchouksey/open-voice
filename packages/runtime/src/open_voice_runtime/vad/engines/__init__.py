__all__ = ["SileroVadEngine"]


def __getattr__(name: str):
    if name == "SileroVadEngine":
        from open_voice_runtime.vad.engines.silero import SileroVadEngine

        return SileroVadEngine
    raise AttributeError(name)
