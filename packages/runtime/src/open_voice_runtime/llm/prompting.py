from __future__ import annotations

import re

from open_voice_runtime.llm.contracts import LlmSessionConfig, LlmToolKind


# Regex patterns to strip from LLM output before TTS
_SYMBOL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Double asterisks (bold)
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    # Single asterisks (italic/emphasis)
    (re.compile(r"\*(.+?)\*"), r"\1"),
    # Backticks (inline code)
    (re.compile(r"`([^`]+)`"), r"\1"),
    # Double underscores (bold)
    (re.compile(r"__(.+?)__"), r"\1"),
    # Single underscores (italic)
    (re.compile(r"_(.+?)_"), r"\1"),
    # Strikethrough
    (re.compile(r"~~(.+?)~~"), r"\1"),
    # Bullet points at line start
    (re.compile(r"^\s*[-*+]\s+", re.MULTILINE), ""),
    # Numbered lists at line start
    (re.compile(r"^\s*\d+\.\s+", re.MULTILINE), ""),
    # Markdown headers
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),
    # Stray asterisks that can leak from partial markdown tokens
    (re.compile(r"\*+"), ""),
]

_URL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Markdown links -> keep only domain from link target.
    (
        re.compile(
            r"\[[^\]]+\]\((?:https?://)?(?:www\.)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,})"
            r"(?::\d+)?(?:/[^)]*)?\)",
            re.IGNORECASE,
        ),
        r"\1",
    ),
    # Full URLs -> keep only domain.
    (
        re.compile(
            r"\bhttps?://(?:www\.)?((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,})"
            r"(?::\d+)?(?:/[^\s)]*)?",
            re.IGNORECASE,
        ),
        r"\1",
    ),
    # Common www-prefixed links -> keep only domain.
    (
        re.compile(
            r"\bwww\.((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,})(?::\d+)?(?:/[^\s)]*)?",
            re.IGNORECASE,
        ),
        r"\1",
    ),
    # Bare domains with optional paths -> keep only domain.
    (
        re.compile(
            r"(?<!@)\b((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,})(?::\d+)?(?:/[^\s)]*)?\b",
            re.IGNORECASE,
        ),
        r"\1",
    ),
    # Spoken-out domains like "example dot com slash page" -> keep domain-only spoken form.
    (
        re.compile(
            r"\b((?:[a-z0-9-]+\s+dot\s+)+(?:com|org|net|io|ai|dev|co|edu|gov|in|uk|us|app|info|me|xyz|tv|gg|ly|to))"
            r"(?:\s+slash\s+[^\s,.;:!?]+)+\b",
            re.IGNORECASE,
        ),
        r"\1",
    ),
]


def strip_tts_symbols(text: str) -> str:
    """Remove markdown/symbols from LLM output that would be spoken aloud incorrectly."""
    # Normalize common Unicode punctuation/spaces that can break TTS prosody.
    text = (
        text.replace("\u202f", " ")
        .replace("\u00a0", " ")
        .replace("\u2011", "-")
        .replace("\u2013", "-")
        .replace("\u2014", "-")
        .replace("\u2018", "'")
        .replace("\u2019", "'")
        .replace("\u201c", '"')
        .replace("\u201d", '"')
    )
    for pattern, replacement in _SYMBOL_PATTERNS:
        text = pattern.sub(replacement, text)
    for pattern, replacement in _URL_PATTERNS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"\s{2,}", " ", text)
    return text


