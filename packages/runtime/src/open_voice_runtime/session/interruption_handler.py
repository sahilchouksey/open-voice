"""
Interruption handling architecture inspired by LiveKit Agents.

Key principles:
1. Interruption can happen at ANY stage (THINKING, SPEAKING, ROUTING, LOADING, etc.)
2. After interrupt, discard ALL in-flight audio (don't let it become a new turn)
3. Wait for VAD silence (end-of-speech) before accepting new input
4. Process complete new utterance as one turn
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any


class InterruptionMode(str, Enum):
    DISABLED = "disabled"
    IMMEDIATE = "immediate"  # Current behavior - interrupt on any speech
    ADAPTIVE = "adaptive"  # Future: use ML to detect true barge-in


@dataclass
class InterruptionState:
    """Tracks interruption state for a session."""

    mode: InterruptionMode = InterruptionMode.IMMEDIATE
    last_interrupt_at: float = 0.0
    cooldown_ms: int = (
        1000  # P1-FIX-003: Increased from 300ms to prevent rapid successive interrupts
    )
    collecting_after_interrupt: bool = False
    # True = we're collecting speech after interrupt, wait for VAD end

    def can_interrupt(self, now: float) -> bool:
        """Check if we can interrupt based on cooldown."""
        if self.mode == InterruptionMode.DISABLED:
            return False
        time_since = now - self.last_interrupt_at
        return time_since >= (self.cooldown_ms / 1000.0)

    def mark_interrupted(self, now: float) -> None:
        """Mark that we just interrupted."""
        self.last_interrupt_at = now
        self.collecting_after_interrupt = True

    def mark_collected(self) -> None:
        """Mark that we've collected the post-interrupt speech."""
        self.collecting_after_interrupt = False


class UnifiedInterruptionHandler:
    """
    Handles interruption at ANY point in the pipeline.

    Inspired by LiveKit's interruption model:
    - Can interrupt during any active state
    - After interrupt, enters 'collecting' mode
    - Waits for complete utterance (VAD end) before processing
    """

    def __init__(self):
        self._states: dict[str, InterruptionState] = {}

    def get_state(self, session_id: str) -> InterruptionState:
        """Get or create interruption state for session."""
        if session_id not in self._states:
            self._states[session_id] = InterruptionState()
        return self._states[session_id]

    def should_interrupt_at_point(
        self,
        session_id: str,
        point: str,  # "audio_append", "audio_commit", "routing", "llm_stream", etc.
        current_status: str,
        has_speech: bool,
        now: float,
    ) -> bool:
        """
        Check if we should interrupt at this point.

        Args:
            session_id: Session ID
            point: Where in the pipeline we are
            current_status: Current session status
            has_speech: Whether VAD detected speech
            now: Current timestamp

        Returns:
            True if we should interrupt
        """
        state = self.get_state(session_id)

        # Can't interrupt if disabled
        if state.mode == InterruptionMode.DISABLED:
            return False

        # Can't interrupt if in cooldown
        if not state.can_interrupt(now):
            return False

        # Can only interrupt during active states
        active_states = {"THINKING", "SPEAKING", "ROUTING", "LOADING"}
        if current_status not in active_states:
            return False

        # Need speech to interrupt (unless in specific points)
        if not has_speech and point not in {"routing", "llm_stream"}:
            return False

        return True

    def handle_interrupt(
        self,
        session_id: str,
        reason: str,
        now: float,
    ) -> dict[str, Any]:
        """
        Handle interruption.

        Returns context for what needs to be cleaned up:
        - Cancel response task
        - Reset STT stream
        - Reset VAD stream
        - Clear turn buffer
        - Enter collecting mode
        """
        state = self.get_state(session_id)
        state.mark_interrupted(now)

        return {
            "cancel_task": True,
            "reset_stt": True,
            "reset_vad": True,
            "clear_buffer": True,
            "enter_collecting_mode": True,
            "reason": reason,
        }

    def should_accept_commit(
        self,
        session_id: str,
        vad_ended: bool,
    ) -> bool:
        """
        Check if we should accept a commit at this point.

        If we're in post-interrupt collecting mode, only accept on VAD end.
        """
        state = self.get_state(session_id)

        if not state.collecting_after_interrupt:
            # Normal operation - accept commit
            return True

        # In collecting mode - only accept on VAD end
        if vad_ended:
            state.mark_collected()
            return True

        return False

    def cleanup_session(self, session_id: str) -> None:
        """Cleanup interruption state when session closes."""
        self._states.pop(session_id, None)


# Usage example in session.py:
"""
# In _append_audio:
handler = UnifiedInterruptionHandler()
state = handler.get_state(session_id)

if handler.should_interrupt_at_point(
    session_id, "audio_append", status, has_speech, now
):
    context = handler.handle_interrupt(session_id, "send_now", now)
    # ... execute cleanup from context ...
    return

# In _commit_audio or auto-commit:
if not handler.should_accept_commit(session_id, vad_ended):
    # Don't commit yet, wait for VAD end
    return
"""
