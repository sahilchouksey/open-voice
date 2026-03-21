# Open Voice Demos

Root-level demo apps now live under:

- `demos/frontend` = Vite + React SDK integration demo
- `demos/backend` = Python runtime integration backend for demo

These are intentionally separated from product SDK packages:

- `packages/runtime` = reusable backend runtime
- `packages/web-sdk` = reusable web client SDK

## Run demo

One command (recommended):

```bash
cd /home/xix3r/Documents/fun/open-voice/demos
bun run dev
```

Or run each service separately:

Backend:

```bash
cd /home/xix3r/Documents/fun/open-voice
python3 demos/backend/run.py
```

Frontend:

```bash
cd /home/xix3r/Documents/fun/open-voice/demos/frontend
bun install
bun run dev
```

Runtime URL for frontend: `http://127.0.0.1:8011`

## Voice mode configuration

This repo includes a project-local OpenCode mode for voice behavior:

- `.opencode/modes/voice.md`

The demo frontend uses this mode via `opencode_mode: "voice"` and passes a strict LLM tool list with only `websearch` enabled.

If you run OpenCode in this repo, this local `.opencode` mode is discovered automatically and merged with your global config.

The demo backend also sets `OPEN_VOICE_OPENCODE_DIRECTORY` to the repo root so OpenCode resolves project-local `.opencode` config while running from `demos/backend`.
