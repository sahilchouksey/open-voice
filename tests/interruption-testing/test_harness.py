# Open Voice SDK - Interruption Testing Harness
# Comprehensive testing framework for interruption handling

import asyncio
import time
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable
from enum import Enum
import websockets
import numpy as np
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class TestStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


@dataclass
class TestResult:
    test_name: str
    status: TestStatus
    duration_ms: float
    metrics: Dict = field(default_factory=dict)
    error_message: Optional[str] = None
    logs: List[str] = field(default_factory=list)


@dataclass
class InterruptionEvent:
    timestamp: float
    event_type: str  # 'detected', 'triggered', 'completed'
    session_id: str
    turn_id: Optional[str] = None
    latency_ms: Optional[float] = None


class OpenVoiceTester:
    """Test harness for Open Voice SDK interruption handling"""

    def __init__(self, runtime_url: str = "ws://localhost:8011"):
        self.runtime_url = runtime_url
        self.session_id: Optional[str] = None
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.results: List[TestResult] = []
        self.interruption_events: List[InterruptionEvent] = []
        self.message_log: List[Dict] = []
        self.is_connected = False

    async def connect(self) -> bool:
        """Connect to Open Voice runtime"""
        try:
            self.websocket = await websockets.connect(self.runtime_url)
            self.is_connected = True
            logger.info(f"Connected to Open Voice runtime at {self.runtime_url}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            return False

    async def disconnect(self):
        """Disconnect from runtime"""
        if self.websocket:
            await self.websocket.close()
            self.is_connected = False
            logger.info("Disconnected from Open Voice runtime")

    async def create_session(self, config: Dict = None) -> Optional[str]:
        """Create a new session"""
        default_config = {
            "type": "session.create",
            "config": {"turn_queue_policy": "send_now", "voice": "af_heart"},
        }

        if config:
            default_config.update(config)

        try:
            await self.websocket.send(json.dumps(default_config))
            response = await asyncio.wait_for(self.websocket.recv(), timeout=5.0)
            data = json.loads(response)

            if data.get("type") == "session.created":
                self.session_id = data.get("session_id")
                logger.info(f"Session created: {self.session_id}")
                return self.session_id
            else:
                logger.error(f"Unexpected response: {data}")
                return None

        except Exception as e:
            logger.error(f"Failed to create session: {e}")
            return None

    async def close_session(self):
        """Close the current session"""
        if self.session_id:
            try:
                await self.websocket.send(
                    json.dumps({"type": "session.close", "session_id": self.session_id})
                )
                logger.info(f"Session closed: {self.session_id}")
                self.session_id = None
            except Exception as e:
                logger.error(f"Failed to close session: {e}")

    async def send_audio(self, audio_data: bytes, sequence: int = 0):
        """Send audio data to the runtime"""
        if not self.session_id:
            logger.error("No active session")
            return

        # Convert audio to base64 for WebSocket transmission
        import base64

        audio_b64 = base64.b64encode(audio_data).decode("utf-8")

        message = {
            "type": "audio.append",
            "session_id": self.session_id,
            "data": audio_b64,
            "encoding": "pcm_s16le",
            "sample_rate": 16000,
            "sequence": sequence,
        }

        try:
            await self.websocket.send(json.dumps(message))
        except Exception as e:
            logger.error(f"Failed to send audio: {e}")

    async def commit_turn(self):
        """Commit the current turn"""
        if not self.session_id:
            logger.error("No active session")
            return

        try:
            await self.websocket.send(
                json.dumps({"type": "audio.commit", "session_id": self.session_id})
            )
        except Exception as e:
            logger.error(f"Failed to commit turn: {e}")

    async def trigger_interrupt(self):
        """Manually trigger an interrupt"""
        if not self.session_id:
            logger.error("No active session")
            return

        try:
            await self.websocket.send(
                json.dumps(
                    {
                        "type": "conversation.interrupt",
                        "session_id": self.session_id,
                        "reason": "manual_test",
                    }
                )
            )
            logger.info("Interrupt triggered manually")
        except Exception as e:
            logger.error(f"Failed to trigger interrupt: {e}")

    async def listen_for_events(self, duration: float = 10.0) -> List[Dict]:
        """Listen for events from the runtime"""
        events = []
        start_time = time.time()

        try:
            while time.time() - start_time < duration:
                try:
                    message = await asyncio.wait_for(self.websocket.recv(), timeout=0.5)
                    data = json.loads(message)
                    events.append(data)
                    self.message_log.append(data)

                    # Track interruption events
                    if data.get("type") == "conversation.interrupted":
                        self.interruption_events.append(
                            InterruptionEvent(
                                timestamp=time.time(),
                                event_type="triggered",
                                session_id=data.get("session_id"),
                                turn_id=data.get("turn_id"),
                                latency_ms=None,
                            )
                        )

                except asyncio.TimeoutError:
                    continue

        except Exception as e:
            logger.error(f"Error listening for events: {e}")

        return events

    def get_session_status(self) -> Optional[str]:
        """Get current session status from message log"""
        for msg in reversed(self.message_log):
            if msg.get("type") == "session.status":
                return msg.get("status")
        return None

    def reset_logs(self):
        """Reset message and event logs"""
        self.message_log.clear()
        self.interruption_events.clear()


# Test scenario implementations will be in separate files
if __name__ == "__main__":
    # Quick test
    async def quick_test():
        tester = OpenVoiceTester()
        if await tester.connect():
            session_id = await tester.create_session()
            if session_id:
                logger.info(f"✓ Session created: {session_id}")
                await tester.close_session()
            await tester.disconnect()

    asyncio.run(quick_test())
