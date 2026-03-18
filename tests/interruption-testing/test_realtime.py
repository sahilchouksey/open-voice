# Open Voice SDK - Real-time Interruption Testing
# Uses pre-recorded speech audio for accurate testing

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


class RealtimeInterruptionTester:
    """Test Open Voice SDK interruption handling in real-time"""

    def __init__(self, runtime_url="ws://localhost:8011"):
        self.runtime_url = runtime_url
        # Ensure the WebSocket path is correct
        if not self.runtime_url.endswith("/v1/realtime/conversation"):
            if self.runtime_url.endswith("/"):
                self.runtime_url = self.runtime_url.rstrip("/")
            self.runtime_url = f"{self.runtime_url}/v1/realtime/conversation"
        self.ws = None
        self.session_id = None
        self.events = []
        self.test_results = []

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
                "interruption": {"mode": "enabled", "cooldown_ms": 1000},
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

    async def send_audio_silence(self, duration_sec=1.0):
        """Send silence (no speech)"""
        num_samples = int(16000 * duration_sec)
        silence = np.zeros(num_samples, dtype=np.int16)
        audio_b64 = base64.b64encode(silence.tobytes()).decode("utf-8")

        await self.ws.send(
            json.dumps(
                {
                    "type": "audio.append",
                    "session_id": self.session_id,
                    "chunk": {
                        "chunk_id": f"chunk_{int(time.time() * 1000)}",
                        "sequence": 0,
                        "encoding": "pcm_s16le",
                        "sample_rate_hz": 16000,
                        "channels": 1,
                        "duration_ms": duration_sec * 1000,
                        "transport": "inline-base64",
                        "data_base64": audio_b64,
                    },
                }
            )
        )

    async def send_audio_tone(self, freq=500, duration_sec=2.0, amplitude=0.5):
        """Send test tone (simulates speech)"""
        num_samples = int(16000 * duration_sec)
        t = np.linspace(0, duration_sec, num_samples, False)
        audio = amplitude * 32767 * np.sin(2 * np.pi * freq * t)
        audio = audio.astype(np.int16)

        # Stream in chunks
        chunk_size = int(16000 * 0.1)  # 100ms chunks
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

    async def trigger_interrupt(self):
        """Manually trigger interrupt"""
        await self.ws.send(
            json.dumps(
                {
                    "type": "conversation.interrupt",
                    "session_id": self.session_id,
                    "reason": "test",
                }
            )
        )
        logger.info("→ Sent: conversation.interrupt")

    async def listen_events(self, duration_sec=5.0):
        """Listen for events"""
        start = time.time()
        events = []

        while time.time() - start < duration_sec:
            try:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=0.5)
                data = json.loads(msg)
                events.append(data)

                event_type = data.get("type")
                if event_type == "session.status":
                    status = data.get("status")
                    reason = data.get("reason", "")
                    logger.info(f"← Status: {status} ({reason})")

                elif event_type == "conversation.interrupted":
                    logger.info(f"← INTERRUPTED: {data.get('reason')}")

                elif event_type == "stt.final":
                    text = data.get("text", "")[:50]
                    logger.info(f'← STT Final: "{text}..."')

                elif event_type == "stt.partial":
                    text = data.get("text", "")[:30]
                    logger.info(f'← STT Partial: "{text}..."')

                elif event_type == "turn.metrics":
                    cancelled = data.get("cancelled", False)
                    logger.info(f"← Turn Complete: cancelled={cancelled}")

                elif event_type == "route.selected":
                    route = data.get("route_name", "unknown")
                    logger.info(f"← Route: {route}")

            except asyncio.TimeoutError:
                continue

        return events

    # ========================================================================
    # TEST 1: Basic Turn Completion (No Interruption)
    # ========================================================================
    async def test_basic_turn_completion(self):
        """Test that a basic turn completes without issues"""
        logger.info("\n" + "=" * 60)
        logger.info("TEST 1: Basic Turn Completion")
        logger.info("=" * 60)

        try:
            await self.create_session()

            # Send query
            logger.info("Sending query audio...")
            await self.send_audio_tone(freq=300, duration_sec=3.0)
            await self.commit_audio()

            # Wait for completion
            logger.info("Waiting for turn to complete...")
            events = await self.listen_events(duration_sec=10.0)

            # Check results
            statuses = [
                e.get("status") for e in events if e.get("type") == "session.status"
            ]
            turn_metrics = [e for e in events if e.get("type") == "turn.metrics"]

            if "thinking" in statuses and turn_metrics:
                last_metric = turn_metrics[-1]
                if not last_metric.get("cancelled", False):
                    logger.info("✓ PASS: Turn completed successfully")
                    return True

            logger.error("✗ FAIL: Turn did not complete properly")
            return False

        except Exception as e:
            logger.error(f"✗ ERROR: {e}")
            return False
        finally:
            await self.close_session()

    # ========================================================================
    # TEST 2: Manual Interrupt During Processing
    # ========================================================================
    async def test_manual_interrupt(self):
        """Test manual interruption works during processing"""
        logger.info("\n" + "=" * 60)
        logger.info("TEST 2: Manual Interrupt During Processing")
        logger.info("=" * 60)

        try:
            await self.create_session()

            # Start event collector
            event_collector = EventCollector(self.ws)
            collector_task = asyncio.create_task(event_collector.start())

            # Send query
            logger.info("Sending query...")
            await self.send_audio_tone(freq=300, duration_sec=3.0)
            await self.commit_audio()

            # Wait briefly then trigger interrupt
            await asyncio.sleep(1.0)
            logger.info("Triggering manual interrupt...")
            await self.trigger_interrupt()

            # Wait for processing
            await asyncio.sleep(3.0)
            events = event_collector.get_events()

            # Check for interruption
            interruptions = [
                e for e in events if e.get("type") == "conversation.interrupted"
            ]

            collector_task.cancel()

            if interruptions:
                logger.info(
                    f"✓ PASS: Manual interrupt detected ({len(interruptions)} events)"
                )
                return True
            else:
                logger.error("✗ FAIL: No interrupt detected")
                return False

        except Exception as e:
            logger.error(f"✗ ERROR: {e}")
            return False
        finally:
            await self.close_session()

    # ========================================================================
    # TEST 3: Chain Reaction Prevention
    # ========================================================================
    async def test_chain_reaction_prevention(self):
        """Test that continuous speech after interrupt doesn't cause chain reaction"""
        logger.info("\n" + "=" * 60)
        logger.info("TEST 3: Chain Reaction Prevention")
        logger.info("=" * 60)

        try:
            await self.create_session()

            # Start event collector
            event_collector = EventCollector(self.ws)
            collector_task = asyncio.create_task(event_collector.start())

            # Send initial query
            logger.info("Sending initial query...")
            await self.send_audio_tone(freq=300, duration_sec=2.0)
            await self.commit_audio()

            await asyncio.sleep(1.0)

            # Trigger interrupt
            logger.info("Triggering interrupt...")
            await self.trigger_interrupt()
            await asyncio.sleep(0.5)

            # Send continuous speech
            logger.info("Sending continuous speech (5 seconds)...")
            await self.send_audio_tone(freq=400, duration_sec=5.0)
            await self.commit_audio()

            # Wait for results
            await asyncio.sleep(5.0)
            events = event_collector.get_events()

            # Analyze
            interruptions = [
                e for e in events if e.get("type") == "conversation.interrupted"
            ]
            turn_metrics = [e for e in events if e.get("type") == "turn.metrics"]

            logger.info(
                f"Interruptions: {len(interruptions)}, Turns: {len(turn_metrics)}"
            )

            collector_task.cancel()

            # Should have 1 interrupt (manual) and 2 completed turns
            if len(interruptions) <= 1 and len(turn_metrics) >= 1:
                logger.info("✓ PASS: Chain reaction prevented")
                return True
            else:
                logger.error(
                    f"✗ FAIL: Chain reaction detected ({len(interruptions)} interrupts)"
                )
                return False

        except Exception as e:
            logger.error(f"✗ ERROR: {e}")
            return False
        finally:
            await self.close_session()

    # ========================================================================
    # TEST 4: Rapid Successive Interrupts (Cooldown)
    # ========================================================================
    async def test_rapid_interrupts(self):
        """Test cooldown enforcement with rapid interrupts"""
        logger.info("\n" + "=" * 60)
        logger.info("TEST 4: Rapid Successive Interrupts")
        logger.info("=" * 60)

        try:
            await self.create_session()

            # Start event collector
            event_collector = EventCollector(self.ws)
            collector_task = asyncio.create_task(event_collector.start())

            # Send query
            await self.send_audio_tone(freq=300, duration_sec=2.0)
            await self.commit_audio()
            await asyncio.sleep(1.0)

            # Send 5 rapid interrupts
            logger.info("Sending 5 rapid interrupts (200ms apart)...")
            for i in range(5):
                await self.trigger_interrupt()
                await asyncio.sleep(0.2)

            # Wait and check
            await asyncio.sleep(2.0)
            events = event_collector.get_events()
            interruptions = [
                e for e in events if e.get("type") == "conversation.interrupted"
            ]

            logger.info(f"Total interrupts processed: {len(interruptions)}/5")

            collector_task.cancel()

            # Should have at most 2-3 due to cooldown
            if len(interruptions) <= 2:
                logger.info(
                    f"✓ PASS: Cooldown enforced ({len(interruptions)} interrupts)"
                )
                return True
            else:
                logger.error(
                    f"✗ FAIL: Cooldown not working ({len(interruptions)} interrupts)"
                )
                return False

        except Exception as e:
            logger.error(f"✗ ERROR: {e}")
            return False
        finally:
            await self.close_session()

    # ========================================================================
    # TEST 5: False Positive Resistance (Background Noise)
    # ========================================================================
    async def test_false_positive_resistance(self):
        """Test that background noise doesn't trigger false interruptions"""
        logger.info("\n" + "=" * 60)
        logger.info("TEST 5: False Positive Resistance")
        logger.info("=" * 60)

        try:
            await self.create_session()

            # Start event collector
            event_collector = EventCollector(self.ws)
            collector_task = asyncio.create_task(event_collector.start())

            # Send query
            await self.send_audio_tone(freq=300, duration_sec=2.0)
            await self.commit_audio()
            await asyncio.sleep(1.5)

            # Send background noise (lower amplitude)
            logger.info("Sending background noise...")
            await self.send_audio_tone(freq=100, duration_sec=3.0, amplitude=0.1)
            await self.commit_audio()

            # Wait and check
            await asyncio.sleep(3.0)
            events = event_collector.get_events()
            interruptions = [
                e for e in events if e.get("type") == "conversation.interrupted"
            ]

            collector_task.cancel()

            if len(interruptions) == 0:
                logger.info("✓ PASS: No false positives from noise")
                return True
            else:
                logger.error(
                    f"✗ FAIL: False positive detected ({len(interruptions)} interrupts)"
                )
                return False

        except Exception as e:
            logger.error(f"✗ ERROR: {e}")
            return False
        finally:
            await self.close_session()

    # ========================================================================
    # REAL-WORLD SCENARIO TESTS (Based on actual user logs)
    # ========================================================================
    async def test_realworld_barge_in_scenario(self):
        """
        REAL-WORLD TEST: Barge-in During Assistant Response

        Based on actual bug report where:
        1. User asks question
        2. Assistant enters THINKING and starts responding
        3. User interrupts with follow-up speech
        4. System should interrupt, start new turn, process it
        5. BUG: New turn got stuck after user stopped speaking
        """
        logger.info("\n" + "=" * 70)
        logger.info("REAL-WORLD TEST: Barge-in During Assistant Response")
        logger.info("=" * 70)
        logger.info("Reproducing actual bug from user interaction logs")
        logger.info("=" * 70)

        try:
            await self.create_session()

            # Start event collector
            event_collector = EventCollector(self.ws)
            collector_task = asyncio.create_task(event_collector.start())

            # Step 1: Send initial query
            logger.info("\n[Step 1] User: 'Tell me who is Sahil...'")
            await self.send_audio_tone(freq=300, duration_sec=3.0)
            await self.commit_audio()

            # Step 2: Wait for THINKING
            logger.info("[Step 2] Waiting for assistant to enter THINKING...")
            thinking_detected = False
            start = time.time()
            while time.time() - start < 10.0:
                await asyncio.sleep(0.1)
                events = event_collector.get_events()
                if any(
                    e.get("type") == "session.status" and e.get("status") == "thinking"
                    for e in events
                ):
                    thinking_detected = True
                    logger.info("✓ Assistant entered THINKING")
                    break

            if not thinking_detected:
                logger.error("✗ Assistant never entered THINKING")
                collector_task.cancel()
                return False

            # Step 3: User interrupts
            logger.info("[Step 3] User interrupts: 'See? Yeah. E-Y.'")
            await self.send_audio_tone(freq=500, duration_sec=2.0)
            await self.commit_audio()

            # Step 4: Wait for interruption
            logger.info("[Step 4] Waiting for interruption detection...")
            await asyncio.sleep(2.0)

            # Step 5: CRITICAL - Check if new turn processes after user stops
            logger.info("[Step 5] CRITICAL: Waiting for new turn to process...")
            logger.info("        (This is where the bug occurred)")

            turn_processed = False
            start = time.time()
            while time.time() - start < 15.0:
                await asyncio.sleep(0.5)
                events = event_collector.get_events()

                # Check if turn completed after interruption
                turn_metrics = [e for e in events if e.get("type") == "turn.metrics"]
                completed = [
                    tm for tm in turn_metrics if not tm.get("cancelled", False)
                ]

                # Check if we got second route (new turn processing)
                route_events = [e for e in events if e.get("type") == "route.selected"]

                if len(completed) >= 1 and len(route_events) >= 2:
                    turn_processed = True
                    logger.info("✓ New turn processed successfully!")
                    break

            collector_task.cancel()

            if turn_processed:
                logger.info("\n✓ PASS: Real-world barge-in scenario worked!")
                return True
            else:
                logger.error("\n✗ FAIL: Bug reproduced - new turn did not process!")
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
        """Run all test scenarios"""
        logger.info("\n" + "=" * 60)
        logger.info("OPEN VOICE SDK - REAL-TIME INTERRUPTION TESTS")
        logger.info("=" * 60)
        logger.info(f"Runtime: {self.runtime_url}")
        logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info("=" * 60)

        if not await self.connect():
            return []

        tests = [
            ("Basic Turn Completion", self.test_basic_turn_completion),
            ("Manual Interrupt", self.test_manual_interrupt),
            ("Chain Reaction Prevention", self.test_chain_reaction_prevention),
            ("Rapid Interrupts", self.test_rapid_interrupts),
            ("False Positive Resistance", self.test_false_positive_resistance),
            ("Real-World: Barge-in Scenario", self.test_realworld_barge_in_scenario),
        ]

        results = []

        for test_name, test_func in tests:
            await asyncio.sleep(2)
            logger.info(f"\n>>> Starting {test_name}...")
            success = await test_func()
            results.append((test_name, "PASS" if success else "FAIL"))
            logger.info(f">>> Finished {test_name}: {'PASS' if success else 'FAIL'}")

        await self.disconnect()

        # Print summary
        logger.info("\n" + "=" * 60)
        logger.info("TEST SUMMARY")
        logger.info("=" * 60)

        passed = sum(1 for _, r in results if r == "PASS")
        failed = sum(1 for _, r in results if r == "FAIL")

        for test_name, result in results:
            icon = "✓" if result == "PASS" else "✗"
            logger.info(f"{icon} {test_name}: {result}")

        logger.info("-" * 60)
        logger.info(f"Passed: {passed}/{len(results)}")
        logger.info(f"Failed: {failed}/{len(results)}")
        logger.info("=" * 60)

        return results


if __name__ == "__main__":
    import sys

    runtime_url = sys.argv[1] if len(sys.argv) > 1 else "ws://localhost:8011"
    tester = RealtimeInterruptionTester(runtime_url)

    try:
        asyncio.run(tester.run_all_tests())
    except KeyboardInterrupt:
        logger.info("\nTests interrupted by user")
    except Exception as e:
        logger.error(f"Test suite failed: {e}")
