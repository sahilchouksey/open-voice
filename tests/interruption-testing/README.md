# Open Voice SDK - Interruption Testing Harness

Comprehensive testing framework for validating interruption handling in the Open Voice SDK.

## Overview

This test harness provides automated testing for interruption scenarios including:
- Basic barge-in detection
- Chain reaction prevention
- Rapid successive interrupt handling
- False positive resistance (background noise)
- Late audio chunk protection

## Prerequisites

- Python 3.8+
- Open Voice SDK runtime running locally (default: ws://localhost:8011)
- WebSocket support

## Installation

```bash
# Navigate to test directory
cd tests/interruption-testing

# Install dependencies
pip install -r requirements.txt

# Generate test audio files
python audio_generators.py
```

## Quick Start

### 1. Start the Open Voice Runtime

Make sure your Open Voice SDK runtime is running:
```bash
# In your Open Voice SDK directory
python -m open_voice_runtime.server
```

### 2. Run All Tests

```bash
python test_scenarios.py
```

This will:
- Connect to the runtime
- Run all test scenarios
- Display results
- Save detailed results to `test_results.json`

### 3. Run Individual Tests

```python
import asyncio
from test_scenarios import InterruptionTestScenarios

async def run_specific_test():
    scenarios = InterruptionTestScenarios("ws://localhost:8011")
    
    await scenarios.setup()
    result = await scenarios.test_chain_reaction_prevention()
    await scenarios.teardown()
    
    print(f"Test: {result.test_name}")
    print(f"Status: {result.status.value}")
    print(f"Duration: {result.duration_ms:.0f}ms")
    print(f"Metrics: {result.metrics}")
    if result.logs:
        print("Logs:")
        for log in result.logs:
            print(f"  {log}")

asyncio.run(run_specific_test())
```

## Test Scenarios

### 1. Basic Barge-In (`test_basic_barge_in`)

**Purpose**: Verify user can interrupt assistant during response

**Scenario**:
1. User sends query
2. Assistant starts responding (THINKING/SPEAKING)
3. User sends barge-in audio
4. Verify interruption is detected

**Success Criteria**:
- Interruption event received
- Session transitions to INTERRUPTED then LISTENING
- New turn created for barge-in

### 2. Chain Reaction Prevention (`test_chain_reaction_prevention`)

**Purpose**: Ensure continuous user speech doesn't cause infinite interrupts

**Scenario**:
1. User sends query
2. Assistant starts responding
3. User interrupts
4. User continues speaking (simulating natural continuous speech)
5. Verify turn completes without being interrupted

**Success Criteria**:
- No chain reaction (no multiple interrupts)
- Turn completes successfully
- Turn metrics show `cancelled: false`

### 3. Rapid Successive Interrupts (`test_rapid_successive_interrupts`)

**Purpose**: Verify cooldown prevents excessive interrupts

**Scenario**:
1. Send 5 rapid manual interrupts (200ms apart)
2. Verify cooldown enforcement

**Success Criteria**:
- Maximum 2-3 interrupts processed (due to 1s cooldown)
- Most interrupt attempts blocked by cooldown

### 4. False Positive Resistance (`test_false_positive_resistance`)

**Purpose**: Ensure background noise doesn't trigger false interruptions

**Scenario**:
1. Send user query
2. Wait for assistant response
3. Send white noise (background noise simulation)
4. Verify no interrupt triggered

**Success Criteria**:
- No interruption events from noise
- VAD properly filters non-speech audio

### 5. Late Audio Chunk Protection (`test_late_chunk_protection`)

**Purpose**: Verify late audio chunks don't trigger false interrupt

**Scenario**:
1. Send user query
2. Wait for THINKING status
3. Send additional audio chunks (simulating mic buffer delay)
4. Verify no interrupt triggered by late chunks

**Success Criteria**:
- No interruptions from late chunks
- Post-STT-final protection working

## Test Audio Patterns

The test harness generates various audio patterns:

| Pattern | Duration | Description |
|---------|----------|-------------|
| `silence_1s` | 1s | Complete silence |
| `tone_1khz` | 2s | Pure 1000Hz tone |
| `chirp` | 3s | Frequency sweep (200-800Hz) |
| `white_noise` | 5s | Background noise |
| `speech_natural` | 5s | Natural speech pattern with pauses |
| `speech_continuous` | 10s | Continuous speech (no pauses) |
| `speech_staccato` | 5s | Short, sharp bursts |
| `speech_noisy` | 5s | Speech with heavy noise |

Generate all test audio:
```bash
python audio_generators.py
```

This creates WAV files in `test_audio/` directory.

## Configuration

### Runtime URL

Change the runtime URL when initializing:

```python
scenarios = InterruptionTestScenarios("ws://your-runtime:8011")
```

### Test Parameters

Modify test parameters in `test_scenarios.py`:

```python
# In test_basic_barge_in()
await asyncio.sleep(2.0)  # Wait time before barge-in

# In test_chain_reaction_prevention()
continuous_audio = get_test_audio("speech_continuous")  # 10s continuous

# In test_rapid_successive_interrupts()
for i in range(5):  # Number of rapid interrupts
    await asyncio.sleep(0.2)  # Delay between interrupts
```

## Results Format

Test results are saved to `test_results.json`:

```json
[
  {
    "test_name": "basic_barge_in",
    "status": "passed",
    "duration_ms": 5234,
    "metrics": {
      "interruption_count": 1,
      "final_status": "listening"
    },
    "error": null,
    "logs": [
      "Session created: sess_abc123",
      "Sent initial user query",
      "✓ Interruption detected: 1 events"
    ]
  }
]
```

## Troubleshooting

### Connection Refused

**Problem**: `Failed to connect to Open Voice runtime`

**Solution**: 
- Ensure Open Voice SDK runtime is running
- Check runtime URL (default: `ws://localhost:8011`)
- Verify no firewall blocking WebSocket port

### No Interruption Detected

**Problem**: Barge-in test fails with "No interruption event received"

**Solution**:
- Check if interruption is enabled in runtime config
- Verify `turn_queue_policy` is set to `send_now` or `barge_in`
- Increase wait time before barge-in audio

### Chain Reaction Still Occurring

**Problem**: Continuous speech triggers multiple interrupts

**Solution**:
- Verify all 9 protection layers are implemented
- Check logs for which protection should be blocking
- Review `_should_handle_interruption()` in session.py

### False Positives

**Problem**: Background noise triggers interruptions

**Solution**:
- Adjust VAD threshold in session.py (default: 0.85 for THINKING, 0.5 otherwise)
- Use higher quality test audio
- Check VAD implementation

## Extending the Test Harness

### Add New Test Scenario

```python
async def test_my_scenario(self) -> TestResult:
    test_name = "my_scenario"
    start_time = time.time()
    logs = []
    
    try:
        session_id = await self.tester.create_session()
        # ... your test logic ...
        
        return TestResult(test_name, TestStatus.PASSED, 
                         (time.time() - start_time) * 1000,
                         {"metric": value}, logs=logs)
    except Exception as e:
        return TestResult(test_name, TestStatus.ERROR, 
                         (time.time() - start_time) * 1000,
                         {}, str(e), logs=logs)
```

Then add to `run_all_tests()`:
```python
tests = [
    # ... existing tests ...
    self.test_my_scenario,
]
```

### Custom Audio Patterns

Add to `audio_generators.py`:

```python
TEST_PATTERNS["my_pattern"] = lambda: AudioGenerator.generate_tone(440, 1.0)
```

Use in tests:
```python
audio = get_test_audio("my_pattern")
```

## Performance Benchmarks

Expected performance targets:

| Metric | Target | Notes |
|--------|--------|-------|
| Interruption Detection | <100ms | From speech start to interrupt event |
| Cooldown Enforcement | 1000ms | Configurable in interruption_config.py |
| Chain Reaction Prevention | 0 | Should be completely prevented |
| False Positive Rate | <2% | Background noise should not trigger |
| Late Chunk Protection | <3s | Block interrupts for 3s after STT final |

## Integration with CI/CD

Example GitHub Actions workflow:

```yaml
name: Interruption Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      
      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: 3.8
      
      - name: Install dependencies
        run: |
          pip install -r tests/interruption-testing/requirements.txt
      
      - name: Start Open Voice Runtime
        run: |
          python -m open_voice_runtime.server &
          sleep 5
      
      - name: Run interruption tests
        run: |
          cd tests/interruption-testing
          python test_scenarios.py
      
      - name: Upload results
        uses: actions/upload-artifact@v2
        with:
          name: test-results
          path: tests/interruption-testing/test_results.json
```

## References

- Open Voice SDK Documentation
- WebRTC VAD Documentation
- Test audio patterns based on LibriSpeech and Common Voice datasets
