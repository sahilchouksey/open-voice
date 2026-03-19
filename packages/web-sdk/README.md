# Open Voice Web SDK

This package is the primary client-side SDK surface for `open-voice`.

It is distinct from:

- `packages/runtime/`, which owns server-side voice session orchestration
- the future web demo app, which will consume this SDK

The web SDK owns:

- runtime HTTP and WebSocket communication
- browser-oriented audio input adaptation
- session control helpers for web apps
- typed protocol helpers for client-to-runtime messages

The intended integration shape is:

`web app -> @open-voice/web-sdk -> open-voice runtime`

For local smoke testing during development, run the runtime server and then:

`bun run smoke`

Before realtime demo testing, verify runtime engines:

- `GET /v1/engines` should report a default `stt` engine with `available: true`
- if `vad` exists, default `vad` should also be `available: true`

## React Demo (SDK Integration)

The demo frontend is now a Vite + React app under:

`demos/frontend/`

Run it with:

`cd /home/xix3r/Documents/fun/open-voice/demos/frontend`

`bun install`

`bun run dev`

This demo is intended as a full integration reference for `@open-voice/web-sdk`:

- detailed tab: explicit controls and full event tracing
- minimal tab: auto-connect + auto-listen with `send_now` queue policy and radial visualizer
- both tabs consume the SDK client/session APIs directly

## Interruption + speaking controls in SDK

The SDK now supports first-class speaking and interruption helpers:

- `session.say(text, { interruptCurrent?: boolean, reason?: string })`
- `session.generateReply({ userText, ..., interruptCurrent?: boolean, reason?: string })`
- `session.interrupt(reason?)`

For output playback, pass an audio adapter in `connectSession`:

- `audioOutput` option accepts an `AudioOutputAdapter`
- built-in `StreamingPcmPlayer` is provided
- `SessionAudioController` handles generation-scoped filtering and stale chunk drop after interrupt

## Demo SFX Override

The demo page loops a subtle thinking cue from:

`packages/web-sdk/examples/assets/sfx/achievement-fx.wav`

To swap it, replace that file and keep the same name, or update `ThinkingAudioPlayer` in `packages/web-sdk/examples/demo.js`.

## Session State and Queue Signals

- Runtime events include optional `generation_id` for per-generation stream correlation.
- Queue and latency visibility are exposed through:
  - `turn.queued`
  - `turn.metrics`
- Session config supports `turnQueue.policy` (`enqueue`, `send_now`, `inject_next_loop`) and is forwarded as `runtime_config.turn_queue.policy`.
