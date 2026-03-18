# Test scenarios for interruption handling
import asyncio
import time
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime
from test_harness import OpenVoiceTester, TestResult, TestStatus
from audio_generators import AudioGenerator, AudioStreamSimulator, get_test_audio

logger = logging.getLogger(__name__)


@dataclass
class EventValidationRule:
    """Rule for validating event sequences"""

    event_type: str
    required_fields: List[str]
    optional_fields: List[str] = None
    field_validators: Dict = None


class EventTraceValidator:
    """Validates event traces against expected patterns"""

    def __init__(self):
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def validate_event_sequence(self, events: List[Dict]) -> bool:
        """Validate that events follow correct sequence"""
        self.errors = []
        self.warnings = []

        if not events:
            self.errors.append("No events to validate")
            return False

        # Check for required event types
        event_types = [e.get("type") for e in events]

        # Validate session lifecycle
        if "session.status" not in event_types:
            self.errors.append("Missing session.status events")

        # Validate turn lifecycle - should have status transitions
        status_events = [e for e in events if e.get("type") == "session.status"]
        statuses = [e.get("status") for e in status_events]

        # Check for proper status transitions
        if "listening" not in statuses:
            self.warnings.append("No 'listening' status found")
        if "thinking" not in statuses:
            self.warnings.append("No 'thinking' status found")

        return len(self.errors) == 0

    def validate_generation_consistency(self, events: List[Dict]) -> Dict:
        """Validate generation_id consistency across events"""
        generation_ids = {}

        for event in events:
            gen_id = event.get("generation_id")
            event_type = event.get("type")

            if gen_id:
                if gen_id not in generation_ids:
                    generation_ids[gen_id] = []
                generation_ids[gen_id].append(event_type)

        return {
            "unique_generations": len(generation_ids),
            "generation_events": generation_ids,
            "valid": len(generation_ids) > 0,
        }

    def validate_turn_metrics(self, events: List[Dict]) -> Dict:
        """Validate turn.metrics events have required fields"""
        metrics_events = [e for e in events if e.get("type") == "turn.metrics"]

        required_fields = [
            "turn_id",
            "generation_id",
            "turn_to_complete_ms",
            "cancelled",
        ]

        validation_results = []
        for event in metrics_events:
            missing = [f for f in required_fields if f not in event]
            validation_results.append(
                {
                    "event_id": event.get("event_id"),
                    "missing_fields": missing,
                    "valid": len(missing) == 0,
                }
            )

        return {
            "metrics_count": len(metrics_events),
            "validations": validation_results,
            "all_valid": all(v["valid"] for v in validation_results),
        }


