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
