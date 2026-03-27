from __future__ import annotations

from collections.abc import Mapping
import json
import os
from dataclasses import dataclass, field
from typing import Any

from open_voice_runtime.llm.config import (
    llm_session_config_from_payload,
    normalize_llm_session_config_payload,
)
from open_voice_runtime.llm.contracts import LlmSessionConfig
from open_voice_runtime.router.contracts import RouteTarget
from open_voice_runtime.router.policy import DEFAULT_LLM_ENGINE_ID, default_route_targets
from open_voice_runtime.session.interruption_config import InterruptionConfig, EndPointingConfig


@dataclass(slots=True)
class RuntimeConfig:
    route_targets: tuple[RouteTarget, ...] = field(default_factory=lambda: default_route_targets())
    default_llm_engine_id: str = DEFAULT_LLM_ENGINE_ID
    llm: LlmSessionConfig = field(default_factory=LlmSessionConfig)
    interruption: InterruptionConfig = field(default_factory=InterruptionConfig)
    endpointing: EndPointingConfig = field(default_factory=EndPointingConfig)

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        default_llm_engine_id = os.getenv("OPEN_VOICE_DEFAULT_LLM_ENGINE", DEFAULT_LLM_ENGINE_ID)
        payload: dict[str, Any] = {"default_llm_engine_id": default_llm_engine_id}
        route_targets_json = os.getenv("OPEN_VOICE_ROUTE_TARGETS")

        if route_targets_json:
            try:
                payload["route_targets"] = json.loads(route_targets_json)
            except json.JSONDecodeError:
                return cls(
                    route_targets=default_route_targets(default_llm_engine_id),
                    default_llm_engine_id=default_llm_engine_id,
                )

        try:
            return cls.from_mapping(payload)
        except TypeError:
            return cls(
                route_targets=default_route_targets(default_llm_engine_id),
                default_llm_engine_id=default_llm_engine_id,
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any] | None,
        *,
        fallback: "RuntimeConfig | None" = None,
    ) -> "RuntimeConfig":
        base = fallback or cls()
        if value is None:
            return base

        payload = normalize_runtime_config_payload(value)
        default_llm_engine_id = payload.get("default_llm_engine_id", base.default_llm_engine_id)
        if not isinstance(default_llm_engine_id, str):
            raise TypeError("Runtime config field 'default_llm_engine_id' must be a string.")

        route_targets = base.route_targets
        if "route_targets" in payload:
            route_targets = _route_targets_from_payload(
                payload["route_targets"], default_llm_engine_id
            )
        elif default_llm_engine_id != base.default_llm_engine_id:
            route_targets = base.with_default_llm_engine(default_llm_engine_id).route_targets

        llm = base.llm
        if "llm" in payload:
            llm = llm_session_config_from_payload(payload["llm"], fallback=base.llm)

        interruption = base.interruption
        if "interruption" in payload:
            interruption = InterruptionConfig.from_payload(payload["interruption"])

        endpointing = base.endpointing
        if "endpointing" in payload:
            endpointing = EndPointingConfig.from_payload(payload["endpointing"])

        return cls(
            route_targets=route_targets,
            default_llm_engine_id=default_llm_engine_id,
            llm=llm,
            interruption=interruption,
            endpointing=endpointing,
        )

    def with_route_targets(self, targets: tuple[RouteTarget, ...]) -> "RuntimeConfig":
        return RuntimeConfig(
            route_targets=targets,
            default_llm_engine_id=self.default_llm_engine_id,
            llm=self.llm,
        )

    def with_default_llm_engine(self, engine_id: str) -> "RuntimeConfig":
        new_targets = tuple(
            RouteTarget(
                llm_engine_id=engine_id,
                provider=t.provider,
                model=t.model,
                profile_id=t.profile_id,
            )
            for t in self.route_targets
        )
        return RuntimeConfig(
            route_targets=new_targets,
            default_llm_engine_id=engine_id,
            llm=self.llm,
        )

    def effective_route_targets(self, llm_engine_id: str | None) -> tuple[RouteTarget, ...]:
        engine_id = llm_engine_id or self.default_llm_engine_id
        if engine_id == self.default_llm_engine_id:
            return self.route_targets
        return self.with_default_llm_engine(engine_id).route_targets