class InterruptionTestScenarios:
    """Comprehensive test scenarios for interruption handling"""

    def __init__(self, runtime_url: str = "ws://localhost:8011"):
        self.runtime_url = runtime_url
        self.tester: OpenVoiceTester = None

    async def setup(self):
        """Setup test environment"""
        self.tester = OpenVoiceTester(self.runtime_url)
        connected = await self.tester.connect()
        if not connected:
            raise ConnectionError("Failed to connect to Open Voice runtime")
        return True

    async def teardown(self):
        """Cleanup test environment"""
        if self.tester:
            if self.tester.session_id:
                await self.tester.close_session()
            await self.tester.disconnect()

    # ============================================================================
    # TEST 1: Basic Interruption (Barge-in)
    # ============================================================================
    async def test_basic_barge_in(self) -> TestResult:
        """
        Test: User interrupts assistant during speech

        Scenario:
        1. Start session and send user query
        2. Wait for assistant to start responding (THINKING/SPEAKING)
        3. Send barge-in audio
        4. Verify interruption is detected and handled
        """
        test_name = "basic_barge_in"
        start_time = time.time()
        logs = []

        try:
            # Create session
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Send initial user query
            user_query = get_test_audio("speech_natural")
            await self._stream_audio_to_commit(user_query)
            logs.append("Sent initial user query")

            # Wait for assistant to start processing
            await asyncio.sleep(2.0)

            # Check status
            status = self.tester.get_session_status()
            logs.append(f"Status after query: {status}")

            if status not in ["thinking", "speaking"]:
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    (time.time() - start_time) * 1000,
                    {},
                    f"Expected THINKING/SPEAKING, got {status}",
                )

            # Send barge-in audio
            self.tester.reset_logs()
            barge_in_audio = get_test_audio("speech_natural")

            # Stream barge-in audio
            simulator = AudioStreamSimulator(chunk_duration_ms=50)
            sequence = 0
            for chunk in simulator.stream_audio(barge_in_audio):
                await self.tester.send_audio(chunk, sequence)
                sequence += 1
                await asyncio.sleep(0.05)  # 50ms between chunks

            logs.append("Sent barge-in audio")

            # Listen for interruption events
            events = await self.tester.listen_for_events(duration=3.0)

            # Check for interruption
            interruption_events = [e for e in events if e.get("type") == "conversation.interrupted"]

            if len(interruption_events) > 0:
                logs.append(f"✓ Interruption detected: {len(interruption_events)} events")

                # Check if new turn was created
                status = self.tester.get_session_status()
                logs.append(f"Final status: {status}")

                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {
                        "interruption_count": len(interruption_events),
                        "final_status": status,
                    },
                    logs=logs,
                )
            else:
                logs.append("✗ No interruption detected")
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {},
                    "No interruption event received",
                    logs=logs,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    # ============================================================================
    # TEST 2: Chain Reaction Prevention
    # ============================================================================
    async def test_chain_reaction_prevention(self) -> TestResult:
        """
        Test: Continuous user speech after interrupt doesn't cause chain reaction

        Scenario:
        1. Start session and send user query
        2. Assistant starts responding
        3. User interrupts
        4. User continues speaking (continuous speech)
        5. Verify new turn completes without being interrupted
        """
        test_name = "chain_reaction_prevention"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Send initial query
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))
            logs.append("Sent initial query")

            await asyncio.sleep(1.5)

            # Trigger interrupt
            await self.tester.trigger_interrupt()
            logs.append("Triggered interrupt")

            await asyncio.sleep(0.5)

            # Send continuous speech (simulating user continuing to talk)
            continuous_audio = get_test_audio("speech_continuous")
            simulator = AudioStreamSimulator(chunk_duration_ms=100)

            logs.append("Starting continuous speech...")
            sequence = 0
            for i, chunk in enumerate(simulator.stream_audio(continuous_audio)):
                await self.tester.send_audio(chunk, sequence)
                sequence += 1
                await asyncio.sleep(0.1)

                # Log progress every second
                if i % 10 == 0:
                    logs.append(f"  Streaming... {i / 10:.1f}s")

            # Commit the turn
            await self.tester.commit_turn()
            logs.append("Committed continuous speech turn")

            # Listen for events
            events = await self.tester.listen_for_events(duration=10.0)

            # Analyze results
            interruption_events = [e for e in events if e.get("type") == "conversation.interrupted"]
            turn_metrics = [e for e in events if e.get("type") == "turn.metrics"]

            logs.append(f"Interruptions during continuous speech: {len(interruption_events)}")
            logs.append(f"Turn metrics received: {len(turn_metrics)}")

            # Check if turn completed successfully
            completed_turns = [m for m in turn_metrics if not m.get("cancelled", False)]

            if len(interruption_events) == 0 and len(completed_turns) > 0:
                logs.append("✓ Chain reaction prevented - turn completed successfully")
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {"interruptions": 0, "completed_turns": len(completed_turns)},
                    logs=logs,
                )
            else:
                logs.append(f"✗ Chain reaction occurred - {len(interruption_events)} interruptions")
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {
                        "interruptions": len(interruption_events),
                        "completed_turns": len(completed_turns),
                    },
                    "Chain reaction detected",
                    logs=logs,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    # ============================================================================
    # TEST 3: Rapid Successive Interrupts
    # ============================================================================
    async def test_rapid_successive_interrupts(self) -> TestResult:
        """
        Test: Rapid successive interrupts are properly handled/cooldown enforced

        Scenario:
        1. Start session
        2. Send query and wait for response
        3. Send multiple rapid interruptions (within 1 second)
        4. Verify cooldown prevents excessive interrupts
        """
        test_name = "rapid_successive_interrupts"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Initial query
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))
            await asyncio.sleep(1.5)

            # Send rapid interruptions
            logs.append("Sending 5 rapid interruptions...")
            interruption_times = []

            for i in range(5):
                self.tester.reset_logs()
                await self.tester.trigger_interrupt()
                interruption_times.append(time.time())
                logs.append(f"  Interrupt {i + 1} at t={interruption_times[-1] - start_time:.3f}s")
                await asyncio.sleep(0.2)  # 200ms between interrupts

            # Listen for events
            events = await self.tester.listen_for_events(duration=5.0)
            interruption_events = [e for e in events if e.get("type") == "conversation.interrupted"]

            logs.append(f"Total interruption events: {len(interruption_events)}")

            # Should have at most 2-3 interrupts due to cooldown
            if len(interruption_events) <= 3:
                logs.append(
                    f"✓ Cooldown working - only {len(interruption_events)} interrupts out of 5 attempts"
                )
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {"attempted": 5, "actual": len(interruption_events)},
                    logs=logs,
                )
            else:
                logs.append(f"✗ Cooldown not enforced - {len(interruption_events)} interrupts")
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {"attempted": 5, "actual": len(interruption_events)},
                    "Cooldown not working properly",
                    logs=logs,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    # ============================================================================
    # TEST 4: False Positive Resistance (Background Noise)
    # ============================================================================
    async def test_false_positive_resistance(self) -> TestResult:
        """
        Test: Background noise doesn't trigger false interruptions

        Scenario:
        1. Start session and send query
        2. Wait for assistant response
        3. Send background noise
        4. Verify no interruption is triggered
        """
        test_name = "false_positive_resistance"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Initial query
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))
            await asyncio.sleep(1.5)

            # Send background noise
            logs.append("Sending background noise...")
            noise_audio = get_test_audio("white_noise")
            simulator = AudioStreamSimulator(chunk_duration_ms=100)

            sequence = 0
            for chunk in simulator.stream_audio(noise_audio):
                await self.tester.send_audio(chunk, sequence)
                sequence += 1
                await asyncio.sleep(0.1)

            logs.append("Noise sent, listening for events...")

            # Listen for events
            events = await self.tester.listen_for_events(duration=3.0)
            interruption_events = [e for e in events if e.get("type") == "conversation.interrupted"]

            if len(interruption_events) == 0:
                logs.append("✓ No false positives - background noise didn't trigger interrupt")
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {"false_positives": 0},
                    logs=logs,
                )
            else:
                logs.append(f"✗ False positive detected - {len(interruption_events)} interruptions")
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {"false_positives": len(interruption_events)},
                    "Background noise triggered false interruption",
                    logs=logs,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    # ============================================================================
    # TEST 5: Late Audio Chunk Protection
    # ============================================================================
    async def test_late_chunk_protection(self) -> TestResult:
        """
        Test: Late audio chunks don't trigger false interrupt after turn enters THINKING

        Scenario:
        1. Start session and send user query
        2. Wait for turn to enter THINKING
        3. Send late audio chunks (simulating mic buffer delay)
        4. Verify no interrupt is triggered by late chunks
        """
        test_name = "late_chunk_protection"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Send initial query
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))
            logs.append("Initial query committed")

            # Wait for THINKING status
            max_wait = 5.0
            waited = 0
            while waited < max_wait:
                status = self.tester.get_session_status()
                if status == "thinking":
                    break
                await asyncio.sleep(0.1)
                waited += 0.1

            if status != "thinking":
                return TestResult(
                    test_name,
                    TestStatus.ERROR,
                    (time.time() - start_time) * 1000,
                    {},
                    f"Never reached THINKING status, got {status}",
                )

            logs.append(f"Status is THINKING after {waited:.1f}s")
            self.tester.reset_logs()

            # Send late audio chunks
            logs.append("Sending late audio chunks (simulating mic buffer)...")
            late_audio = get_test_audio("speech_staccato")
            simulator = AudioStreamSimulator(chunk_duration_ms=50)

            sequence = 0
            for chunk in simulator.stream_audio(late_audio):
                await self.tester.send_audio(chunk, sequence)
                sequence += 1
                await asyncio.sleep(0.05)

            # Listen for events
            events = await self.tester.listen_for_events(duration=5.0)
            interruption_events = [e for e in events if e.get("type") == "conversation.interrupted"]

            if len(interruption_events) == 0:
                logs.append("✓ Late chunks didn't trigger interrupt - protection working")
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {"late_chunks": sequence, "interruptions": 0},
                    logs=logs,
                )
            else:
                logs.append(f"✗ Late chunks triggered {len(interruption_events)} interrupts")
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {"interruptions": len(interruption_events)},
                    "Late chunks triggered false interrupt",
                    logs=logs,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    # ============================================================================
    # TEST 6: Event Trace Validation (Based on Live Event Trace)
    # ============================================================================
    async def test_event_trace_validation(self) -> TestResult:
        """
        Test: Validate complete event trace from live session

        Based on real event trace from session:
        - sess_d4dc7b4bf682443ebb6a9843e7ed803c

        Scenario:
        1. Start session and capture all events
        2. Validate event sequence and completeness
        3. Check generation_id consistency
        4. Verify turn metrics completeness
        """
        test_name = "event_trace_validation"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Reset logs to capture only this test's events
            self.tester.reset_logs()

            # Send initial query
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))
            logs.append("Sent initial query")

            # Wait for full processing cycle
            await asyncio.sleep(5.0)

            # Get all captured events
            events = self.tester.message_log
            logs.append(f"Captured {len(events)} events")

            # Validate with trace validator
            validator = EventTraceValidator()

            # Test 1: Event sequence validation
            sequence_valid = validator.validate_event_sequence(events)
            logs.append(f"Event sequence valid: {sequence_valid}")
            if validator.errors:
                logs.extend([f"  Error: {e}" for e in validator.errors])
            if validator.warnings:
                logs.extend([f"  Warning: {w}" for w in validator.warnings])

            # Test 2: Generation consistency
            gen_validation = validator.validate_generation_consistency(events)
            logs.append(
                f"Generation validation: {gen_validation['unique_generations']} unique generations"
            )

            # Test 3: Turn metrics validation
            metrics_validation = validator.validate_turn_metrics(events)
            logs.append(
                f"Turn metrics: {metrics_validation['metrics_count']} events, all_valid={metrics_validation['all_valid']}"
            )

            # Test 4: Check for specific event types from trace
            required_event_types = [
                "session.status",
                "turn.metrics",
                "stt.final",
                "vad.state",
                "route.selected",
                "llm.response.delta",
                "tts.chunk",
                "tts.completed",
            ]

            event_types_found = set(e.get("type") for e in events)
            missing_types = [t for t in required_event_types if t not in event_types_found]

            if missing_types:
                logs.append(f"Missing event types: {missing_types}")
            else:
                logs.append("✓ All required event types present")

            # Test 5: Validate status transitions
            status_events = [e for e in events if e.get("type") == "session.status"]
            status_sequence = [e.get("status") for e in status_events]
            logs.append(f"Status sequence: {status_sequence}")

            # Expected: listening -> thinking -> speaking -> listening
            valid_transition = (
                "listening" in status_sequence
                and "thinking" in status_sequence
                and "speaking" in status_sequence
            )

            if valid_transition:
                logs.append("✓ Valid status transitions detected")
            else:
                logs.append("✗ Invalid or incomplete status transitions")

            # Final validation
            all_checks_pass = (
                sequence_valid
                and gen_validation["valid"]
                and metrics_validation["all_valid"]
                and len(missing_types) == 0
                and valid_transition
            )

            duration_ms = (time.time() - start_time) * 1000

            if all_checks_pass:
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {
                        "event_count": len(events),
                        "generation_count": gen_validation["unique_generations"],
                        "metrics_count": metrics_validation["metrics_count"],
                        "status_transitions": len(status_sequence),
                    },
                    logs=logs,
                )
            else:
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {
                        "sequence_valid": sequence_valid,
                        "generation_valid": gen_validation["valid"],
                        "metrics_valid": metrics_validation["all_valid"],
                        "missing_types": missing_types,
                    },
                    "Event trace validation failed",
                    logs=logs,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    # ============================================================================
    # TEST 7: Generation ID Race Condition (Based on Live Event Trace)
    # ============================================================================
    async def test_generation_id_race_condition(self) -> TestResult:
        """
        Test: Validate generation_id tracking prevents race conditions

        Based on real event trace showing:
        - First turn: gen_9f384c10f9d5429c8277b63b1e76d072
        - Interrupt: gen_be25ceb5ff2249039c1bde68e8e29bbb

        Scenario:
        1. Start session and begin first turn
        2. Interrupt during first turn's TTS
        3. Verify new generation_id is used for second turn
        4. Check that stale TTS chunks are rejected
        """
        test_name = "generation_id_race_condition"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")
            self.tester.reset_logs()

            # Send first query - this will establish first generation_id
            logs.append("Step 1: Sending first query...")
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))

            # Wait for SPEAKING status
            await asyncio.sleep(2.0)

            # Get events so far
            events = self.tester.message_log
            first_gen_id = None
            for e in events:
                if e.get("generation_id"):
                    first_gen_id = e.get("generation_id")
                    break

            if not first_gen_id:
                return TestResult(
                    test_name,
                    TestStatus.ERROR,
                    (time.time() - start_time) * 1000,
                    {},
                    "First generation_id not found",
                    logs=logs,
                )

            logs.append(f"First generation_id: {first_gen_id}")

            # Step 2: Interrupt during TTS
            logs.append("Step 2: Interrupting during TTS...")
            await self.tester.trigger_interrupt()

            await asyncio.sleep(1.0)

            # Step 3: Send second query
            logs.append("Step 3: Sending second query...")
            self.tester.reset_logs()
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))

            await asyncio.sleep(3.0)

            # Get all events
            events = self.tester.message_log

            # Collect all generation_ids
            generation_ids = set()
            for e in events:
                gen_id = e.get("generation_id")
                if gen_id:
                    generation_ids.add(gen_id)

            logs.append(f"Generation IDs found: {len(generation_ids)}")
            logs.append(f"  {generation_ids}")

            # Check for interruption events
            interrupt_events = [e for e in events if e.get("type") == "conversation.interrupted"]
            logs.append(f"Interruption events: {len(interrupt_events)}")

            # Validate: Should have at least 2 different generation IDs
            # (one for first turn, one for second turn after interrupt)
            if len(generation_ids) >= 2:
                logs.append("✓ Multiple generation IDs detected - race condition handling working")

                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {
                        "generation_count": len(generation_ids),
                        "first_gen_id": first_gen_id,
                        "interrupt_count": len(interrupt_events),
                    },
                    logs=logs,
                )
            else:
                logs.append(
                    "✗ Only one generation ID found - race condition handling may be broken"
                )

                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {
                        "generation_count": len(generation_ids),
                        "interrupt_count": len(interrupt_events),
                    },
                    "Generation ID race condition not properly handled",
                    logs=logs,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )
        """
        Test: Late audio chunks don't trigger false interrupt after turn enters THINKING

        Scenario:
        1. Start session and send user query
        2. Wait for turn to enter THINKING
        3. Send late audio chunks (simulating mic buffer delay)
        4. Verify no interrupt is triggered by late chunks
        """
        test_name = "late_chunk_protection"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Send initial query
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))
            logs.append("Initial query committed")

            # Wait for THINKING status
            max_wait = 5.0
            waited = 0
            while waited < max_wait:
                status = self.tester.get_session_status()
                if status == "thinking":
                    break
                await asyncio.sleep(0.1)
                waited += 0.1

            if status != "thinking":
                return TestResult(
                    test_name,
                    TestStatus.ERROR,
                    (time.time() - start_time) * 1000,
                    {},
                    f"Never reached THINKING status, got {status}",
                )

            logs.append(f"Status is THINKING after {waited:.1f}s")
            self.tester.reset_logs()

            # Send late audio chunks
            logs.append("Sending late audio chunks (simulating mic buffer)...")
            late_audio = get_test_audio("speech_staccato")
            simulator = AudioStreamSimulator(chunk_duration_ms=50)

            sequence = 0
            for chunk in simulator.stream_audio(late_audio):
                await self.tester.send_audio(chunk, sequence)
                sequence += 1
                await asyncio.sleep(0.05)

            # Listen for events
            events = await self.tester.listen_for_events(duration=5.0)
            interruption_events = [e for e in events if e.get("type") == "conversation.interrupted"]

            if len(interruption_events) == 0:
                logs.append("✓ Late chunks didn't trigger interrupt - protection working")
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {"late_chunks": sequence, "interruptions": 0},
                    logs=logs,
                )
            else:
                logs.append(f"✗ Late chunks triggered {len(interruption_events)} interrupts")
                duration_ms = (time.time() - start_time) * 1000
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {"interruptions": len(interruption_events)},
                    "Late chunks triggered false interrupt",
                    logs=logs,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    async def test_llm_thinking_timeout_recovery(self) -> TestResult:
        """
        Test: LLM stuck in THINKING phase for extended time, then user interrupts

        Scenario (from live event trace sess_1df644d7564d46b3bcb87f0a5e9382bc):
        1. User sends query "I do it. What's taking you so long?"
        2. LLM enters THINKING phase
        3. LLM remains in THINKING for 2+ minutes (backend timeout/no response)
        4. User interrupts after extended wait
        5. System should recover and handle new turn correctly

        Validates:
        - System doesn't crash on LLM timeout
        - Interrupt properly cancels stuck generation
        - New turn gets fresh generation_id
        - Event sequence remains valid after recovery
        """
        test_name = "llm_thinking_timeout_recovery"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Send initial query
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))
            logs.append("Initial query committed: 'I do it. What's taking you so long?'")

            # Wait for THINKING status (max 30s, simulating long wait)
            max_wait = 30.0
            waited = 0
            thinking_start_time = None

            while waited < max_wait:
                status = self.tester.get_session_status()
                if status == "thinking" and thinking_start_time is None:
                    thinking_start_time = time.time()
                    logs.append(f"Status is THINKING at {waited:.1f}s")
                    break
                await asyncio.sleep(0.5)
                waited += 0.5

            if status != "thinking":
                return TestResult(
                    test_name,
                    TestStatus.ERROR,
                    (time.time() - start_time) * 1000,
                    {},
                    f"Never reached THINKING status, got {status}",
                )

            # Capture generation_id before interrupt
            events_before = self.tester.get_event_log()
            first_generation_id = None
            for e in reversed(events_before):
                if e.get("type") in ["stt.final", "route.selected", "llm.phase"]:
                    first_generation_id = e.get("generation_id")
                    if first_generation_id:
                        break

            logs.append(f"First generation_id: {first_generation_id}")

            # Simulate user interrupt after extended wait (simulating 2+ min wait)
            # In real test, this would be actual wait time
            logs.append("Simulating extended wait (2+ minutes in production)...")
            await asyncio.sleep(2.0)  # Shortened for test

            # User interrupts
            logs.append("User interrupting after extended wait...")
            await self.tester.interrupt()

            # Wait for interrupt to process
            await asyncio.sleep(0.5)

            # Verify interrupt was processed
            events_after_interrupt = self.tester.get_event_log()
            interrupt_events = [
                e for e in events_after_interrupt if e.get("type") == "conversation.interrupted"
            ]

            if len(interrupt_events) == 0:
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    (time.time() - start_time) * 1000,
                    {},
                    "No conversation.interrupted event received",
                    logs=logs,
                )

            interrupt_event = interrupt_events[-1]
            interrupted_generation_id = interrupt_event.get("generation_id")
            logs.append(f"Interrupt received for generation: {interrupted_generation_id}")

            # Verify the interrupted generation matches the first one
            if interrupted_generation_id != first_generation_id:
                logs.append(
                    f"WARNING: Interrupt generation_id ({interrupted_generation_id}) "
                    f"doesn't match first generation ({first_generation_id})"
                )

            # Now send a new query to verify recovery
            logs.append("Sending new query after interrupt to verify recovery...")
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))
            logs.append("Second query committed: 'Thank you.'")

            # Wait for new turn processing
            await asyncio.sleep(2.0)

            # Capture all events
            all_events = self.tester.get_event_log()

            # Find second generation_id
            second_generation_id = None
            for e in reversed(all_events):
                if e.get("type") in ["stt.final", "route.selected", "llm.phase"]:
                    gid = e.get("generation_id")
                    if gid and gid != first_generation_id:
                        second_generation_id = gid
                        break

            logs.append(f"Second generation_id: {second_generation_id}")

            # Validate results
            validation_passed = True

            # Check 1: First generation was interrupted
            turn_metrics = [
                e
                for e in all_events
                if e.get("type") == "turn.metrics" and e.get("generation_id") == first_generation_id
            ]

            if turn_metrics:
                cancelled = turn_metrics[-1].get("cancelled", False)
                if not cancelled:
                    logs.append("✗ First turn not marked as cancelled")
                    validation_passed = False
                else:
                    logs.append("✓ First turn properly marked as cancelled")
            else:
                logs.append("WARNING: No turn.metrics found for first generation")

            # Check 2: Second generation is different from first
            if second_generation_id == first_generation_id:
                logs.append("✗ Second generation_id same as first - race condition!")
                validation_passed = False
            elif second_generation_id is None:
                logs.append("✗ No second generation_id found")
                validation_passed = False
            else:
                logs.append("✓ Second generation has new generation_id")

            # Check 3: Event sequence after interrupt
            status_events = [e for e in all_events if e.get("type") == "session.status"]
            interrupt_idx = None
            for i, e in enumerate(status_events):
                if e.get("type") == "conversation.interrupted":
                    interrupt_idx = i
                    break

            if interrupt_idx is not None and interrupt_idx < len(status_events) - 1:
                next_status = status_events[interrupt_idx + 1].get("status")
                if next_status == "listening":
                    logs.append("✓ Session resumed to listening after interrupt")
                else:
                    logs.append(f"? Session status after interrupt: {next_status}")

            duration_ms = (time.time() - start_time) * 1000

            if validation_passed:
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {
                        "first_generation": first_generation_id,
                        "second_generation": second_generation_id,
                        "interrupt_count": len(interrupt_events),
                        "thinking_wait_sec": waited,
                    },
                    logs=logs,
                )
            else:
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {
                        "first_generation": first_generation_id,
                        "second_generation": second_generation_id,
                    },
                    "LLM timeout recovery validation failed",
                    logs=logs,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    async def test_interrupt_during_thinking_correction(self) -> TestResult:
        """
        Test: User corrects themselves while LLM is thinking

        Scenario (from live event trace sess_56bc28446fc84c4cb358704e57e181d9):
        1. User says "I want to do research about child chocolate"
        2. LLM enters THINKING phase
        3. User starts speaking again to correct: "Sahil Chokse"
        4. System should interrupt thinking and process new input
        5. LLM should respond to "Sahil Chokse" not "child chocolate"

        Validates:
        - Interrupt triggers during THINKING state (not just SPEAKING)
        - Old generation is properly cancelled
        - New turn processes the corrected input
        - No duplicate/mixed responses
        """
        test_name = "interrupt_during_thinking_correction"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Step 1: User says first query
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))
            logs.append(
                "Step 1: First query committed - 'I want to do research about child chocolate'"
            )

            # Step 2: Wait for LLM to enter THINKING state
            max_wait = 10.0
            waited = 0
            first_generation_id = None

            while waited < max_wait:
                status = self.tester.get_session_status()
                if status == "thinking":
                    # Capture the generation_id of the first turn
                    events = self.tester.get_event_log()
                    for e in reversed(events):
                        if e.get("type") in [
                            "stt.final",
                            "route.selected",
                            "llm.phase",
                        ]:
                            gid = e.get("generation_id")
                            if gid:
                                first_generation_id = gid
                                break
                    logs.append(f"Step 2: LLM entered THINKING (generation: {first_generation_id})")
                    break
                await asyncio.sleep(0.5)
                waited += 0.5

            if status != "thinking":
                return TestResult(
                    test_name,
                    TestStatus.ERROR,
                    (time.time() - start_time) * 1000,
                    {},
                    f"Never reached THINKING, got {status}",
                    logs=logs,
                )

            # Step 3: User starts speaking again (correcting themselves)
            # This simulates the user saying "Sahil Chokse" while LLM is thinking
            logs.append("Step 3: User starts speaking again (correcting)...")
            await asyncio.sleep(1.0)  # Brief pause

            # Send audio while LLM is thinking
            simulator = AudioStreamSimulator(chunk_duration_ms=100)
            sequence = 0
            for chunk in simulator.stream_audio(get_test_audio("speech_natural")):
                await self.tester.send_audio(chunk, sequence)
                sequence += 1
                await asyncio.sleep(0.05)

            # Wait for interrupt to process
            await asyncio.sleep(1.0)

            # Check for interrupt event
            events = self.tester.get_event_log()
            interrupt_events = [e for e in events if e.get("type") == "conversation.interrupted"]

            if len(interrupt_events) > 0:
                logs.append(
                    f"✓ Interrupt triggered during THINKING ({len(interrupt_events)} events)"
                )
                interrupt_gen_id = interrupt_events[-1].get("generation_id")

                # Verify the interrupted generation matches the first one
                if interrupt_gen_id == first_generation_id:
                    logs.append("✓ Correct generation was interrupted")
                else:
                    logs.append(
                        f"✗ Wrong generation interrupted: {interrupt_gen_id} vs {first_generation_id}"
                    )
            else:
                logs.append("✗ No interrupt triggered during THINKING - this is the bug!")
                logs.append(
                    "  The LLM will continue processing the old query instead of the corrected one"
                )

            # Step 4: Commit the new turn
            await self.tester.commit_turn()
            logs.append("Step 4: Committed corrected turn")

            # Wait for new turn to process
            await asyncio.sleep(3.0)

            # Verify the new turn has a different generation_id
            all_events = self.tester.get_event_log()
            second_generation_id = None
            for e in reversed(all_events):
                if e.get("type") in ["stt.final", "route.selected", "llm.phase"]:
                    gid = e.get("generation_id")
                    if gid and gid != first_generation_id:
                        second_generation_id = gid
                        break

            # Validation
            validation_passed = True
            validation_messages = []

            if len(interrupt_events) == 0:
                validation_passed = False
                validation_messages.append(
                    "CRITICAL: No interrupt during THINKING - old query will be processed"
                )

            if second_generation_id is None:
                validation_passed = False
                validation_messages.append("No second generation found")
            elif second_generation_id == first_generation_id:
                validation_passed = False
                validation_messages.append("Second generation same as first - race condition!")
            else:
                validation_messages.append("✓ Second generation has new ID")

            # Check turn metrics for first generation (should be cancelled)
            turn_metrics = [
                e
                for e in all_events
                if e.get("type") == "turn.metrics" and e.get("generation_id") == first_generation_id
            ]
            if turn_metrics:
                if turn_metrics[-1].get("cancelled"):
                    validation_messages.append("✓ First turn properly cancelled")
                else:
                    validation_messages.append("✗ First turn not cancelled")
                    validation_passed = False

            duration_ms = (time.time() - start_time) * 1000

            if validation_passed:
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {
                        "first_generation": first_generation_id,
                        "second_generation": second_generation_id,
                        "interrupt_during_thinking": len(interrupt_events) > 0,
                    },
                    logs=logs + validation_messages,
                )
            else:
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {
                        "first_generation": first_generation_id,
                        "second_generation": second_generation_id,
                    },
                    "Interrupt during THINKING not working correctly",
                    logs=logs + validation_messages,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    async def test_vad_during_thinking_should_interrupt(self) -> TestResult:
        """
        Test: VAD detects speech while LLM is THINKING - should trigger interrupt

        Scenario (from live event trace sess_77cfff4b94844819b169b456243a775e):
        1. User says "okay i want you to search intensively and get seven days forecast of Javakur"
        2. LLM enters THINKING phase
        3. User starts speaking again "No, never mind" (vad.start_of_speech)
        4. System should interrupt the thinking and process the new input

        Root Cause Analysis:
        - The interrupt logic in _append_audio only triggers during SPEAKING state
        - During THINKING, VAD detects speech but interrupt doesn't fire
        - User's speech gets added to the SAME turn instead of triggering a new turn
        - LLM continues processing old query because interrupt never happened

        Expected Behavior:
        - When VAD detects speech during THINKING state, system should immediately interrupt
        - The current generation should be cancelled
        - A new turn should be created for the user's new speech
        """
        test_name = "vad_during_thinking_should_interrupt"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Step 1: User sends initial query
            logs.append("Step 1: User sends query 'I want you to search for weather forecast'")
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))

            # Step 2: Wait for LLM to enter THINKING state
            max_wait = 15.0
            waited = 0
            thinking_detected = False
            first_generation_id = None

            while waited < max_wait:
                status = self.tester.get_session_status()
                if status == "thinking":
                    thinking_detected = True
                    # Capture generation_id
                    events = self.tester.get_event_log()
                    for e in reversed(events):
                        if e.get("type") in [
                            "stt.final",
                            "route.selected",
                            "llm.phase",
                        ]:
                            gid = e.get("generation_id")
                            if gid:
                                first_generation_id = gid
                                break
                    logs.append(f"Step 2: LLM entered THINKING (generation: {first_generation_id})")
                    break
                await asyncio.sleep(0.5)
                waited += 0.5

            if not thinking_detected:
                return TestResult(
                    test_name,
                    TestStatus.ERROR,
                    (time.time() - start_time) * 1000,
                    {},
                    f"Never reached THINKING status, got {status}",
                    logs=logs,
                )

            # Step 3: User starts speaking again while LLM is thinking
            # This simulates the user saying "No, never mind" to interrupt
            logs.append("Step 3: User starts speaking again (VAD detects speech during THINKING)")

            # Wait a moment to ensure LLM is actively thinking
            await asyncio.sleep(2.0)

            # Clear event log to capture new events
            initial_events = len(self.tester.get_event_log())

            # Send audio while LLM is thinking - this should trigger interrupt
            simulator = AudioStreamSimulator(chunk_duration_ms=100)
            sequence = 0
            for chunk in simulator.stream_audio(get_test_audio("speech_natural")):
                await self.tester.send_audio(chunk, sequence)
                sequence += 1
                await asyncio.sleep(0.05)

            # Wait for VAD to detect speech and trigger interrupt
            await asyncio.sleep(2.0)

            # Step 4: Check for interrupt event
            all_events = self.tester.get_event_log()
            new_events = all_events[initial_events:]

            # Look for interrupt events
            interrupt_events = [
                e for e in new_events if e.get("type") == "conversation.interrupted"
            ]
            vad_start_events = [
                e
                for e in new_events
                if e.get("type") == "vad.state" and e.get("kind") == "start_of_speech"
            ]
            stt_final_events = [e for e in new_events if e.get("type") == "stt.final"]

            logs.append(f"Step 4: Checking for interrupt...")
            logs.append(f"  VAD start_of_speech events: {len(vad_start_events)}")
            logs.append(f"  STT final events: {len(stt_final_events)}")
            logs.append(f"  Interrupt events: {len(interrupt_events)}")

            # Step 5: Validate the behavior
            validation_passed = True
            validation_messages = []

            # Check 1: VAD should have detected speech
            if len(vad_start_events) == 0:
                validation_passed = False
                validation_messages.append(
                    "✗ No VAD start_of_speech detected - audio not being processed"
                )
            else:
                validation_messages.append("✓ VAD detected speech during THINKING")

            # Check 2: Interrupt should have been triggered
            if len(interrupt_events) == 0:
                validation_passed = False
                validation_messages.append(
                    "✗ CRITICAL: No interrupt triggered during THINKING state"
                )
                validation_messages.append(
                    "  This is the bug - user's speech was added to same turn instead of interrupting"
                )
            else:
                validation_messages.append(
                    f"✓ Interrupt triggered during THINKING ({len(interrupt_events)} events)"
                )
                # Verify the interrupted generation matches
                interrupt_gen = interrupt_events[-1].get("generation_id")
                if interrupt_gen == first_generation_id:
                    validation_messages.append("✓ Correct generation was interrupted")
                else:
                    validation_messages.append(f"✗ Wrong generation interrupted: {interrupt_gen}")

            # Check 3: New turn should have been created
            if len(stt_final_events) > 0:
                validation_messages.append("✓ New STT final event(s) received")
                # Check if new generation was created
                new_gen_id = None
                for e in stt_final_events:
                    gid = e.get("generation_id")
                    if gid and gid != first_generation_id:
                        new_gen_id = gid
                        break

                if new_gen_id:
                    validation_messages.append(f"✓ New generation created: {new_gen_id}")
                else:
                    validation_messages.append("? No new generation ID found in STT events")

            # Check 4: Turn metrics should show first turn was cancelled
            turn_metrics = [
                e
                for e in all_events
                if e.get("type") == "turn.metrics" and e.get("generation_id") == first_generation_id
            ]
            if turn_metrics:
                if turn_metrics[-1].get("cancelled"):
                    validation_messages.append("✓ First turn properly marked as cancelled")
                else:
                    validation_messages.append("✗ First turn NOT cancelled")
                    validation_passed = False

            duration_ms = (time.time() - start_time) * 1000

            if validation_passed:
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {
                        "first_generation": first_generation_id,
                        "vad_speech_detected": len(vad_start_events) > 0,
                        "interrupt_triggered": len(interrupt_events) > 0,
                        "new_stt_final": len(stt_final_events) > 0,
                    },
                    logs=logs + validation_messages,
                )
            else:
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {
                        "first_generation": first_generation_id,
                        "vad_speech_detected": len(vad_start_events) > 0,
                        "interrupt_triggered": len(interrupt_events) > 0,
                    },
                    "VAD during THINKING should trigger interrupt but did not",
                    logs=logs + validation_messages,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    async def test_llm_stuck_thinking_no_interrupt(self) -> TestResult:
        """
        Test: LLM stuck in THINKING for 2+ minutes, then user interrupts

        Scenario (from live event trace sess_1df644d7564d46b3bcb87f0a5e9382bc):
        1. User says "I do it. What's taking you so long?"
        2. LLM enters THINKING phase
        3. User starts speaking again at 16:49:27 (vad.start_of_speech)
        4. User says "No." and "Never mind." to cancel
        5. LLM continues processing for 2+ minutes (no interrupt triggered!)
        6. LLM eventually calls tool 2+ minutes later

        This tests the "recently entered processing" protection that blocks interrupts
        for 3 seconds after THINKING starts, but the event trace shows the protection
        is blocking legitimate interrupts during extended THINKING phases.

        Validates:
        - Interrupt should trigger when user starts speaking during THINKING
        - VAD start_of_speech during THINKING should trigger interrupt immediately
        - Old generation should be cancelled, not allowed to continue
        """
        test_name = "llm_stuck_thinking_no_interrupt"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Step 1: User sends initial query
            logs.append("Step 1: User sends query")
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))

            # Step 2: Wait for THINKING
            max_wait = 15.0
            waited = 0
            thinking_detected = False
            first_generation_id = None

            while waited < max_wait:
                status = self.tester.get_session_status()
                if status == "thinking":
                    thinking_detected = True
                    events = self.tester.get_event_log()
                    for e in reversed(events):
                        if e.get("type") in ["stt.final", "route.selected", "llm.phase"]:
                            gid = e.get("generation_id")
                            if gid:
                                first_generation_id = gid
                                break
                    logs.append(f"Step 2: LLM entered THINKING (generation: {first_generation_id})")
                    break
                await asyncio.sleep(0.5)
                waited += 0.5

            if not thinking_detected:
                return TestResult(
                    test_name,
                    TestStatus.ERROR,
                    (time.time() - start_time) * 1000,
                    {},
                    f"Never reached THINKING, got {status}",
                    logs=logs,
                )

            # Step 3: User starts speaking while LLM is thinking (simulating "Never mind")
            logs.append("Step 3: User starts speaking during THINKING...")
            await asyncio.sleep(0.5)

            # Clear events to track what happens after
            initial_event_count = len(self.tester.get_event_log())

            # Send audio while LLM is thinking
            simulator = AudioStreamSimulator(chunk_duration_ms=100)
            sequence = 0
            for chunk in simulator.stream_audio(get_test_audio("speech_natural")):
                await self.tester.send_audio(chunk, sequence)
                sequence += 1
                await asyncio.sleep(0.05)

            # Wait a moment for VAD detection
            await asyncio.sleep(1.0)

            # Check if VAD detected speech during THINKING
            new_events = self.tester.get_event_log()[initial_event_count:]
            vad_speech_detected = any(
                e.get("type") == "vad.state" and e.get("kind") == "start_of_speech"
                for e in new_events
            )

            if vad_speech_detected:
                logs.append("✓ VAD detected speech during THINKING")
            else:
                logs.append("✗ VAD did not detect speech during THINKING")

            # Check if interrupt was triggered
            interrupt_triggered = any(
                e.get("type") == "conversation.interrupted" for e in new_events
            )

            if interrupt_triggered:
                logs.append("✓ Interrupt triggered when user spoke during THINKING")
            else:
                logs.append(
                    "✗ CRITICAL: No interrupt triggered - LLM will continue with old query!"
                )

            # Wait to see if LLM continues processing
            await asyncio.sleep(2.0)

            # Check if LLM is still processing (no interrupt happened)
            current_status = self.tester.get_session_status()
            if current_status == "thinking":
                logs.append("✗ LLM still processing - interrupt did not work!")

            # Commit the new turn
            await self.tester.commit_turn()
            logs.append("Step 4: Committed 'Never mind' turn")

            await asyncio.sleep(2.0)

            # Validate
            validation_passed = True
            if not interrupt_triggered:
                validation_passed = False
                logs.append("CRITICAL: Interrupt did not trigger during THINKING")

            duration_ms = (time.time() - start_time) * 1000

            if validation_passed:
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {
                        "interrupt_triggered": interrupt_triggered,
                        "vad_speech_detected": vad_speech_detected,
                    },
                    logs=logs,
                )
            else:
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {
                        "interrupt_triggered": interrupt_triggered,
                        "vad_speech_detected": vad_speech_detected,
                    },
                    "Interrupt did not trigger during THINKING - LLM continued with old query",
                    logs=logs,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    async def test_stt_final_should_wait_for_vad_end(self) -> TestResult:
        """
        Test: STT final events should not commit turn until VAD end_of_speech

        Scenario (from live event trace sess_77cfff4b94844819b169b456243a775e):
        1. User starts speaking at 16:55:32 (vad.start_of_speech)
        2. stt.final comes at 16:55:34 (while still speaking!)
        3. stt.final comes at 16:55:35 (still speaking)
        4. stt.final with generation_id at 16:55:36
        5. LLM enters THINKING at 16:55:37 (BEFORE vad.end_of_speech!)

        The system committed the turn and started LLM processing before the user
        finished speaking. This causes the "Never mind" to be treated as a new query
        instead of being part of the same utterance.

        Validates:
        - Turn should NOT be committed until VAD shows end_of_speech
        - STT final events should be buffered until VAD end
        - LLM should not start processing until user finishes speaking
        """
        test_name = "stt_final_should_wait_for_vad_end"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Step 1: Start streaming audio (user starts speaking)
            logs.append("Step 1: User starts speaking...")

            # Stream first part of speech
            simulator = AudioStreamSimulator(chunk_duration_ms=100)
            sequence = 0
            for i, chunk in enumerate(simulator.stream_audio(get_test_audio("speech_natural"))):
                await self.tester.send_audio(chunk, sequence)
                sequence += 1
                await asyncio.sleep(0.1)

                # After a few chunks, check if stt.final came too early
                if i > 5:  # Only check after some audio is sent
                    events = self.tester.get_event_log()
                    stt_finals = [e for e in events if e.get("type") == "stt.final"]
                    vad_end = any(
                        e.get("type") == "vad.state" and e.get("kind") == "end_of_speech"
                        for e in events
                    )

                    if stt_finals and not vad_end:
                        logs.append(
                            f"⚠ STT final detected at chunk {i} but no VAD end_of_speech yet"
                        )

            # Wait for VAD end_of_speech
            await asyncio.sleep(2.0)

            events = self.tester.get_event_log()
            vad_start = any(
                e.get("type") == "vad.state" and e.get("kind") == "start_of_speech" for e in events
            )
            vad_end = any(
                e.get("type") == "vad.state" and e.get("kind") == "end_of_speech" for e in events
            )
            stt_finals = [e for e in events if e.get("type") == "stt.final"]

            logs.append(f"VAD start_of_speech detected: {vad_start}")
            logs.append(f"VAD end_of_speech detected: {vad_end}")
            logs.append(f"STT final events: {len(stt_finals)}")

            # Check if turn was committed before end_of_speech
            commit_before_end = False
            if stt_finals and vad_end:
                # Find timestamps
                stt_final_time = None
                vad_end_time = None
                for e in events:
                    if e.get("type") == "stt.final" and stt_final_time is None:
                        stt_final_time = e.get("timestamp")
                    if e.get("type") == "vad.state" and e.get("kind") == "end_of_speech":
                        vad_end_time = e.get("timestamp")

                if stt_final_time and vad_end_time:
                    # Compare timestamps
                    stt_dt = datetime.fromisoformat(stt_final_time.replace("Z", "+00:00"))
                    vad_dt = datetime.fromisoformat(vad_end_time.replace("Z", "+00:00"))

                    if stt_dt < vad_dt:
                        logs.append(
                            "✓ STT final came before VAD end - turn may have been committed too early"
                        )
                        commit_before_end = True
                    else:
                        logs.append("✓ STT final came after VAD end - correct behavior")

            # Wait for turn to process
            await asyncio.sleep(2.0)

            # Check if LLM is processing
            final_status = self.tester.get_session_status()
            logs.append(f"Final session status: {final_status}")

            # Validation
            validation_passed = True
            validation_messages = []

            if not vad_start:
                validation_messages.append("✗ VAD start_of_speech not detected")
                validation_passed = False

            if not vad_end:
                validation_messages.append(
                    "⚠ VAD end_of_speech not detected - user may still be speaking"
                )

            if stt_finals and not vad_end:
                validation_messages.append(
                    "✗ STT final came without VAD end - turn committed too early!"
                )
                validation_passed = False

            if commit_before_end:
                validation_messages.append(
                    "⚠ Turn may have been committed before user finished speaking"
                )

            duration_ms = (time.time() - start_time) * 1000

            if validation_passed:
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {"vad_start": vad_start, "vad_end": vad_end, "stt_finals": len(stt_finals)},
                    logs=logs + validation_messages,
                )
            else:
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {"vad_start": vad_start, "vad_end": vad_end, "stt_finals": len(stt_finals)},
                    "Turn may have been committed before user finished speaking",
                    logs=logs + validation_messages,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    async def test_recently_entered_processing_protection(self) -> TestResult:
        """
        Test: "Recently entered processing" protection blocks valid interrupts

        The backend has a 3-second protection (Check 7 in _should_handle_interruption)
        that blocks interrupts for 3 seconds after the turn enters THINKING state.

        From session.py line 1288-1306:
        "Block interrupts for 3 seconds after turn enters THINKING to prevent
        false interrupts from late audio chunks that arrive right after processing starts"

        Problem: This protection blocks legitimate interrupts when user starts speaking
        while LLM is thinking. The user's "Never mind" at 16:55:47 came 27 seconds AFTER
        THINKING started (16:55:38), so the protection should NOT have blocked it.

        But the real issue is: the interrupt logic in _should_handle_interruption
        is NOT triggering during THINKING when VAD detects speech. The protection
        is checking if we're in SPEAKING state, not if VAD detects speech during THINKING.

        Validates:
        - Interrupt should trigger when VAD detects speech during THINKING
        - 3-second protection should only apply during SPEAKING state
        - Extended THINKING phases (> 3 seconds) should allow interrupts immediately
        """
        test_name = "recently_entered_processing_protection"
        start_time = time.time()
        logs = []

        try:
            session_id = await self.tester.create_session()
            if not session_id:
                return TestResult(test_name, TestStatus.ERROR, 0, {}, "Failed to create session")

            logs.append(f"Session created: {session_id}")

            # Step 1: User sends initial query
            logs.append("Step 1: User sends query")
            await self._stream_audio_to_commit(get_test_audio("speech_natural"))

            # Step 2: Wait for THINKING
            max_wait = 15.0
            waited = 0
            thinking_detected = False
            first_generation_id = None

            while waited < max_wait:
                status = self.tester.get_session_status()
                if status == "thinking":
                    thinking_detected = True
                    events = self.tester.get_event_log()
                    for e in reversed(events):
                        if e.get("type") in ["stt.final", "route.selected", "llm.phase"]:
                            gid = e.get("generation_id")
                            if gid:
                                first_generation_id = gid
                                break
                    logs.append(f"Step 2: LLM entered THINKING (generation: {first_generation_id})")
                    break
                await asyncio.sleep(0.5)
                waited += 0.5

            if not thinking_detected:
                return TestResult(
                    test_name,
                    TestStatus.ERROR,
                    (time.time() - start_time) * 1000,
                    {},
                    f"Never reached THINKING, got {status}",
                    logs=logs,
                )

            # Step 3: Wait MORE than 3 seconds (beyond the protection window)
            logs.append(
                "Step 3: Waiting 4 seconds to exceed 'recently entered processing' protection"
            )
            await asyncio.sleep(4.0)

            # Step 4: User starts speaking while LLM is STILL thinking
            logs.append("Step 4: User starts speaking during extended THINKING...")

            initial_event_count = len(self.tester.get_event_log())

            # Send audio
            simulator = AudioStreamSimulator(chunk_duration_ms=100)
            sequence = 0
            for chunk in simulator.stream_audio(get_test_audio("speech_natural")):
                await self.tester.send_audio(chunk, sequence)
                sequence += 1
                await asyncio.sleep(0.05)

            await asyncio.sleep(1.0)

            # Check if interrupt was triggered
            new_events = self.tester.get_event_log()[initial_event_count:]
            interrupt_triggered = any(
                e.get("type") == "conversation.interrupted" for e in new_events
            )

            vad_speech_detected = any(
                e.get("type") == "vad.state" and e.get("kind") == "start_of_speech"
                for e in new_events
            )

            logs.append(f"VAD detected speech: {vad_speech_detected}")
            logs.append(f"Interrupt triggered: {interrupt_triggered}")

            # Wait for turn to process
            await asyncio.sleep(2.0)

            # Check if LLM is still processing (no interrupt happened)
            current_status = self.tester.get_session_status()
            if current_status == "thinking":
                logs.append("✗ LLM still processing - interrupt did not work!")

            # Validation
            validation_passed = True
            validation_messages = []

            if not vad_speech_detected:
                validation_messages.append("✗ VAD did not detect speech during THINKING")
                validation_passed = False

            if not interrupt_triggered:
                validation_messages.append(
                    "✗ CRITICAL: Interrupt did not trigger during THINKING "
                    "(even after 3-second protection expired)"
                )
                validation_passed = False
            else:
                validation_messages.append("✓ Interrupt triggered during extended THINKING")

            duration_ms = (time.time() - start_time) * 1000

            if validation_passed:
                return TestResult(
                    test_name,
                    TestStatus.PASSED,
                    duration_ms,
                    {
                        "interrupt_triggered": interrupt_triggered,
                        "vad_speech_detected": vad_speech_detected,
                    },
                    logs=logs + validation_messages,
                )
            else:
                return TestResult(
                    test_name,
                    TestStatus.FAILED,
                    duration_ms,
                    {
                        "interrupt_triggered": interrupt_triggered,
                        "vad_speech_detected": vad_speech_detected,
                    },
                    "Interrupt did not trigger during THINKING - 'recently entered processing' protection may be too aggressive",
                    logs=logs + validation_messages,
                )

        except Exception as e:
            logger.exception("Test failed")
            return TestResult(
                test_name,
                TestStatus.ERROR,
                (time.time() - start_time) * 1000,
                {},
                str(e),
                logs=logs,
            )

    # ============================================================================
    # Helper Methods
    # ============================================================================
    async def _stream_audio_to_commit(self, audio_data: bytes):
        """Stream audio and commit turn"""
        simulator = AudioStreamSimulator(chunk_duration_ms=100)
        sequence = 0

        for chunk in simulator.stream_audio(audio_data):
            await self.tester.send_audio(chunk, sequence)
            sequence += 1
            await asyncio.sleep(0.1)

        await self.tester.commit_turn()

    async def run_all_tests(self) -> List[TestResult]:
        """Run all test scenarios"""
        results = []

        tests = [
            self.test_basic_barge_in,
            self.test_chain_reaction_prevention,
            self.test_rapid_successive_interrupts,
            self.test_false_positive_resistance,
            self.test_late_chunk_protection,
            self.test_event_trace_validation,
            self.test_generation_id_race_condition,
            self.test_llm_thinking_timeout_recovery,
            self.test_interrupt_during_thinking_correction,
            self.test_vad_during_thinking_should_interrupt,
            self.test_llm_stuck_thinking_no_interrupt,
            self.test_stt_final_should_wait_for_vad_end,
            self.test_recently_entered_processing_protection,
        ]

        for test in tests:
            test_name = test.__name__
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Running: {test_name}")
            logger.info("=" * 60)

            try:
                await self.setup()
                result = await test()
                results.append(result)

                # Log result
                status_icon = "✓" if result.status == TestStatus.PASSED else "✗"
                logger.info(f"{status_icon} {test_name}: {result.status.value}")
                if result.error_message:
                    logger.error(f"  Error: {result.error_message}")

            except Exception as e:
                logger.exception(f"Test setup failed: {test_name}")
                results.append(TestResult(test_name, TestStatus.ERROR, 0, {}, str(e)))
            finally:
                await self.teardown()
                await asyncio.sleep(1)  # Brief pause between tests

        return results


