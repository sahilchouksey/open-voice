from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from open_voice_runtime.llm.contracts import LlmSessionConfig, LlmToolDefinition, LlmToolKind


def normalize_llm_session_config_payload(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}

    payload = dict(value)
    for field_name in ("system_prompt", "additional_instructions"):
        field_value = payload.get(field_name)
        if field_value is not None and not isinstance(field_value, str):
            raise TypeError(f"LLM config field '{field_name}' must be a string.")

    opencode_mode = payload.get("opencode_mode")
    if opencode_mode is not None and not isinstance(opencode_mode, str):
        raise TypeError("LLM config field 'opencode_mode' must be a string.")

    opencode_force_system_override = payload.get("opencode_force_system_override")
    if opencode_force_system_override is not None and not isinstance(
        opencode_force_system_override, bool
    ):
        raise TypeError("LLM config field 'opencode_force_system_override' must be a boolean.")

    enable_fast_ack = payload.get("enable_fast_ack")
    if enable_fast_ack is not None and not isinstance(enable_fast_ack, bool):
        raise TypeError("LLM config field 'enable_fast_ack' must be a boolean.")

    tools = payload.get("tools")
    if tools is not None:
        if not isinstance(tools, list):
            raise TypeError("LLM config field 'tools' must be an array.")
        normalized_tools: list[dict[str, Any]] = []
        for index, item in enumerate(tools):
            if not isinstance(item, Mapping):
                raise TypeError(f"LLM tool config at index {index} must be an object.")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                raise TypeError(f"LLM tool config at index {index} must include a string 'name'.")

            normalized_tool = dict(item)
            for key in ("description", "kind"):
                field_value = normalized_tool.get(key)
                if field_value is not None and not isinstance(field_value, str):
                    raise TypeError(
                        f"LLM tool config field '{key}' at index {index} must be a string."
                    )

            for key in ("parameters", "metadata"):
                field_value = normalized_tool.get(key)
                if field_value is not None and not isinstance(field_value, Mapping):
                    raise TypeError(
                        f"LLM tool config field '{key}' at index {index} must be an object."
                    )
                if isinstance(field_value, Mapping):
                    normalized_tool[key] = dict(field_value)

            normalized_tools.append(normalized_tool)

        payload["tools"] = normalized_tools

    return payload


def llm_session_config_from_payload(
    value: Mapping[str, Any] | None,
    *,
    fallback: LlmSessionConfig | None = None,
) -> LlmSessionConfig:
    base = fallback or LlmSessionConfig()
    if value is None:
        return base

    payload = normalize_llm_session_config_payload(value)
    return LlmSessionConfig(
        system_prompt=_optional_string(payload.get("system_prompt"), base.system_prompt),
        additional_instructions=_optional_string(
            payload.get("additional_instructions"), base.additional_instructions
        ),
        opencode_mode=_optional_string(payload.get("opencode_mode"), base.opencode_mode),
        opencode_force_system_override=payload.get(
            "opencode_force_system_override",
            base.opencode_force_system_override,
        ),
        tools=_tools_from_payload(payload.get("tools"), base.tools),
        enable_fast_ack=payload.get("enable_fast_ack", base.enable_fast_ack),
    )


def _optional_string(value: Any, fallback: str | None) -> str | None:
    if value is None:
        return fallback
    if not isinstance(value, str):
        raise TypeError("Expected string value.")
    return value


def _tools_from_payload(
    value: Any,
    fallback: tuple[LlmToolDefinition, ...],
) -> tuple[LlmToolDefinition, ...]:
    if value is None:
        return fallback
    if not isinstance(value, list):
        raise TypeError("LLM config field 'tools' must be an array.")

    tools: list[LlmToolDefinition] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise TypeError("LLM tool config must be an object.")
        tool_kind = _tool_kind(item.get("kind"))
        tools.append(
            LlmToolDefinition(
                name=str(item["name"]),
                description=item.get("description")
                if isinstance(item.get("description"), str)
                else None,
                kind=tool_kind,
                parameters=dict(item.get("parameters", {}))
                if isinstance(item.get("parameters"), Mapping)
                else {},
                metadata=dict(item.get("metadata", {}))
                if isinstance(item.get("metadata"), Mapping)
                else {},
            )
        )
    return tuple(tools)


def _tool_kind(value: Any) -> LlmToolKind:
    if value is None:
        return LlmToolKind.FUNCTION
    if not isinstance(value, str):
        raise TypeError("LLM tool config field 'kind' must be a string.")
    try:
        return LlmToolKind(value)
    except ValueError as exc:
        raise TypeError(f"Unsupported LLM tool kind: {value!r}.") from exc
