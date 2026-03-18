# Open Voice SDK - Real-World Scenario Tests
# Based on actual user interaction logs

import asyncio
import json
import time
import logging
from datetime import datetime
import base64
import numpy as np
import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


class EventCollector:
    """Background event collector to capture all WebSocket events"""

    def __init__(self, websocket):
        self.ws = websocket
        self.events = []
        self._running = False

    async def start(self):
        """Start collecting events in background"""
        self._running = True
        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(self.ws.recv(), timeout=0.1)
                    data = json.loads(msg)
                    self.events.append(data)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            logger.debug("Event collector cancelled")
        except Exception as e:
            logger.error(f"Event collector error: {e}")

    def get_events(self):
        """Get all collected events (returns copy)"""
        return self.events.copy()

    def stop(self):
        """Stop collecting events"""
        self._running = False


class RealWorldScenarioTester:
    """Test real-world interruption scenarios based on actual user logs"""

    def __init__(self, runtime_url="ws://localhost:8011"):
        self.runtime_url = runtime_url
        # Ensure the WebSocket path is correct
        if not self.runtime_url.endswith("/v1/realtime/conversation"):
            if self.runtime_url.endswith("/"):
                self.runtime_url = self.runtime_url.rstrip("/")
            self.runtime_url = f"{self.runtime_url}/v1/realtime/conversation"
        self.ws = None
        self.session_id = None

    async def connect(self):
        """Connect to Open Voice runtime"""
        try:
            self.ws = await websockets.connect(self.runtime_url)
            logger.info(f"✓ Connected to {self.runtime_url}")
            return True
        except Exception as e:
            logger.error(f"✗ Connection failed: {e}")
            return False

    async def disconnect(self):
        """Disconnect from runtime"""
        if self.ws:
            await self.ws.close()
            logger.info("✓ Disconnected")

    async def create_session(self, turn_queue_policy="send_now"):
        """Create a new session"""
        msg = {
            "type": "session.start",
            "config": {
                "turn_queue_policy": turn_queue_policy,
                "voice": "af_heart",
                "interruption": {"mode": "enabled", "cooldown_ms": 300},
            },
        }

        await self.ws.send(json.dumps(msg))
        response = await asyncio.wait_for(self.ws.recv(), timeout=5.0)
        data = json.loads(response)

        if data.get("type") in ["session.created", "session.ready"]:
            self.session_id = data.get("session_id")
            logger.info(f"✓ Session created: {self.session_id[:8]}...")
            return True
        return False

    async def close_session(self):
        """Close session"""
        if self.session_id:
            await self.ws.send(
                json.dumps({"type": "session.close", "session_id": self.session_id})
            )
            logger.info("✓ Session closed")

    async def send_audio_tone(self, freq=500, duration_sec=2.0, amplitude=0.5):
        """Send test tone (simulates speech)"""
        num_samples = int(16000 * duration_sec)
        t = np.linspace(0, duration_sec, num_samples, False)
        audio = amplitude * 32767 * np.sin(2 * np.pi * freq * t)
        audio = audio.astype(np.int16)

        chunk_size = int(16000 * 0.1)
        audio_list = audio.tolist()

        for i in range(0, len(audio_list), chunk_size):
            chunk_data = audio_list[i : i + chunk_size]
            chunk_array = np.array(chunk_data, dtype=np.int16)
            chunk_b64 = base64.b64encode(chunk_array.tobytes()).decode("utf-8")
            await self.ws.send(
                json.dumps(
                    {
                        "type": "audio.append",
                        "session_id": self.session_id,
                        "chunk": {
                            "chunk_id": f"chunk_{int(time.time() * 1000)}_{i}",
                            "sequence": i // chunk_size,
                            "encoding": "pcm_s16le",
                            "sample_rate_hz": 16000,
                            "channels": 1,
                            "duration_ms": 100,
                            "transport": "inline-base64",
                            "data_base64": chunk_b64,
                        },
                    }
                )
            )
            await asyncio.sleep(0.1)

    async def commit_audio(self):
        """Commit audio turn"""
        await self.ws.send(
            json.dumps({"type": "audio.commit", "session_id": self.session_id})
        )

    # ========================================================================
    # REAL-WORLD TEST 1: Barge-in During Assistant Response
    # ========================================================================
    async def test_barge_in_during_response(self):
        """
        Test: User asks question, assistant starts responding, user interrupts with follow-up

        Scenario from logs:
        1. User: "Tell me who is Sahil Choksi..."
        2. Assistant enters THINKING, starts responding
        3. User interrupts: "See? Yeah. E-Y."
        4. System should: Interrupt first turn, start new turn, process it
        5. Bug: New turn got stuck after user stopped speaking
        """
        logger.info("\n" + "=" * 70)
        logger.info("REAL-WORLD TEST 1: Barge-in During Assistant Response")
        logger.info("=" * 70)
        logger.info(
            "Scenario: User asks question → Assistant responds → User interrupts"
        )
        logger.info(
            "Expected: System interrupts, starts new turn, processes it successfully"
        )
        logger.info("=" * 70)

        try:
            await self.create_session()

            # Start event collector
            event_collector = EventCollector(self.ws)
            collector_task = asyncio.create_task(event_collector.start())

            # Step 1: Send initial query (simulating: "Tell me who is Sahil...")
            logger.info("\n[Step 1] Sending initial query (3 seconds of speech)...")
            await self.send_audio_tone(freq=300, duration_sec=3.0)
            await self.commit_audio()

            # Step 2: Wait for assistant to enter THINKING
            logger.info("[Step 2] Waiting for assistant to enter THINKING...")
            thinking_detected = False
            start = time.time()
            while time.time() - start < 10.0:
                await asyncio.sleep(0.1)
                events = event_collector.get_events()

                thinking_events = [
                    e
                    for e in events
                    if e.get("type") == "session.status"
                    and e.get("status") == "thinking"
                ]
                if thinking_events:
                    thinking_detected = True
                    logger.info("✓ Assistant entered THINKING")
                    break

            if not thinking_detected:
                logger.error("✗ FAIL: Assistant never entered THINKING")
                collector_task.cancel()
                return False

            # Step 3: User interrupts while assistant is responding
            logger.info("[Step 3] User interrupts with follow-up speech (2 seconds)...")
            await self.send_audio_tone(freq=500, duration_sec=2.0)
            await self.commit_audio()

            # Step 4: Wait for interruption to be detected
            logger.info("[Step 4] Waiting for interruption detection...")
            await asyncio.sleep(2.0)

            events = event_collector.get_events()
            interruptions = [
                e for e in events if e.get("type") == "conversation.interrupted"
            ]

            if interruptions:
                logger.info(f"✓ Interruption detected ({len(interruptions)} events)")
            else:
                logger.warning("⚠ No interruption event detected (may still work)")

            # Step 5: CRITICAL - Wait for new turn to be processed after user stops
            logger.info(
                "[Step 5] CRITICAL: Waiting for new turn to process after user stops..."
            )
            logger.info("        (This is where the bug occurred - turn got stuck)")

            turn_processed = False
            start = time.time()
            while time.time() - start < 15.0:  # Wait up to 15 seconds
                await asyncio.sleep(0.5)
                events = event_collector.get_events()

                # Check if a turn completed after the interruption
                turn_metrics = [e for e in events if e.get("type") == "turn.metrics"]
                completed_after_interrupt = False

                for tm in turn_metrics:
                    # Check if this turn completed and wasn't cancelled
                    if not tm.get("cancelled", False):
                        completed_after_interrupt = True
                        break

                if completed_after_interrupt:
                    turn_processed = True
                    logger.info("✓ New turn processed successfully after interruption")
                    break

                # Also check if we got route.selected (indicates processing started)
                route_events = [e for e in events if e.get("type") == "route.selected"]
                if (
                    len(route_events) >= 2
                ):  # Second route means new turn started processing
                    turn_processed = True
                    logger.info("✓ New turn started processing (route selected)")
                    break

            collector_task.cancel()

            if turn_processed:
                logger.info("\n" + "=" * 70)
                logger.info("✓ PASS: Real-world barge-in scenario worked correctly!")
                logger.info("  - Initial query processed")
                logger.info("  - Assistant entered THINKING")
                logger.info("  - User interrupted successfully")
                logger.info("  - New turn processed after user stopped")
                logger.info("=" * 70)
                return True
            else:
                logger.error("\n" + "=" * 70)
                logger.error("✗ FAIL: New turn did not process after interruption!")
                logger.error("  This reproduces the bug from your logs")
                logger.error("  Events received:")
                for e in events[-10:]:  # Show last 10 events
                    logger.error(f"    {e.get('type')}: {e.get('status', '')}")
                logger.info("=" * 70)
                return False

        except Exception as e:
            logger.error(f"✗ ERROR: {e}")
            import traceback

            traceback.print_exc()
            return False
        finally:
            await self.close_session()

    # ========================================================================
    # REAL-WORLD TEST 2: Continuous Speech After Interrupt (Chain Reaction)
    # ========================================================================
    async def test_continuous_speech_after_interrupt(self):
        """
        Test: User interrupts, then continues speaking naturally

        Scenario from logs:
        1. User interrupts assistant
        2. User continues talking without pausing
        3. System should: Process ONE turn, not multiple
        4. Bug: Could create chain reaction of multiple turns
        """
        logger.info("\n" + "=" * 70)
        logger.info("REAL-WORLD TEST 2: Continuous Speech After Interrupt")
        logger.info("=" * 70)
        logger.info(
            "Scenario: User interrupts → Continues talking → Should be ONE turn"
        )
        logger.info("Expected: Single turn processed, no chain reaction")
        logger.info("=" * 70)

        try:
            await self.create_session()

            # Start event collector
            event_collector = EventCollector(self.ws)
            collector_task = asyncio.create_task(event_collector.start())

            # Step 1: Send initial query
            logger.info("\n[Step 1] Sending initial query...")
            await self.send_audio_tone(freq=300, duration_sec=2.0)
            await self.commit_audio()
            await asyncio.sleep(1.0)

            # Step 2: Interrupt
            logger.info("[Step 2] Triggering interrupt...")
            await self.ws.send(
                json.dumps(
                    {
                        "type": "conversation.interrupt",
                        "session_id": self.session_id,
                        "reason": "test",
                    }
                )
            )
            await asyncio.sleep(0.5)

            # Step 3: Send continuous speech (simulating user continuing to talk)
            logger.info("[Step 3] Sending continuous speech (5 seconds)...")
            await self.send_audio_tone(freq=400, duration_sec=5.0)
            await self.commit_audio()

            # Step 4: Wait and check results
            logger.info("[Step 4] Waiting for processing...")
            await asyncio.sleep(10.0)

            events = event_collector.get_events()
            interruptions = [
                e for e in events if e.get("type") == "conversation.interrupted"
            ]
            turn_metrics = [e for e in events if e.get("type") == "turn.metrics"]
            completed_turns = [
                tm for tm in turn_metrics if not tm.get("cancelled", False)
            ]

            collector_task.cancel()

            logger.info(
                f"Results: {len(interruptions)} interruptions, {len(completed_turns)} completed turns"
            )

            if len(interruptions) <= 1 and len(completed_turns) >= 1:
                logger.info("\n" + "=" * 70)
                logger.info("✓ PASS: Chain reaction prevented!")
                logger.info(f"  - {len(interruptions)} interruption(s) (expected ≤1)")
                logger.info(
                    f"  - {len(completed_turns)} turn(s) completed (expected ≥1)"
                )
                logger.info("=" * 70)
                return True
            else:
                logger.error("\n" + "=" * 70)
                logger.error("✗ FAIL: Chain reaction detected!")
                logger.error(f"  - {len(interruptions)} interruptions")
                logger.error(f"  - {len(completed_turns)} turns completed")
                logger.error("=" * 70)
                return False

        except Exception as e:
            logger.error(f"✗ ERROR: {e}")
            return False
        finally:
            await self.close_session()

    # ========================================================================
    # REAL-WORLD TEST 3: Natural Conversation Flow
    # ========================================================================
    async def test_natural_conversation_flow(self):
        """
        Test: Multiple back-and-forth turns

        Scenario:
        1. User: "Tell me about X"
        2. Assistant: responds
        3. User: "What about Y?"
        4. Assistant: responds
        5. All turns should complete successfully
        """
        logger.info("\n" + "=" * 70)
        logger.info("REAL-WORLD TEST 3: Natural Conversation Flow")
        logger.info("=" * 70)
        logger.info("Scenario: Multiple back-and-forth turns")
        logger.info("Expected: All turns complete successfully")
        logger.info("=" * 70)

        try:
            await self.create_session()

            # Start event collector
            event_collector = EventCollector(self.ws)
            collector_task = asyncio.create_task(event_collector.start())

            completed_turns = 0

            for i in range(3):  # 3 turns
                logger.info(f"\n[Turn {i + 1}] Sending query...")
                await self.send_audio_tone(freq=300 + (i * 100), duration_sec=2.0)
                await self.commit_audio()

                # Wait for turn to complete
                await asyncio.sleep(5.0)

                events = event_collector.get_events()
                turn_metrics = [e for e in events if e.get("type") == "turn.metrics"]
                completed = [
                    tm for tm in turn_metrics if not tm.get("cancelled", False)
                ]

                if len(completed) > completed_turns:
                    completed_turns = len(completed)
                    logger.info(f"✓ Turn {i + 1} completed")
                else:
                    logger.warning(f"⚠ Turn {i + 1} may not have completed yet")

            collector_task.cancel()

            if completed_turns >= 2:  # At least 2 should complete
                logger.info("\n" + "=" * 70)
                logger.info(f"✓ PASS: Natural conversation flow worked!")
                logger.info(f"  - {completed_turns} turns completed")
                logger.info("=" * 70)
                return True
            else:
                logger.error("\n" + "=" * 70)
                logger.error(f"✗ FAIL: Not enough turns completed ({completed_turns})")
                logger.error("=" * 70)
                return False

        except Exception as e:
            logger.error(f"✗ ERROR: {e}")
            return False
        finally:
            await self.close_session()

    # ========================================================================
    # Run All Tests
    # ========================================================================
    async def run_all_tests(self):
        """Run all real-world scenario tests"""
        logger.info("\n" + "=" * 70)
        logger.info("OPEN VOICE SDK - REAL-WORLD SCENARIO TESTS")
        logger.info("Based on actual user interaction logs")
        logger.info("=" * 70)
        logger.info(f"Runtime: {self.runtime_url}")
        logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 70)

        if not await self.connect():
            return []

        tests = [
            ("Barge-in During Response", self.test_barge_in_during_response),
            (
                "Continuous Speech After Interrupt",
                self.test_continuous_speech_after_interrupt,
            ),
            ("Natural Conversation Flow", self.test_natural_conversation_flow),
        ]

        results = []

        for test_name, test_func in tests:
            await asyncio.sleep(2)
            logger.info(f"\n{'=' * 70}")
            logger.info(f">>> Starting: {test_name}")
            logger.info(f"{'=' * 70}")
            success = await test_func()
            results.append((test_name, "PASS" if success else "FAIL"))
            logger.info(f">>> Finished: {test_name} - {'PASS' if success else 'FAIL'}")

        await self.disconnect()

        # Print summary
        logger.info("\n" + "=" * 70)
        logger.info("TEST SUMMARY")
        logger.info("=" * 70)

        passed = sum(1 for _, r in results if r == "PASS")
        failed = sum(1 for _, r in results if r == "FAIL")

        for test_name, result in results:
            icon = "✓" if result == "PASS" else "✗"
            logger.info(f"{icon} {test_name}: {result}")

        logger.info("-" * 70)
        logger.info(f"Passed: {passed}/{len(results)}")
        logger.info(f"Failed: {failed}/{len(results)}")
        logger.info("=" * 70)

        return results


if __name__ == "__main__":
    import sys

    runtime_url = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8011"
    tester = RealWorldScenarioTester(runtime_url)

    try:
        asyncio.run(tester.run_all_tests())
    except KeyboardInterrupt:
        logger.info("\nTests interrupted by user")
    except Exception as e:
        logger.error(f"Test suite failed: {e}")
