from __future__ import annotations

from open_voice_runtime.core.registry import EngineDescriptor


def build_engine_catalog(
    *,
    stt_entries: list[EngineDescriptor],
    vad_entries: list[EngineDescriptor],
    router_entries: list[EngineDescriptor],
    llm_entries: list[EngineDescriptor],
    tts_entries: list[EngineDescriptor],
) -> dict[str, list[EngineDescriptor]]:
    return {
        "stt": _with_known_defaults(
            stt_entries,
            [
                EngineDescriptor(
                    id="moonshine",
                    kind="stt",
                    label="Moonshine Voice",
                    default=True,
                    capabilities={
                        "streaming": True,
                        "batch": True,
                        "partial_results": True,
                        "languages": ["en"],
                    },
                    available=False,
                    status="missing_dependency",
                )
            ],
        ),
        "vad": _with_known_defaults(
            vad_entries,
            [
                EngineDescriptor(
                    id="silero",
                    kind="vad",
                    label="Silero VAD Lite",
                    default=True,
                    capabilities={"streaming": True, "sample_rates_hz": [16000]},
                    available=False,
                    status="missing_dependency",
                )
            ],
        ),
        "router": _with_known_defaults(
            router_entries,
            [
                EngineDescriptor(
                    id="arch-router",
                    kind="router",
                    label="Arch Router 1.5B",
                    default=True,
                    capabilities={},
                    available=False,
                    status="missing_dependency",
                )
            ],
        ),
        "llm": _with_known_defaults(
            llm_entries,
            [
                EngineDescriptor(
                    id="opencode",
                    kind="llm",
                    label="OpenCode SDK",
                    default=True,
                    capabilities={},
                    available=False,
                    status="planned",
                )
            ],
        ),
        "tts": _with_known_defaults(
            tts_entries,
            [
                EngineDescriptor(
                    id="kokoro",
                    kind="tts",
                    label="Kokoro ONNX",
                    default=True,
                    capabilities={"streaming": True},
                    available=False,
                    status="missing_dependency",
                )
            ],
        ),
    }


def _with_known_defaults(
    entries: list[EngineDescriptor],
    defaults: list[EngineDescriptor],
) -> list[EngineDescriptor]:
    known = {entry.id: entry for entry in entries}
    for default in defaults:
        known.setdefault(default.id, default)
    return list(known.values())
