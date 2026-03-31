# Open Voice Demo Backend

This backend runs the Open Voice runtime APIs for the root-level demo frontend.

## Quick Start

### From Repo Root

```bash
cd /home/xix3r/Documents/fun/open-voice

# Create virtual environment
python3 -m venv demos/backend/.venv

# Install dependencies
demos/backend/.venv/bin/python -m pip install -r demos/backend/requirements.txt

# Run the backend
demos/backend/.venv/bin/python demos/backend/run.py
```

### Then Run Frontend

```bash
cd /home/xix3r/Documents/fun/open-voice/demos/frontend
bun install
bun run dev
```

Set runtime URL in UI to `http://127.0.0.1:8011`.

## Environment Variables

Create `.env` file in `demos/backend/` or set in your shell:

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `127.0.0.1` | Server host |
| `PORT` | `7860` | Server port |
| `OPENCODE_BASE_URL` | `http://127.0.0.1:4096` | OpenCode server URL |
| `OPEN_VOICE_REDIS_URL` | (none) | Redis URL for session persistence |

Example `.env`:

```bash
HOST=127.0.0.1
PORT=7860
OPENCODE_BASE_URL=http://127.0.0.1:4096
```

## Health Checks

- **Server Health:** `http://127.0.0.1:7860/health`
- **Engine Status:** `http://127.0.0.1:7860/v1/engines`

The engines check should show `stt` and `vad` with `available: true`.

## Troubleshooting

### STT/VAD Unavailable

If engines show unavailable, reinstall runtime extras:

```bash
cd /home/xix3r/Documents/fun/open-voice
demos/backend/.venv/bin/python -m pip install -r demos/backend/requirements.txt
```

### Port Already in Use

```bash
# Find process using port
lsof -i :7860

# Kill it or change PORT in .env
```

### OpenCode Connection Failed

Make sure OpenCode server is running:

```bash
# In another terminal
cd /home/xix3r/Documents/fun/open-voice/.opencode
bun run dev  # or your OpenCode start command
```
