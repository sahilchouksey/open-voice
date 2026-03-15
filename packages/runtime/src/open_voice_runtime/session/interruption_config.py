from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class InterruptionConfig:
    """Configuration for interruption handling.

    Based on LiveKit's interruption model for voice AI.
    """

    mode: str = "immediate"  # "immediate", "adaptive", "disabled"
    min_duration: float = 0.3  # Minimum speech duration (seconds) to count as interruption
    min_words: int = 0  # Minimum words (requires STT) - 0 to disable
    cooldown_ms: int = (
        1000  # Cooldown between interrupts (milliseconds) - P1-FIX-003: Increased from 300ms
    )

    @classmethod
    def from_payload(cls, payload: dict | None) -> "InterruptionConfig":
        if payload is None:
            return cls()
        return cls(
            mode=payload.get("mode", cls.mode),
            min_duration=payload.get("min_duration", cls.min_duration),
            min_words=payload.get("min_words", cls.min_words),
            cooldown_ms=payload.get("cooldown_ms", cls.cooldown_ms),
        )


@dataclass(slots=True)
class EndPointingConfig:
    """Configuration for end-of-turn detection (endpointing).

    Based on LiveKit's endpointing model for voice AI.
    """

    mode: str = "fixed"  # "fixed", "dynamic"
    min_delay: float = 0.5  # Minimum time after speech to declare turn complete (seconds)
    max_delay: float = 3.0  # Maximum time to wait before terminating turn (seconds)

    @classmethod
    def from_payload(cls, payload: dict | None) -> "EndPointingConfig":
        if payload is None:
            return cls()
        return cls(
            mode=payload.get("mode", cls.mode),
            min_delay=payload.get("min_delay", cls.min_delay),
            max_delay=payload.get("max_delay", cls.max_delay),
        )
