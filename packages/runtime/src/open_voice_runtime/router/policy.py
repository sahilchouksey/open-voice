from __future__ import annotations

from open_voice_runtime.router.contracts import RouteTarget


DEFAULT_LLM_ENGINE_ID = "opencode"


def default_route_targets(llm_engine_id: str | None = None) -> tuple[RouteTarget, ...]:
    engine_id = llm_engine_id or DEFAULT_LLM_ENGINE_ID
    return (
        RouteTarget(
            llm_engine_id=engine_id,
            provider="github-copilot",
            model="gpt-5-mini",
            profile_id="trivial_route",
        ),
        RouteTarget(
            llm_engine_id=engine_id,
            provider="opencode-go",
            model="minimax-m2.5",
            profile_id="simple_route",
        ),
        RouteTarget(
            llm_engine_id=engine_id,
            provider="opencode",
            model="gpt-5.3-codex",
            profile_id="moderate_route",
        ),
        RouteTarget(
            llm_engine_id=engine_id,
            provider="github-copilot",
            model="claude-sonnet-4.5",
            profile_id="complex_route",
        ),
        RouteTarget(
            llm_engine_id=engine_id,
            provider="github-copilot",
            model="gpt-5.4",
            profile_id="expert_route",
        ),
    )


def select_route_target(
    route_name: str,
    targets: tuple[RouteTarget, ...],
) -> RouteTarget | None:
    normalized = route_name.lower().strip()
    for target in targets:
        if target.profile_id == normalized:
            return target
    return None
