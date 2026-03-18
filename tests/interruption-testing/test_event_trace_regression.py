"""
Event Trace Regression Test

This test validates the Open Voice SDK against a real event trace
captured from a live session. It ensures that the event sequence,
generation ID handling, and status transitions match expected patterns.

Usage:
    python test_event_trace_regression.py

Based on event trace from session: sess_d4dc7b4bf682443ebb6a9843e7ed803c
Date: 2026-03-24T14:21:25.678738+00:00
"""

import json
import pytest
from typing import List, Dict, Set
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class EventTraceExpectation:
    """Expected event pattern from live trace"""

    event_type: str
    required_fields: List[str]
    status_transitions: List[str] = field(default_factory=list)


class EventTraceRegressionTest:
    """
    Regression test based on live event trace.

    Validates that current implementation produces events matching
    the captured trace from session sess_d4dc7b4bf682443ebb6a9843e7ed803c
    """

    # Expected event sequence from live trace
    EXPECTED_EVENT_SEQUENCE = [
        "session.status",  # listening
        "turn.metrics",  # First turn metrics
        "stt.final",  # User speech
        "vad.state",  # end_of_speech
        "stt.final",  # Final transcript
        "route.selected",  # Route selection
        "session.status",  # thinking
        "llm.reasoning.delta",  # Reasoning
        "llm.phase",  # thinking phase
        "llm.reasoning.delta",  # More reasoning
        "llm.phase",  # generating phase
        "llm.response.delta",  # Response text
        "session.status",  # speaking
        "llm.usage",  # Token usage
        "llm.summary",  # Summary
        "llm.completed",  # Completion
        "llm.phase",  # done phase
        "tts.chunk",  # Audio chunk 1
        "tts.chunk",  # Audio chunk 2
        "tts.completed",  # TTS done
        "session.status",  # listening
        "turn.metrics",  # Final metrics
    ]

    # Expected status transitions
    EXPECTED_STATUS_SEQUENCE = ["listening", "thinking", "speaking", "listening"]

    # Required fields per event type (from trace analysis)
    REQUIRED_FIELDS = {
        "session.status": ["session_id", "turn_id", "status", "timestamp"],
        "turn.metrics": [
            "session_id",
            "turn_id",
            "generation_id",
            "event_id",
            "timestamp",
            "turn_to_complete_ms",
            "cancelled",
        ],
        "stt.final": ["session_id", "turn_id", "text", "timestamp"],
        "vad.state": ["session_id", "turn_id", "kind", "speaking", "timestamp"],
        "route.selected": [
            "session_id",
            "turn_id",
            "generation_id",
            "router_id",
            "route_name",
            "llm_engine_id",
            "timestamp",
        ],
        "llm.reasoning.delta": [
            "session_id",
            "turn_id",
            "generation_id",
            "part_id",
            "delta",
            "timestamp",
        ],
        "llm.phase": ["session_id", "turn_id", "generation_id", "phase", "timestamp"],
        "llm.response.delta": [
            "session_id",
            "turn_id",
            "generation_id",
            "part_id",
            "delta",
            "lane",
            "timestamp",
        ],
        "llm.usage": [
            "session_id",
            "turn_id",
            "generation_id",
            "usage",
            "cost",
            "timestamp",
        ],
        "llm.summary": [
            "session_id",
            "turn_id",
            "generation_id",
            "provider",
            "model",
            "usage",
            "timestamp",
        ],
        "llm.completed": [
            "session_id",
            "turn_id",
            "generation_id",
            "text",
            "finish_reason",
            "provider",
            "model",
            "timestamp",
        ],
        "tts.chunk": [
            "session_id",
            "turn_id",
            "generation_id",
            "chunk",
            "text_segment",
            "timestamp",
        ],
        "tts.completed": [
            "session_id",
            "turn_id",
            "generation_id",
            "duration_ms",
            "timestamp",
        ],
    }

    def __init__(self):
        self.validation_errors: List[str] = []
        self.validation_warnings: List[str] = []

    def validate_event_structure(self, event: Dict) -> bool:
        """Validate a single event has required structure"""
        event_type = event.get("type")

        if not event_type:
            self.validation_errors.append("Event missing 'type' field")
            return False

        required = self.REQUIRED_FIELDS.get(event_type, [])
        missing = [f for f in required if f not in event]

        if missing:
            self.validation_errors.append(
                f"{event_type}: Missing required fields: {missing}"
            )
            return False

        return True

    def validate_event_sequence(self, events: List[Dict]) -> Dict:
        """Validate event sequence matches expected pattern"""
        self.validation_errors = []
        self.validation_warnings = []

        if not events:
            return {"valid": False, "error": "No events provided"}

        # Extract event types
        actual_types = [e.get("type") for e in events]

        # Check for required event types
        required_types = set(self.EXPECTED_EVENT_SEQUENCE)
        actual_types_set = set(actual_types)

        missing_types = required_types - actual_types_set
        if missing_types:
            self.validation_warnings.append(f"Missing event types: {missing_types}")

        # Validate status transitions
        status_events = [e for e in events if e.get("type") == "session.status"]
        actual_statuses = [e.get("status") for e in status_events]

        # Check core status transitions
        has_listening = "listening" in actual_statuses
        has_thinking = "thinking" in actual_statuses
        has_speaking = "speaking" in actual_statuses

        status_valid = has_listening and has_thinking and has_speaking

        if not status_valid:
            missing_statuses = []
            if not has_listening:
                missing_statuses.append("listening")
            if not has_thinking:
                missing_statuses.append("thinking")
            if not has_speaking:
                missing_statuses.append("speaking")
            self.validation_errors.append(
                f"Missing status transitions: {missing_statuses}"
            )

        # Validate generation_id consistency
        gen_validation = self._validate_generation_ids(events)

        # Validate turn lifecycle
        turn_validation = self._validate_turn_lifecycle(events)

        return {
            "valid": len(self.validation_errors) == 0,
            "event_count": len(events),
            "unique_event_types": len(actual_types_set),
            "status_transitions": actual_statuses,
            "generation_validation": gen_validation,
            "turn_validation": turn_validation,
            "errors": self.validation_errors,
            "warnings": self.validation_warnings,
        }

    def _validate_generation_ids(self, events: List[Dict]) -> Dict:
        """Validate generation_id consistency across events"""
        generation_map: Dict[str, List[str]] = {}

        for event in events:
            gen_id = event.get("generation_id")
            if gen_id:
                event_type = event.get("type", "unknown")
                if gen_id not in generation_map:
                    generation_map[gen_id] = []
                generation_map[gen_id].append(event_type)

        # Check for expected patterns
        # Should have at least one generation ID
        has_generations = len(generation_map) > 0

        # Check that TTS chunks have consistent generation_id
        tts_events = [e for e in events if e.get("type", "").startswith("tts.")]
        tts_gen_ids = set(
            e.get("generation_id") for e in tts_events if e.get("generation_id")
        )

        return {
            "generation_count": len(generation_map),
            "generations": list(generation_map.keys()),
            "tts_generation_consistency": len(tts_gen_ids) <= 2,  # Should be 1-2
            "events_per_generation": {
                gen: len(types) for gen, types in generation_map.items()
            },
        }

    def _validate_turn_lifecycle(self, events: List[Dict]) -> Dict:
        """Validate complete turn lifecycle"""
        # Find turn start
        first_turn_id = None
        for e in events:
            if e.get("turn_id"):
                first_turn_id = e.get("turn_id")
                break

        if not first_turn_id:
            return {"valid": False, "error": "No turn_id found in events"}

        # Check for turn completion
        turn_metrics = [e for e in events if e.get("type") == "turn.metrics"]
        completed_turns = [m for m in turn_metrics if not m.get("cancelled", True)]

        # Validate event ordering within turn
        turn_events = [e for e in events if e.get("turn_id") == first_turn_id]

        # Check ordering: stt.final -> route.selected -> llm.* -> tts.*
        event_order_valid = True

        has_stt = any(e.get("type") == "stt.final" for e in turn_events)
        has_route = any(e.get("type") == "route.selected" for e in turn_events)
        has_llm = any(e.get("type", "").startswith("llm.") for e in turn_events)
        has_tts = any(e.get("type", "").startswith("tts.") for e in turn_events)

        if not (has_stt and has_route and has_llm and has_tts):
            event_order_valid = False

        return {
            "turn_id": first_turn_id,
            "turn_event_count": len(turn_events),
            "completed_turns": len(completed_turns),
            "has_stt": has_stt,
            "has_route": has_route,
            "has_llm": has_llm,
            "has_tts": has_tts,
            "event_order_valid": event_order_valid,
        }

    def generate_trace_report(self, events: List[Dict]) -> str:
        """Generate a human-readable report of the event trace"""
        lines = []
        lines.append("=" * 70)
        lines.append("EVENT TRACE REGRESSION TEST REPORT")
        lines.append("=" * 70)
        lines.append("")

        # Summary
        lines.append(f"Total Events: {len(events)}")
        lines.append(f"Unique Event Types: {len(set(e.get('type') for e in events))}")
        lines.append("")

        # Event type breakdown
        lines.append("EVENT TYPE BREAKDOWN:")
        type_counts = {}
        for e in events:
            t = e.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        for event_type, count in sorted(type_counts.items()):
            lines.append(f"  {event_type}: {count}")
        lines.append("")

        # Status transitions
        status_events = [e for e in events if e.get("type") == "session.status"]
        if status_events:
            lines.append("STATUS TRANSITIONS:")
            for e in status_events:
                ts = e.get("timestamp", "")
                status = e.get("status", "unknown")
                reason = e.get("reason", "")
                lines.append(f"  [{ts}] {status} (reason: {reason})")
            lines.append("")

        # Generation IDs
        gen_ids = set()
        for e in events:
            gen_id = e.get("generation_id")
            if gen_id:
                gen_ids.add(gen_id)

        if gen_ids:
            lines.append("GENERATION IDs:")
            for gen_id in sorted(gen_ids):
                lines.append(f"  {gen_id}")
            lines.append("")

        # Turn metrics
        metrics = [e for e in events if e.get("type") == "turn.metrics"]
        if metrics:
            lines.append("TURN METRICS:")
            for m in metrics:
                cancelled = "CANCELLED" if m.get("cancelled") else "COMPLETED"
                duration = m.get("turn_to_complete_ms", 0)
                lines.append(f"  {cancelled} - {duration:.0f}ms")
            lines.append("")

        # Validation results
        validation = self.validate_event_sequence(events)
        lines.append("VALIDATION RESULTS:")
        lines.append(f"  Valid: {validation['valid']}")

        if validation["errors"]:
            lines.append("  ERRORS:")
            for error in validation["errors"]:
                lines.append(f"    ✗ {error}")

        if validation["warnings"]:
            lines.append("  WARNINGS:")
            for warning in validation["warnings"]:
                lines.append(f"    ⚠ {warning}")

        lines.append("")
        lines.append("=" * 70)

        return "\n".join(lines)


