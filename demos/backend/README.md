# Open Voice Demo Backend

This backend runs the Open Voice runtime APIs for the root-level demo frontend.

## Start command

Run from repo root:

```bash
cd /home/xix3r/Documents/fun/open-voice
python3 -m venv demos/backend/.venv
demos/backend/.venv/bin/python -m pip install -r demos/backend/requirements.txt
demos/backend/.venv/bin/python demos/backend/run.py
```

Then run frontend:

```bash
cd /home/xix3r/Documents/fun/open-voice/demos/frontend
bun install
bun run dev
```

Set runtime URL in UI to `http://127.0.0.1:8011`.

Health check:

`http://127.0.0.1:8011/health`

Engine health check (must show `stt` and `vad` with `available: true`):

`http://127.0.0.1:8011/v1/engines`

If STT/VAD are unavailable, install runtime extras in your Python env:

```bash
cd /home/xix3r/Documents/fun/open-voice
demos/backend/.venv/bin/python -m pip install -r demos/backend/requirements.txt
```