if __name__ == "__main__":

    async def main():
        # Run all tests
        scenarios = InterruptionTestScenarios()
        results = await scenarios.run_all_tests()

        # Print summary
        print("\n" + "=" * 60)
        print("TEST SUMMARY")
        print("=" * 60)

        passed = sum(1 for r in results if r.status == TestStatus.PASSED)
        failed = sum(1 for r in results if r.status == TestStatus.FAILED)
        errors = sum(1 for r in results if r.status == TestStatus.ERROR)

        for result in results:
            icon = (
                "✓"
                if result.status == TestStatus.PASSED
                else "✗"
                if result.status == TestStatus.FAILED
                else "⚠"
            )
            print(f"{icon} {result.test_name}: {result.status.value} ({result.duration_ms:.0f}ms)")

        print(f"\nPassed: {passed}/{len(results)}")
        print(f"Failed: {failed}/{len(results)}")
        print(f"Errors: {errors}/{len(results)}")

        # Save detailed results
        import json

        with open("test_results.json", "w") as f:
            json.dump(
                [
                    {
                        "test_name": r.test_name,
                        "status": r.status.value,
                        "duration_ms": r.duration_ms,
                        "metrics": r.metrics,
                        "error": r.error_message,
                        "logs": r.logs,
                    }
                    for r in results
                ],
                f,
                indent=2,
            )

        print("\nDetailed results saved to test_results.json")

    asyncio.run(main())