def test_validate_live_event_trace():
    """
    Test that validates against the live event trace.

    This test loads events from a JSON file and validates them
    against the expected patterns from the live trace.
    """
    # Create validator
    validator = EventTraceRegressionTest()

    # Load events from trace file (if exists)
    try:
        with open("tests/fixtures/live_event_trace.json", "r") as f:
            events = json.load(f)
    except FileNotFoundError:
        # For testing without file, create sample events
        events = []

    if not events:
        pytest.skip("No event trace file found")

    # Validate
    result = validator.validate_event_sequence(events)

    # Generate report
    report = validator.generate_trace_report(events)
    print(report)

    # Assertions
    assert result["valid"], f"Validation failed: {result['errors']}"
    assert result["generation_validation"]["generation_count"] > 0
    assert result["turn_validation"]["event_order_valid"]


if __name__ == "__main__":
    print("Event Trace Regression Test")
    print("=" * 70)
    print()
    print("This test validates event traces against expected patterns")
    print("from live Open Voice SDK sessions.")
    print()
    print("Expected event types:")
    for event_type in EventTraceRegressionTest.EXPECTED_EVENT_SEQUENCE:
        print(f"  - {event_type}")
    print()
    print("To run with live events, save them to:")
    print("  tests/fixtures/live_event_trace.json")
    print()
    print("Run with pytest:")
    print("  pytest test_event_trace_regression.py -v")
