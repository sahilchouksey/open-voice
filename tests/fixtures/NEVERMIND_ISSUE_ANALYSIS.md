# "Never Mind" Issue Analysis

## Problem Statement

User says "never mind" to cancel a request, but the LLM continues processing the old query and responds to it. The system treats "never mind" as a separate turn instead of interrupting the current turn.

## Root Cause (from event trace sess_77cfff4b94844819b169b456243a775e)

### Timeline:
- **16:55:32** - vad.start_of_speech (user starts speaking)
- **16:55:34** - stt.final "okay now search" (while still speaking!)
- **16:55:35** - stt.final "About me" (still speaking)
- **16:55:36** - stt.final with generation_id "okay now search About me" **TURN COMMITTED**
- **16:55:37** - route.selected
- **16:55:38** - llm.phase "thinking" **LLM STARTS PROCESSING**
- **16:55:47** - "never mind" becomes **separate turn**

### Root Cause:
1. **Early commit**: The turn was committed at 16:55:36 BEFORE VAD detected `end_of_speech`
2. **LLM started processing**: At 16:55:38, LLM started processing "okay now search About me"
3. **"Never mind" became new turn**: At 16:55:47, "never mind" became a separate turn (too late to interrupt)

### Why it happened:
The system was in "post-interrupt collecting mode" which waits for VAD end before committing. But this protection only applies AFTER an interrupt, not during normal speech.

The code had logic to defer auto-commit until VAD shows `end_of_speech`, but ONLY during post-interrupt mode:
```python
if in_post_interrupt_collecting and result.should_auto_commit:
    vad_ended = any(e.kind is VadEventKind.END_OF_SPEECH for e in vad_events)
    if not vad_ended:
        # Don't auto-commit yet, wait for VAD end
        result = TurnRecognitionResult(...)
```

## Fix Applied

### session.py (line ~470-495):
Added check for VAD `end_of_speech` before auto-commit for ALL turns, not just post-interrupt turns:

```python
if emit is not None and result.should_auto_commit and can_auto_commit:
    # Check if VAD has signaled end of speech
    vad_ended = any(e.kind is VadEventKind.END_OF_SPEECH for e in vad_events)
    
    if not vad_ended:
        # Don't auto-commit yet - user may still be speaking
        result = TurnRecognitionResult(...)
    else:
        # VAD ended - we can commit
        ...
```

### What this fix does:
1. When `stt.final` arrives but VAD has NOT detected `end_of_speech`, the system will NOT commit the turn
2. The system will wait for VAD `end_of_speech` before committing
3. This ensures the user has finished speaking before the turn is committed
4. "Never mind" will now properly interrupt the current turn instead of becoming a separate turn

## Test Cases Added

1. **test_llm_stuck_thinking_no_interrupt** - Tests LLM stuck for 2+ minutes with no interrupt
2. **test_stt_final_should_wait_for_vad_end** - Tests that turn commit waits for VAD end
3. **test_recently_entered_processing_protection** - Tests 3-second protection blocking legitimate interrupts

## Fixture Files Added

1. `early_commit_event_trace.json` - Shows system committing BEFORE VAD end
2. `llm_continues_after_nevermind.json` - Shows LLM continuing after "never mind"
