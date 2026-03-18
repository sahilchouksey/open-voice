# Open Voice SDK - Interruption Testing Suite

Complete real-time testing framework for Open Voice SDK interruption handling.

## 🚀 Quick Start (30 seconds)

```bash
cd tests/interruption-testing
./run_tests.sh
```

That's it! This will:
1. Check dependencies
2. Connect to your Open Voice runtime (default: localhost:8011)
3. Run all 5 interruption tests
4. Show results

## 📁 Files Overview

| File | Purpose | Use When |
|------|---------|----------|
| `test_realtime.py` | **Main test script** | Run this to test your SDK |
| `run_tests.sh` | Bash wrapper | Easier execution |
| `QUICKSTART.md` | Quick guide | Getting started |
| `test_scenarios.py` | Extended tests | More detailed testing |
| `test_harness.py` | Test framework | Building custom tests |
| `audio_generators.py` | Audio synthesis | Custom audio patterns |
| `hls_stream_tester.py` | Live stream testing | Integration testing |
| `README.md` | Full documentation | Complete reference |

## 🎯 What Gets Tested

### 5 Core Tests:

1. **Basic Turn Completion**
   - Simple query → complete turn
   - Verifies: Normal operation works

2. **Barge-In During THINKING**
   - Query → wait for THINKING → interrupt
   - Verifies: send_now interruption works

3. **Chain Reaction Prevention** ⭐
   - Query → interrupt → continuous speech
   - Verifies: No infinite interrupt loop

4. **Rapid Successive Interrupts**
   - 5 rapid interrupts
   - Verifies: 1s cooldown enforced

5. **Late Audio Chunk Protection**
   - Query → reach THINKING → late chunks
   - Verifies: Post-STT-final protection works

## 📊 Expected Results

All tests should **PASS** with output like:

```
✓ Basic Turn Completion: PASS
✓ Barge-In During THINKING: PASS
✓ Chain Reaction Prevention: PASS
✓ Rapid Interrupts: PASS
✓ Late Chunk Protection: PASS

Passed: 5/5
```

## 🔧 Prerequisites

1. **Open Voice SDK running**
   ```bash
   python -m open_voice_runtime.server
   ```

2. **Python 3.8+ with dependencies:**
   ```bash
   pip install websockets numpy
   ```

## 🧪 Running Tests

### Option 1: Bash Script (Recommended)
```bash
./run_tests.sh                    # Default: ws://localhost:8011
./run_tests.sh ws://remote:8011   # Custom URL
```

### Option 2: Python Directly
```bash
python3 test_realtime.py                    # Default
python3 test_realtime.py ws://remote:8011   # Custom URL
```

### Option 3: Individual Tests
```python
import asyncio
from test_realtime import RealtimeInterruptionTester

tester = RealtimeInterruptionTester()
await tester.connect()
await tester.create_session()
result = await tester.test_chain_reaction_prevention()
print(f"Result: {result}")
```

## 🐛 If Tests Fail

### "Connection refused"
- Make sure Open Voice runtime is running
- Check the WebSocket port (default: 8011)

### "No interruption detected"
- Verify `turn_queue_policy: "send_now"` in your config
- Check interruption mode is not "disabled"

### "Chain reaction occurred"
- Review session.py protection layers
- Check logs for which protection should block

## 📈 Test Methodology

**Real-time WebSocket Communication:**
1. Connects via WebSocket (just like your web UI)
2. Creates session with `send_now` policy
3. Streams synthetic audio in real-time (100ms chunks)
4. Monitors all events from runtime
5. Validates protection layers work

**Synthetic Audio:**
- Sine wave tones (simulates speech)
- 16-bit PCM, 16kHz, mono
- No external audio files needed

## 🎨 Customization

Edit `test_realtime.py`:

```python
# Change tone frequency
await self.send_audio_tone(freq=800, duration_sec=3.0)

# Change wait times
await asyncio.sleep(5.0)  # Longer wait for slow systems

# Add more test cases
async def test_my_scenario(self):
    # Your custom test
    pass
```

## 📚 Documentation

- **QUICKSTART.md** - Get started in 30 seconds
- **README.md** - Complete documentation
- **Code comments** - Inline documentation

## ✅ Verification Checklist

Before saying interruption handling is "flawless":

- [ ] All 5 tests PASS
- [ ] No chain reactions in 10+ consecutive runs
- [ ] Barge-in works within 500ms
- [ ] Cooldown blocks rapid interrupts
- [ ] Late chunks don't false-trigger
- [ ] Logs show protection layers active

## 🔄 Continuous Testing

Run tests repeatedly to ensure stability:

```bash
for i in {1..10}; do
    echo "=== Run $i ==="
    ./run_tests.sh
done
```

## 📞 Support

If tests fail:
1. Check Open Voice runtime is running
2. Check QUICKSTART.md troubleshooting
3. Review test logs for specific failures
4. Verify all 9 protection layers in session.py

## 🎉 Success Criteria

Your interruption handling is working when:
- ✅ 5/5 tests pass consistently
- ✅ No chain reactions ever occur
- ✅ Barge-in responds in <500ms
- ✅ Cooldown prevents spam
- ✅ False positives <2%

---

**Ready?** Run `./run_tests.sh` now!
