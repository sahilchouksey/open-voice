# Event Trace Testing

This directory contains comprehensive tests based on real event traces from Open Voice SDK sessions.

## Test Files

### 1. `test_scenarios.py` (Enhanced)
Now includes two new test scenarios based on the live event trace:

- **test_event_trace_validation**: Validates complete event sequences, generation IDs, and status transitions
- **test_generation_id_race_condition**: Tests that generation IDs are properly tracked across interrupt scenarios

### 2. `test_event_trace_regression.py` (New)
Standalone regression test that can validate events against expected patterns from live traces.

### 3. `test_event_trace_regression.py` Features

- **Event Structure Validation**: Checks that events have required fields
- **Sequence Validation**: Validates event ordering matches expected patterns
- **Generation ID Consistency**: Ensures generation IDs are properly tracked
- **Turn Lifecycle Validation**: Validates complete turn processing
- **Status Transition Validation**: Checks proper status changes (listening → thinking → speaking)

## Running Tests

### Run all interruption tests:
```bash
cd tests/interruption-testing
python test_scenarios.py
```

### Run specific test:
```bash
cd tests/interruption-testing
python -c "
import asyncio
from test_scenarios import InterruptionTestScenarios

async def main():
    scenarios = InterruptionTestScenarios()
    await scenarios.setup()
    result = await scenarios.test_event_trace_validation()
    print(f'Result: {result.status.value}')
    print(f'Logs: {result.logs}')
    await scenarios.teardown()

asyncio.run(main())
"
```

### Run regression test with pytest:
```bash
pytest tests/interruption-testing/test_event_trace_regression.py -v
```

## Event Trace Analysis

Based on live trace from session: `sess_d4dc7b4bf682443ebb6a9843e7ed803c`

### Key Observations

1. **Status Transitions**:
   - `listening` → `thinking` → `speaking` → `listening`
   - Proper lifecycle management

2. **Generation ID Pattern**:
   - First turn: `gen_5493ef04737c4a63ba46216a13783dbd`
   - Second turn: `gen_61ae75b2bb654e579e3c4fba20ce5a55`
   - Clear separation between turns

3. **Event Sequence**:
   ```
   session.status (listening)
   turn.metrics
   stt.final
   vad.state (end_of_speech)
   route.selected
   session.status (thinking)
   llm.phase (thinking)
   llm.phase (generating)
   llm.response.delta
   session.status (speaking)
   llm.completed
   tts.completed
   ```

4. **Turn Metrics**:
   - `turn_to_complete_ms`: 7976.51ms (first turn)
   - `llm_first_delta_to_tts_first_chunk_ms`: 2227.76ms
   - Proper timing tracking

## Test Coverage

The tests validate:

✅ Event type completeness  
✅ Required field presence  
✅ Generation ID consistency  
✅ Status transitions  
✅ Turn lifecycle  
✅ Timing metrics  
✅ Interrupt handling  
✅ Race condition prevention  

## Adding New Test Cases

To add a test based on a new event trace:

1. Save the trace to `tests/fixtures/<name>_trace.json`
2. Add test method to `InterruptionTestScenarios`
3. Update `run_all_tests()` to include new test
4. Run tests and verify output

## Fixtures

### `tests/fixtures/live_event_trace.json`
Sample event trace from a live session showing complete turn lifecycle.

## Validation Rules

### Required Event Types
- `session.status` - Session state changes
- `turn.metrics` - Turn performance metrics
- `stt.final` - Speech-to-text results
- `vad.state` - Voice activity detection
- `route.selected` - Router selection
- `llm.response.delta` - LLM response chunks
- `tts.chunk` / `tts.completed` - Text-to-speech events

### Required Fields by Event Type
See `EventTraceRegressionTest.REQUIRED_FIELDS` for complete list.

### Status Sequence
Expected: `listening` → `thinking` → `speaking` → `listening`

## Troubleshooting

If tests fail:

1. Check runtime is running: `ws://localhost:8011`
2. Verify session can be created
3. Check event logs for missing fields
4. Review generation ID consistency
5. Validate status transitions

## Continuous Integration

These tests should be run:
- On every commit affecting interruption handling
- Before releases
- When updating dependencies
- After infrastructure changes
