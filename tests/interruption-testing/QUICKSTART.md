# Open Voice SDK - Real-time Interruption Testing

Quick and simple real-time testing for the Open Voice SDK's interruption handling.

## Quick Start

### 1. Start Your Open Voice Runtime

Make sure your Open Voice SDK is running with the demo:

```bash
# In your Open Voice SDK directory
python -m open_voice_runtime.server
# or however you start it
```

Default WebSocket URL: `ws://localhost:8011`

### 2. Run the Tests

```bash
cd tests/interruption-testing
python test_realtime.py
```

Or with custom runtime URL:

```bash
python test_realtime.py ws://your-runtime:8011
```

## What It Tests

The real-time tester connects to your running Open Voice SDK and runs 5 automated tests:

### Test 1: Basic Turn Completion
- Sends a simple query
- Waits for turn to complete
- **Verifies**: Turn completes without interruption

### Test 2: Barge-In During THINKING  
- Sends query, waits for THINKING status
- Sends barge-in audio during THINKING
- **Verifies**: Interruption is detected and handled

### Test 3: Chain Reaction Prevention
- Sends query, triggers interrupt
- Sends continuous speech after interrupt
- **Verifies**: No chain reaction (turn completes successfully)

### Test 4: Rapid Successive Interrupts
- Sends 5 rapid manual interrupts (200ms apart)
- **Verifies**: Cooldown enforcement (max 2-3 interrupts)

### Test 5: Late Audio Chunk Protection
- Sends query, waits for THINKING
- Sends late audio chunks (simulating mic buffer delay)
- **Verifies**: Late chunks don't trigger false interrupt

## Expected Output

```
================================================================================
OPEN VOICE SDK - REAL-TIME INTERRUPTION TESTS
================================================================================
Runtime: ws://localhost:8011
Time: 2026-03-24 11:30:00
================================================================================

============================================================
TEST 1: Basic Turn Completion
============================================================
11:30:00.123 - ✓ Session created: sess_abc123...
11:30:00.234 - Sending query audio...
11:30:03.456 - ← Status: thinking (llm.generating)
11:30:05.678 - ← Turn Complete: cancelled=false
11:30:05.789 - ✓ PASS: Turn completed successfully

============================================================
TEST 2: Barge-In During THINKING
============================================================
...
11:30:10.234 - ← INTERRUPTED: barge_in
11:30:10.345 - ✓ PASS: Barge-in detected

...

================================================================================
TEST SUMMARY
================================================================================
✓ Basic Turn Completion: PASS
✓ Barge-In During THINKING: PASS
✓ Chain Reaction Prevention: PASS
✓ Rapid Interrupts: PASS
✓ Late Chunk Chunk Protection: PASS
------------------------------------------------------------------------
Passed: 5/5
Failed: 0/5
================================================================================
```

## Troubleshooting

### Connection Refused

```
✗ Connection failed: [Errno 111] Connect call failed ('127.0.0.1', 8011)
```

**Fix**: Make sure Open Voice runtime is running on the correct port.

### No Interruption Detected

```
✗ FAIL: No interruption detected
```

**Fix**: Check that:
- `turn_queue_policy` is set to `"send_now"` or `"barge_in"` in your runtime config
- Interruption mode is enabled (not `"disabled"`)
- VAD is working correctly

### Chain Reaction Still Happening

```
✗ FAIL: Chain reaction occurred (X interrupts)
```

**Fix**: Review your session.py to ensure all 9 protection layers are implemented:
1. 2-second continuous speech window
2. 1-second minimum turn duration
3. 1-second cooldown
4. 3-second post-interrupt window
5. 5-second post-interrupt turn tracking
6. 10-second speech flow tracking
7. 3-second post-STT-final protection
8. 3-second recently-entered-processing protection
9. Auto-commit status check

## Test Audio Details

The test generates synthetic audio:
- **Format**: 16-bit PCM, 16kHz, mono
- **Type**: Sine wave tones (simulates speech)
- **Chunks**: 100ms chunks streamed in real-time

This simulates real microphone input without requiring actual audio files.

## Customizing Tests

Edit `test_realtime.py` to customize:

```python
# Change test tone frequency
await self.send_audio_tone(freq=800, duration_sec=3.0)  # Higher pitch

# Change wait times
await asyncio.sleep(3.0)  # Longer wait

# Add more interrupts
for i in range(10):  # 10 instead of 5
    await self.trigger_interrupt()
    await asyncio.sleep(0.1)
```

## Requirements

```bash
pip install websockets numpy
```

## Integration with Your Demo

This test is designed to work with your Open Voice demo:

1. Start your demo with `send_now` turn queue policy
2. Run the test
3. It will connect via WebSocket just like your web UI
4. Sends audio just like the microphone
5. Tests interruption handling in real-time

No external dependencies - tests YOUR SDK directly.
