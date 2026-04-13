from __future__ import annotations

from open_voice_runtime.app.config import RuntimeConfig
from open_voice_runtime.llm.contracts import LlmToolKind
from open_voice_runtime.llm.contracts import LlmMessage, LlmRequest, LlmRole
from open_voice_runtime.llm.engines.opencode import default_opencode_tools
from open_voice_runtime.llm.engines.opencode import _delta
from open_voice_runtime.llm.engines.opencode import _prompt
from open_voice_runtime.llm.prompting import build_open_voice_system_prompt
from open_voice_runtime.llm.prompting import strip_tts_symbols


def test_runtime_config_parses_nested_llm_settings() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "default_llm_engine_id": "custom-llm",
            "llm": {
                "system_prompt": "Prefer concise architecture guidance.",
                "additional_instructions": "Ask clarifying questions when requirements conflict.",
                "tools": [
                    {
                        "name": "websearch",
                        "description": "Search the web for current information.",
                        "kind": "mcp",
                        "metadata": {"source": "runtime-default"},
                    }
                ],
            },
        }
    )

    assert config.default_llm_engine_id == "custom-llm"
    assert config.llm.system_prompt == "Prefer concise architecture guidance."
    assert config.llm.additional_instructions == (
        "Ask clarifying questions when requirements conflict."
    )
    assert len(config.llm.tools) == 1
    assert config.llm.tools[0].name == "websearch"
    assert config.llm.tools[0].kind is LlmToolKind.MCP


def test_runtime_config_parses_opencode_mode_and_override_flags() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "llm": {
                "opencode_mode": "build",
                "opencode_force_system_override": True,
            }
        }
    )

    assert config.llm.opencode_mode == "build"
    assert config.llm.opencode_force_system_override is True


def test_runtime_config_preserves_fallback_llm_settings() -> None:
    base = RuntimeConfig.from_mapping(
        {
            "llm": {
                "system_prompt": "Base system prompt.",
                "tools": [
                    {
                        "name": "webfetch",
                        "description": "Fetch web pages.",
                    }
                ],
            }
        }
    )

    merged = RuntimeConfig.from_mapping(
        {
            "llm": {
                "additional_instructions": "Prefer spoken summaries first.",
            }
        },
        fallback=base,
    )

    assert merged.llm.system_prompt == "Base system prompt."
    assert merged.llm.additional_instructions == "Prefer spoken summaries first."
    assert len(merged.llm.tools) == 1
    assert merged.llm.tools[0].name == "webfetch"


def test_open_voice_prompt_builder_layers_runtime_and_tool_context() -> None:
    config = RuntimeConfig.from_mapping(
        {
            "llm": {
                "system_prompt": "You are helping with app architecture.",
                "additional_instructions": "Keep spoken output short.",
                "tools": [
                    {
                        "name": "websearch",
                        "description": "Search the web for current information.",
                        "kind": "mcp",
                    },
                ],
            }
        }
    )

    prompt = build_open_voice_system_prompt(config.llm)

    assert (
        "You are Open Voice, a realtime voice-first assistant for conversation and web research."
        in prompt
    )
    assert "Application Context:" in prompt
    assert "You are helping with app architecture." in prompt
    assert "Additional Session Instructions:" in prompt
    assert "Keep spoken output short." in prompt
    assert "Available Tools (use naturally without mentioning technical details):" in prompt
    assert "- websearch (external service): Search the web for current information." in prompt
    assert (
        "For current events or other time-sensitive topics, search the web before answering"
        in prompt
    )
    assert (
        "Do not rely on memory alone for live facts like news, elections, markets, sports, or weather"
        in prompt
    )
    assert "Never read full URLs out loud" in prompt
    assert "say only the domain name" in prompt
    assert "Never mention that you cannot hear audio or are text-only" in prompt
    assert (
        "Never ask the user to type, paste, click, tap, copy, upload, or use the keyboard" in prompt
    )
    assert "ask the user to say it aloud, spell it slowly, or answer verbally" in prompt
    assert (
        "Never mention internal tools routing decisions model names or runtime internals" in prompt
    )
    assert "You are NOT a coding IDE assistant in this app" in prompt


def test_default_opencode_tools_include_web_search_only() -> None:
    tools = default_opencode_tools()

    assert [tool.name for tool in tools] == ["websearch", "webfetch"]


def test_opencode_delta_ignores_whitespace_only_snapshot_changes() -> None:
    previous = (
        "This is a Python runtime project for Open Voice. It has modules for:\n- VAD\n- STT\n"
    )
    current = "This is a Python runtime project for Open Voice. It has modules for:\n- VAD\n- STT"

    assert _delta(previous, current) == ""


def test_opencode_delta_extracts_true_new_content() -> None:
    previous = "From the search results"
    current = "From the search results, this is settled theory."

    assert _delta(previous, current) == ", this is settled theory."


def test_strip_tts_symbols_removes_stray_asterisks() -> None:
    text = "I can **help* with this * now."

    assert strip_tts_symbols(text) == "I can help with this now."


def test_strip_tts_symbols_reduces_links_to_domain_only() -> None:
    text = (
        "See https://docs.python.org/3/library/os.html and also www.github.com/openai/openai-python"
    )

    assert strip_tts_symbols(text) == "See docs.python.org and also github.com"


def test_prompt_uses_raw_system_prompt_when_force_override_enabled() -> None:
    request = LlmRequest(
        session_id="sess-1",
        turn_id="turn-1",
        messages=[LlmMessage(role=LlmRole.USER, content="hello")],
        system_prompt="You are Open Voice and this must fully override upstream agent prompts.",
        metadata={
            "additional_instructions": "ignored under force override",
            "opencode_force_system_override": True,
        },
    )

    assert (
        _prompt(request, default_opencode_tools())
        == "You are Open Voice and this must fully override upstream agent prompts."
    )
