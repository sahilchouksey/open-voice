# Open Voice Web SDK

This package is the primary client-side SDK surface for `open-voice`.

It is distinct from:

- `apps/runtime/`, which owns server-side voice session orchestration
- the future web demo app, which will consume this SDK

The web SDK owns:

- runtime HTTP and WebSocket communication
- browser-oriented audio input adaptation
- session control helpers for web apps
- typed protocol helpers for client-to-runtime messages

The intended integration shape is:

`web app -> @open-voice/web-sdk -> open-voice runtime`