def normalize_runtime_config_payload(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}

    payload = dict(value)
    default_llm_engine_id = payload.get("default_llm_engine_id")
    if default_llm_engine_id is not None and not isinstance(default_llm_engine_id, str):
        raise TypeError("Runtime config field 'default_llm_engine_id' must be a string.")

    route_targets = payload.get("route_targets")
    if route_targets is not None:
        if not isinstance(route_targets, list):
            raise TypeError("Runtime config field 'route_targets' must be an array.")
        normalized_targets: list[dict[str, Any]] = []
        for index, item in enumerate(route_targets):
            if not isinstance(item, Mapping):
                raise TypeError(f"Runtime config route target at index {index} must be an object.")
            normalized_item = dict(item)
            for key in ("llm_engine_id", "provider", "model", "profile_id"):
                target_value = normalized_item.get(key)
                if target_value is not None and not isinstance(target_value, str):
                    raise TypeError(
                        f"Runtime config route target field '{key}' at index {index} must be a string."
                    )
            normalized_targets.append(normalized_item)
        payload["route_targets"] = normalized_targets

    router = payload.get("router")
    if router is not None:
        if not isinstance(router, Mapping):
            raise TypeError("Runtime config field 'router' must be an object.")
        normalized_router = dict(router)
        timeout_ms = normalized_router.get("timeout_ms")
        if timeout_ms is not None and not isinstance(timeout_ms, int):
            raise TypeError("Runtime config field 'router.timeout_ms' must be an integer.")
        mode = normalized_router.get("mode")
        if mode is not None and not isinstance(mode, str):
            raise TypeError("Runtime config field 'router.mode' must be a string.")
        payload["router"] = normalized_router

    llm = payload.get("llm")
    if llm is not None:
        if not isinstance(llm, Mapping):
            raise TypeError("Runtime config field 'llm' must be an object.")
        payload["llm"] = normalize_llm_session_config_payload(llm)
        normalized_llm = dict(payload["llm"])
        for key in ("first_delta_timeout_ms", "total_timeout_ms"):
            timeout_value = normalized_llm.get(key)
            if timeout_value is not None and not isinstance(timeout_value, int):
                raise TypeError(f"Runtime config field 'llm.{key}' must be an integer.")
        payload["llm"] = normalized_llm

    stt = payload.get("stt")
    if stt is not None:
        if not isinstance(stt, Mapping):
            raise TypeError("Runtime config field 'stt' must be an object.")
        normalized_stt = dict(stt)
        final_timeout_ms = normalized_stt.get("final_timeout_ms")
        if final_timeout_ms is not None and not isinstance(final_timeout_ms, int):
            raise TypeError("Runtime config field 'stt.final_timeout_ms' must be an integer.")
        payload["stt"] = normalized_stt

    turn_detection = payload.get("turn_detection")
    if turn_detection is not None:
        if not isinstance(turn_detection, Mapping):
            raise TypeError("Runtime config field 'turn_detection' must be an object.")
        normalized_turn_detection = dict(turn_detection)
        mode = normalized_turn_detection.get("mode")
        if mode is not None and not isinstance(mode, str):
            raise TypeError("Runtime config field 'turn_detection.mode' must be a string.")
        for key in (
            "transcript_timeout_ms",
            "stabilization_ms",
            "min_silence_duration_ms",
            "min_speech_duration_ms",
            "vad_chunk_size",
        ):
            value = normalized_turn_detection.get(key)
            if value is not None and not isinstance(value, int):
                raise TypeError(f"Runtime config field 'turn_detection.{key}' must be an integer.")
        activation_threshold = normalized_turn_detection.get("activation_threshold")
        if activation_threshold is not None and not isinstance(activation_threshold, (int, float)):
            raise TypeError(
                "Runtime config field 'turn_detection.activation_threshold' must be a number."
            )
        payload["turn_detection"] = normalized_turn_detection

    turn_queue = payload.get("turn_queue")
    if turn_queue is not None:
        if not isinstance(turn_queue, Mapping):
            raise TypeError("Runtime config field 'turn_queue' must be an object.")
        normalized_turn_queue = dict(turn_queue)
        policy = normalized_turn_queue.get("policy")
        if policy is not None and not isinstance(policy, str):
            raise TypeError("Runtime config field 'turn_queue.policy' must be a string.")
        payload["turn_queue"] = normalized_turn_queue

    retry = payload.get("retry")
    if retry is not None:
        if not isinstance(retry, Mapping):
            raise TypeError("Runtime config field 'retry' must be an object.")
        normalized_retry = dict(retry)
        enabled = normalized_retry.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise TypeError("Runtime config field 'retry.enabled' must be a boolean.")
        after_ms = normalized_retry.get("after_ms")
        if after_ms is not None and not isinstance(after_ms, int):
            raise TypeError("Runtime config field 'retry.after_ms' must be an integer.")
        payload["retry"] = normalized_retry

    # Interruption configuration
    interruption = payload.get("interruption")
    if interruption is not None:
        if not isinstance(interruption, Mapping):
            raise TypeError("Runtime config field 'interruption' must be an object.")
        normalized_interruption = dict(interruption)
        mode = normalized_interruption.get("mode")
        if mode is not None and not isinstance(mode, str):
            raise TypeError("Runtime config field 'interruption.mode' must be a string.")
        min_duration = normalized_interruption.get("min_duration")
        if min_duration is not None and not isinstance(min_duration, (int, float)):
            raise TypeError("Runtime config field 'interruption.min_duration' must be a number.")
        min_words = normalized_interruption.get("min_words")
        if min_words is not None and not isinstance(min_words, int):
            raise TypeError("Runtime config field 'interruption.min_words' must be an integer.")
        cooldown_ms = normalized_interruption.get("cooldown_ms")
        if cooldown_ms is not None and not isinstance(cooldown_ms, int):
            raise TypeError("Runtime config field 'interruption.cooldown_ms' must be an integer.")
        payload["interruption"] = normalized_interruption

    # EndPointing configuration
    endpointing = payload.get("endpointing")
    if endpointing is not None:
        if not isinstance(endpointing, Mapping):
            raise TypeError("Runtime config field 'endpointing' must be an object.")
        normalized_endpointing = dict(endpointing)
        mode = normalized_endpointing.get("mode")
        if mode is not None and not isinstance(mode, str):
            raise TypeError("Runtime config field 'endpointing.mode' must be a string.")
        min_delay = normalized_endpointing.get("min_delay")
        if min_delay is not None and not isinstance(min_delay, (int, float)):
            raise TypeError("Runtime config field 'endpointing.min_delay' must be a number.")
        max_delay = normalized_endpointing.get("max_delay")
        if max_delay is not None and not isinstance(max_delay, (int, float)):
            raise TypeError("Runtime config field 'endpointing.max_delay' must be a number.")
        payload["endpointing"] = normalized_endpointing

    return payload


def _route_targets_from_payload(value: Any, default_llm_engine_id: str) -> tuple[RouteTarget, ...]:
    if not isinstance(value, list):
        raise TypeError("Runtime config field 'route_targets' must be an array.")
    return tuple(
        RouteTarget(
            llm_engine_id=(
                item.get("llm_engine_id") if isinstance(item.get("llm_engine_id"), str) else None
            )
            or default_llm_engine_id,
            provider=item.get("provider") if isinstance(item.get("provider"), str) else None,
            model=item.get("model") if isinstance(item.get("model"), str) else None,
            profile_id=item.get("profile_id") if isinstance(item.get("profile_id"), str) else None,
        )
        for item in value
        if isinstance(item, Mapping)
    )