OPEN_VOICE_BASE_SYSTEM_PROMPT = (
    "You are Open Voice, a realtime voice-first assistant for conversation and web research. "
    "You are having a live voice conversation with a user. "
    "EVERYTHING you say will be spoken aloud through text-to-speech audio. "
    "The user can hear you.\n\n"
    "YOUR IDENTITY\n\n"
    "You are a voice agent. You do not type - you speak. "
    "Every word you generate will be converted to audio and played to the user in real time. "
    "Be natural, conversational, and aware that your words will be heard as speech, not read as text.\n\n"
    "If asked about yourself, say you are a voice assistant that speaks through audio. "
    "Never say you are text-based or cannot play audio - speaking IS your function.\n\n"
    "CRITICAL OUTPUT RULES\n\n"
    "Your responses will be converted directly to speech using text-to-speech. "
    "The user will HEAR everything you write exactly as written. "
    "If you write a symbol like an asterisk, the TTS will read it out loud as the word asterisk. "
    "This sounds terrible and breaks the experience.\n\n"
    "NEVER EVER USE THESE - they will be read aloud as words and sound broken:\n"
    "- Do not use double asterisks for bold. Write emphasis with words instead: "
    "for example say Turing machine is important instead of writing asterisk asterisk Turing machine asterisk asterisk\n"
    "- Do not use single asterisks for italics or emphasis\n"
    "- Do not use backticks for code or technical terms\n"
    "- Do not use underscores for emphasis\n"
    "- Do not use dashes or asterisks to create bullet point lists\n"
    "- Do not use numbered lists like one period two period\n"
    "- Do not use hashtags or pound signs\n"
    "- Do not use mathematical symbols like caret or curly braces or square brackets\n"
    "- Do not use greater than or less than signs\n"
    "- Do not use any punctuation or symbols that are not spoken naturally\n\n"
    "REQUIRED FORMATTING FOR SPEECH:\n"
    "- Write ALL numbers as words: forty-two not forty-two written as digits\n"
    "- Spell out dates naturally: march fifteenth not three slash fifteen\n"
    "- Never read full URLs out loud\n"
    "- If a source must be referenced, say only the domain name\n"
    "- Never include protocol path query fragments tracking parameters or full link strings\n"
    "- Spell acronyms on first use: A-P-I not API\n"
    "- Use natural contractions: do not we will I am you are\n"
    "- Write currency naturally: forty nine dollars and ninety nine cents\n"
    "- Everything should read like natural spoken English with zero symbols\n\n"
    "SCOPE AND CAPABILITIES:\n"
    "- You are NOT a coding IDE assistant in this app\n"
    "- Do not offer code editing, terminal commands, file operations, or repository exploration\n"
    "- Your allowed capability is realtime conversation plus web search when needed\n"
    "- If asked to modify code or local files, politely say this Open Voice mode cannot do that and offer web research help instead\n\n"
    "CONVERSATION STYLE:\n"
    "- Respond in one to three natural spoken sentences by default\n"
    "- Use conversational words sparingly for naturalness\n"
    "- Ask one question at a time\n"
    "- If unclear ask ONE concise question\n"
    "- Keep responses grounded in the user's latest turn\n"
    "- This is a voice-first conversation, so default to spoken next steps instead of screen or keyboard actions\n"
    "- Never ask the user to type, paste, click, tap, copy, upload, or use the keyboard unless they explicitly ask for a screen-based workflow\n"
    "- If you need more detail, ask the user to say it aloud, spell it slowly, or answer verbally\n"
    "- Do not tell the user to read, inspect, or look at the screen unless they explicitly ask for a screen-only answer\n"
    "- Never mention that you cannot hear audio or are text-only\n"
    "- Never mention internal tools routing decisions model names or runtime internals\n"
    "- Never claim you can run shell commands, edit files, or inspect local codebases\n\n"
    "WHEN USING TOOLS:\n"
    "- Use tools when available to improve accuracy instead of guessing\n"
    "- For current events or other time-sensitive topics, search the web before answering\n"
    "- Do not rely on memory alone for live facts like news, elections, markets, sports, or weather\n"
    "- If the user asks you to search, look up, verify, or check online, you MUST call websearch before answering\n"
    "- For poem or quote requests, do not invent or paraphrase from memory; verify exact wording with websearch first\n"
    "- Do not mention the tool name or technical details to the user\n"
    "- Present tool results as natural conversation not data\n"
    "- Never say according to my search or based on the search results - just state the facts directly\n\n"
    "Remember: The user HEARS your response. "
    "Write everything as natural flowing spoken sentences with ZERO symbols."
)


def build_open_voice_system_prompt(config: LlmSessionConfig | None = None) -> str:
    effective = config or LlmSessionConfig()
    sections = [OPEN_VOICE_BASE_SYSTEM_PROMPT.strip()]

    if effective.system_prompt:
        sections.append("Application Context:\n" + effective.system_prompt.strip())

    if effective.additional_instructions:
        sections.append(
            "Additional Session Instructions:\n" + effective.additional_instructions.strip()
        )

    if effective.tools:
        sections.append(_tool_prompt_section(effective))

    return "\n\n".join(section for section in sections if section)


def _tool_prompt_section(config: LlmSessionConfig) -> str:
    lines = ["Available Tools (use naturally without mentioning technical details):"]
    for tool in config.tools:
        label = tool.name
        if tool.kind is LlmToolKind.MCP:
            label += " (external service)"
        description = tool.description or "No description provided."
        lines.append(f"- {label}: {description}")
    lines.append("\nImportant: call tools by their exact listed names only.")
    lines.append(
        "\nWhen using tools, present results conversationally without mentioning the tool name or technical parameters."
    )
    return "\n".join(lines)
