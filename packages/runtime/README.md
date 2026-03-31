# Open Voice Runtime

This package contains the SDK-first realtime runtime for `open-voice`.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        OPEN VOICE RUNTIME                                   │
└─────────────────────────────────────────────────────────────────────────────┘

  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────────┐
  │  audio  │───▶│   STT   │───▶│  router │───▶│   LLM   │───▶│    TTS     │
  │  input  │    │  (VAD)  │    │         │    │         │    │  output    │
  └─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────────┘
       │              │              │              │               │
       ▼              ▼              ▼              ▼               ▼
  ┌──────────────────────────────────────────────────────────────────────────┐
  │                         SESSION LAYER                                    │
  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────┐  │
  │  │CREATED  │─▶│LOADING  │─▶│ READY   │─▶│LISTENING│─▶│TRANSCRIBING │  │
  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘  └─────────────┘  │
  │       │            │            │            │              │            │
  │       │            │            │            │              ▼            │
  │       │            │            │            │        ┌──────────┐       │
  │       │            │            │            │        │ THINKING │       │
  │       │            │            │            │        └────┬─────┘       │
  │       │            │            │            │             │             │
  │       │            │            │            │             ▼             │
  │       │            │            │            │        ┌─────────┐        │
  │       ▼            ▼            ▼            ▼        │ SPEAKING│        │
  │  ┌─────────┐  ┌──────────┐  ┌───────────┐  ┌──────────┐  └────┬──────┘  │
  │  │ CLOSED  │  │INTERRUPTED│  │  FAILED   │  │ INTERRUPT│◀──────┘        │
  │  └─────────┘  └──────────┘  └───────────┘  └──────────┘                 │
  └──────────────────────────────────────────────────────────────────────────┘
```

## Module Reference

### 1. Session Module (`session/`)

Manages voice session lifecycle and turn state.

| Class | Purpose |
|-------|---------|
| `SessionState` | Core session data - session_id, status, timestamps, turns |
| `SessionManager` | Abstract base for session CRUD |
| `InMemorySessionManager` | In-memory storage |
| `RedisSessionManager` | Redis-backed distributed storage |
| `TurnRecognition` | Turn detection and audio buffering |
| `UnifiedInterruptionHandler` | Handle barge-in at any pipeline stage |

**State Machine:**
```
CREATED → LOADING → READY → LISTENING → TRANSCRIBING → THINKING → SPEAKING
                                              ↓                      ↓
                                        INTERRUPTED ←──────────────┘
                                              ↓
                                         LISTENING (recover)
```

**Key Methods:**
- `SessionState.create()` - Create new session
- `SessionState.begin_turn()` / `complete_turn()` - Turn management
- `SessionManager.create/get/update/close` - CRUD operations

---

### 2. Audio Module (`audio/`)

Core audio data types and preprocessing.

| Class/Function | Purpose |
|----------------|---------|
| `AudioFormat` | Audio format (sample rate, channels, encoding) |
| `AudioChunk` | Single audio chunk with metadata |
| `audio_chunk_to_mono_floats()` | Convert to mono float samples |
| `audio_bytes_to_mono_floats()` | Stereo-to-mono conversion |

**Audio Pipeline:**
```
┌─────────┐    ┌─────────┐    ┌─────────┐
│  Mic   │───▶│   VAD   │───▶│   STT   │
│ Input  │    │  Detect │    │         │
└─────────┘    └─────────┘    └─────────┘
                                     │
                                     ▼
                              ┌─────────────┐
                              │ AudioChunk  │
                              │   Data      │
                              └─────────────┘
                                     │
                                     ▼
┌─────────┐    ┌─────────┐    ┌─────────┐
│ Speaker │◀───│   TTS   │◀───│   LLM    │
│ Output  │    │  Synth  │    │ Response │
└─────────┘    └─────────┘    └─────────┘
```

---

### 3. STT Module (`stt/`)

Speech-to-Text processing.

| Class | Purpose |
|-------|---------|
| `BaseSttEngine` | Abstract base for STT engines |
| `SttEngineRegistry` | Engine registry |
| `SttService` | High-level STT API |
| `MoonshineSttEngine` | Moonshine implementation |

**STT Pipeline:**
```
Audio Input
    │
    ▼
