# Open Voice Demos

Root-level demo apps now live under:

- `demos/frontend` = Vite + React SDK integration demo
- `demos/backend` = Python runtime integration backend for demo

These are intentionally separated from product SDK packages:

- `packages/runtime` = reusable backend runtime
- `packages/web-sdk` = reusable web client SDK

## Environment

The demo loads configuration from two files (in order):

- `demos/.env` — shared defaults (committed to repo, no secrets)
- `demos/.env.local` — **your overrides** (gitignored, machine-specific)

Both files are auto-loaded by `demos/dev.sh` and `demos/backend/run.py`.

See [`demos/.env.example`](./.env.example) for a template.

---

### `.env` — Default Configuration

The base `.env` contains shared provider keys and route targets.

| Variable | Description | Example |
|----------|-------------|---------|
| `DO_AI_API_KEY` | DigitalOcean AI inference API key | `sk-do-...` |
| `OPEN_VOICE_ROUTE_TARGETS` | JSON array mapping route tiers → LLM models | See below |

**Route targets** define which model handles each complexity tier:

```json
[
  {"llm_engine_id":"opencode","provider":"doai","model":"openai-gpt-oss-120b","profile_id":"trivial_route"},
  {"llm_engine_id":"opencode","provider":"doai","model":"openai-gpt-oss-120b","profile_id":"simple_route"},
  {"llm_engine_id":"opencode","provider":"doai","model":"openai-gpt-oss-120b","profile_id":"moderate_route"},
  {"llm_engine_id":"opencode","provider":"doai","model":"openai-gpt-oss-120b","profile_id":"complex_route"},
  {"llm_engine_id":"opencode","provider":"doai","model":"openai-gpt-oss-120b","profile_id":"expert_route"}
]
```

Each entry has:
- `llm_engine_id` — engine to use (`"opencode"`)
- `provider` — LLM provider (`"doai"`, `"copilot-proxy"`, etc.)
- `model` — model name (provider-specific)
- `profile_id` — route tier name (used by the router to select a model)

---

### `.env.local` — Your Overrides

Place your machine-specific settings here. This file is **gitignored** and takes precedence over `.env`.

| Variable | Description | Default |
|----------|-------------|---------|
| `OPEN_VOICE_ROUTE_TARGETS` | Override the default route map | (from `.env`) |
| `OPEN_VOICE_OPENCODE_DIRECTORY` | Path to repo root for `.opencode` config | `.` (repo root) |
| `OPEN_VOICE_OPENCODE_ENABLE_EXA` | Enable Exa web search integration | `0` |
| `OPEN_VOICE_KOKORO_ONNX_ASSET_DIR` | Path to Kokoro TTS ONNX model directory | `packages/runtime/.models/kokoro-onnx` |
| `OPEN_VOICE_DEMO_HOST` | Bind address for backend server | `0.0.0.0` |
| `OPEN_VOICE_DEMO_PORT` | Port for backend server | `8011` |
| `PARAKEET_DEVICE` | Device for Parakeet STT engine (`cuda` or `cpu`) | `cuda` |
| `VITE_OPEN_VOICE_FRONTEND_DIAGNOSTICS` | Enable frontend diagnostics tracing | `0` |

**Example `.env.local` (GitHub Copilot Proxy setup):**

```bash
# Use local GitHub Copilot proxy with free models
OPEN_VOICE_ROUTE_TARGETS='[{"llm_engine_id":"opencode","provider":"copilot-proxy","model":"gpt-4.1","profile_id":"trivial_route"},{"llm_engine_id":"opencode","provider":"copilot-proxy","model":"gpt-4o","profile_id":"simple_route"},{"llm_engine_id":"opencode","provider":"copilot-proxy","model":"gpt-4o-mini","profile_id":"moderate_route"},{"llm_engine_id":"opencode","provider":"copilot-proxy","model":"claude-sonnet-4","profile_id":"complex_route"},{"llm_engine_id":"opencode","provider":"copilot-proxy","model":"gpt-5-mini","profile_id":"expert_route"}]'

# Opencode config directory
OPEN_VOICE_OPENCODE_DIRECTORY=/path/to/repo

# Enable web search
OPEN_VOICE_OPENCODE_ENABLE_EXA=1

# TTS model path
OPEN_VOICE_KOKORO_ONNX_ASSET_DIR=/path/to/models/kokoro-onnx

# Server config
OPEN_VOICE_DEMO_HOST=0.0.0.0
OPEN_VOICE_DEMO_PORT=8011
```

---

### Route Tiers

The router selects a model based on query complexity:

| Tier | Profile ID | Use Case |
|------|------------|----------|
| **Trivial** | `trivial_route` | Greetings, confirmations |
| **Simple** | `simple_route` | Basic questions |
| **Moderate** | `moderate_route` | Standard tasks |
| **Complex** | `complex_route` | Multi-step reasoning |
| **Expert** | `expert_route` | Code generation, analysis |

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
