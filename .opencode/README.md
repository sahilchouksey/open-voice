# Open Voice OpenCode Configuration

This directory contains OpenCode agent configurations for the Open Voice runtime.

## Files

| File | Description |
|------|-------------|
| `opencode.json` | Active configuration (gitignored - contains secrets) |
| `opencode.example.json` | Template with example providers (safe to commit) |
| `voice.md` | Voice agent prompt and rules |
| `build.md` | Build agent prompt and rules |
| `modes/voice.md` | Voice mode permissions |

## Quick Start

### 1. Copy the example file

```bash
cp opencode.example.json opencode.json
```

### 2. Edit opencode.json

Edit `opencode.json` to configure your providers:

```json
{
  "provider": {
    "ollama-local": {
      "options": {
        "baseURL": "http://localhost:11434/v1",
        "apiKey": "ollama"
      }
    }
  }
}
```

## Using Ollama

### Option A: Local Ollama

```bash
# Start Ollama
ollama serve

# Pull a model
ollama pull llama3.2
```

Then configure in `opencode.json`:

```json
{
  "provider": {
    "ollama-local": {
      "options": {
        "baseURL": "http://localhost:11434/v1",
        "apiKey": "ollama"
      },
      "models": {
        "llama3.2": {
          "name": "Llama 3.2",
          "modalities": {
            "input": ["text"],
            "output": ["text"]
          }
        }
      }
    }
  }
}
```

### Option B: Remote Ollama

If Ollama is running on another machine:

```json
{
  "provider": {
    "ollama-remote": {
      "options": {
        "baseURL": "http://192.168.1.100:11434/v1",
        "apiKey": "ollama"
      }
    }
  }
}
```

## Changing the Active Route

To use a different provider/model, edit these files:

### 1. Runtime Router Policy

**File:** `packages/runtime/src/open_voice_runtime/router/policy.py`

Look for `default_route_targets()` function:

```python
def default_route_targets():
    return RouteTarget(
        llm_engine_id="opencode",
        provider="ollama-local",    # ← Change this to your provider ID
        model="llama3.2",           # ← Change this to your model ID
        profile_id="moderate_route",
    )
```

### 2. Or via Environment Variable

Set `OPEN_VOICE_ROUTE_TARGETS` in your environment or `.env`:

```bash
# Format: JSON array of RouteTarget objects
export OPEN_VOICE_ROUTE_TARGETS='[{"llm_engine_id":"opencode","provider":"ollama-local","model":"llama3.2","profile_id":"moderate_route"}]'
```

### 3. Demo Frontend

**File:** `demos/frontend/src/constants/config.ts`

Look for route configuration:

```typescript
export const DEFAULT_ROUTE_PROVIDER = "ollama-local"
export const DEFAULT_ROUTE_MODEL = "llama3.2"
```

### 4. Demo .env

**File:** `demos/.env.example` → copy to `demos/.env`

```bash
# Runtime URL
VITE_RUNTIME_BASE_URL=http://localhost:7860

# OpenCode Base URL (for local OpenCode server)
OPENCODE_BASE_URL=http://127.0.0.1:4096
```

## Available Providers in Example

| Provider ID | Description | Base URL |
|-------------|-------------|----------|
| `ollama-local` | Ollama running on localhost | http://localhost:11434/v1 |
| `ollama-remote` | Ollama on remote machine | http://192.168.1.100:11434/v1 |
| `digitalocean-oss` | DigitalOcean OSS endpoint | https://inference.do-ai.run/v1 |

## Supported Models

Check the `models` section in each provider for available options:

- `llama3.2` - Meta's Llama 3.2
- `qwen2.5` - Alibaba's Qwen 2.5
- `mistral` - Mistral AI
- `openai-gpt-oss-120b` - DigitalOcean GPT OSS

## Environment Variables

The runtime uses these OpenCode-related environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENCODE_BASE_URL` | `http://127.0.0.1:4096` | OpenCode server URL |
| `OPEN_VOICE_OPENCODE_DIRECTORY` | From opencode.json | Working directory |
| `OPEN_VOICE_OPENCODE_WORKSPACE` | None | Workspace name |
| `OPEN_VOICE_OPENCODE_ENABLE_EXA` | `True` | Enable Exa search |

## Security Notes

- ⚠️ `opencode.json` is **gitignored** - never commit API keys
- ⚠️ Use `opencode.example.json` as a template
- API keys in `opencode.json` should be kept secret
