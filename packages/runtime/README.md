# Open Voice Runtime

This package contains the SDK-first realtime runtime for `open-voice`.

The runtime is organized by product domains rather than concrete vendors:

- `session`
- `audio`
- `stt`
- `router`
- `llm`
- `tts`
- `conversation`
- `transport`

The primary control flow is:

`STT -> Router -> LLM -> TTS`

`open-voice` owns the voice session layer, including audio flow, turn state,
interruption, and orchestration.

This runtime is intended to pair with client SDK surfaces, starting with the
web client SDK in `packages/web-sdk`.

## Realtime Harness Notes

- Response tasks are generation-scoped (`generation_id`) to avoid stale LLM/TTS frames after cancellation.
- New user turns during active thinking/speaking can be queued via runtime config `turn_queue.policy`:
  - `enqueue` (default): keep accepting turns and process in FIFO once current generation completes
  - `send_now`: interrupt current generation immediately and process new turn now
  - `inject_next_loop`: currently normalized to `enqueue`
- Runtime emits `turn.queued` and `turn.metrics` events to support queue visibility and latency instrumentation.

For local adapter testing, the ASGI app factory lives at:

`open_voice_runtime.app.asgi:create_asgi_app`

## Session Persistence

By default, sessions are stored in-memory (`InMemorySessionManager`). For production deployments requiring session persistence across restarts or horizontal scaling, you can use Redis-backed session storage.

### Redis Setup

1. Install the Redis extra:
   ```bash
   pip install "open-voice-runtime[redis]"
   ```

2. Set the environment variable:
   ```bash
   export OPEN_VOICE_REDIS_URL="redis://localhost:6379/0"
   ```

3. Start the runtime - it will automatically use Redis for session storage.

### Redis Benefits

- **Survive restarts**: Sessions and queued turns persist across process restarts
- **Horizontal scaling**: Multiple runtime instances can share session state
- **Better reliability**: Session state is not lost on crashes
- **Debugging**: Full session timeline and queue depth history available

### Configuration

- `OPEN_VOICE_REDIS_URL`: Redis connection URL (e.g., `redis://localhost:6379/0`)
- If not set, falls back to in-memory storage
- Supports Redis clusters and authentication via standard URL format