┌──────────────┐     ┌──────────────────┐
│ SttService   │────▶│ SttEngineRegistry│
└──────┬───────┘     └────────┬─────────┘
       │                      │
       ▼                      ▼
┌──────────────────┐    ┌────────────────┐
│ MoonshineSttStream│    │ BaseSttEngine │
│ - push_audio()   │    │ - create_stream│
│ - flush()        │    │ - transcribe() │
└────────┬─────────┘    └────────────────┘
         │
         ▼
   ┌──────────┐
   │ SttEvent  │  ──▶ PARTIAL / FINAL
   └──────────┘
```

**Key Methods:**
- `create_stream(config)` - Create streaming STT session
- `push_audio(chunk)` - Feed audio to stream
- `events()` - Async iterator for transcription events

---

### 4. Router Module (`router/`)

Routes user queries to appropriate LLM based on complexity.

| Class | Purpose |
|-------|---------|
| `BaseRouterEngine` | Abstract base for routers |
| `ArchRouterEngine` | Arch Router implementation |
| `RouterService` | Main routing API |
| `RouteDecision` | Routing decision output |

**Route Tiers:**
| Route | Latency | Use Case |
|-------|---------|----------|
| `trivial_route` | LOW | Greetings, confirmations |
| `simple_route` | LOW | Basic questions |
| `moderate_route` | MEDIUM | Standard operations |
| `complex_route` | HIGH | Multi-step reasoning |
| `expert_route` | HIGH | Code generation |

**Router Flow:**
```
RouteRequest(user_text)
         │
         ▼
┌─────────────────┐
│ RouterService  │
└────────┬────────┘
         │
         ▼
┌─────────────────────┐
│ ArchRouterEngine    │
│   .route()         │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│ ArchRouterClient    │
│   .classify()      │
└────────┬────────────┘
         │
         ▼
   RouteDecision
   (route_name, provider, model)
```

---

### 5. LLM Module (`llm/`)

LLM integration and prompt management.

| Component | Purpose |
|-----------|---------|
| `BaseLlmEngine` | Abstract LLM interface |
| `OpenCodeLlmEngine` | OpenCode provider integration |
| `PromptManager` | System prompt handling |

---

### 6. TTS Module (`tts/`)

Text-to-Speech synthesis.

| Component | Purpose |
|-----------|---------|
| `BaseTtsEngine` | Abstract TTS interface |
| `KokoroTtsEngine` | Kokoro TTS implementation |
| `TtsChunk` | Audio chunk with metadata |

---

### 7. Transport Module (`transport/`)

WebSocket and HTTP communication.

| Component | Purpose |
|-----------|---------|
| `WebSocketServer` | WS session handling |
| `HttpServer` | REST API endpoints |
| `MessageParser` | Protocol message parsing |

---

### 8. Conversation Module (`conversation/`)

Event orchestration and turn coordination.

| Component | Purpose |
|-----------|---------|
| `EventBus` | Event distribution |
| `TurnCoordinator` | Turn state coordination |
| `PipelineOrchestrator` | End-to-end pipeline control |

---

## Running the Runtime

```bash
# Create virtual environment
python3 -m venv .venv

# Install with all extras
pip install -e ".[all]"

# Run the server
python -m open_voice_runtime.app.asgi
```

Server runs on `http://127.0.0.1:7860`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `127.0.0.1` | Server host |
| `PORT` | `7860` | Server port |
| `OPENCODE_BASE_URL` | `http://127.0.0.1:4096` | OpenCode server URL |
| `OPEN_VOICE_REDIS_URL` | (none) | Redis for session persistence |

## Session Persistence

See [Session Persistence](#session-persistence) section in original README.

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Health check |
| `GET /v1/engines` | List available engines |
| `WS /v1/session` | Realtime voice session |
| `POST /v1/session` | Create HTTP session |
