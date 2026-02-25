<div align="center">

```
╔═══════════════════════════════════════════════════════════════╗
║                                                               ║
║                    ┌─┐     ┌───┐     ┌─┐                      ║
║                    │ │ ┌─┐ │   │ ┌─┐ │ │                      ║
║                 ───┘ └─┘ └─┘   └─┘ └─┘ └───                   ║
║                       O P E N   V O I C E                     ║
║                                                               ║
╚═══════════════════════════════════════════════════════════════╝
```
<b>Talk to any LLM</b>
</div>

---

## What is Open Voice?

Open Voice is an open-source voice middleware that lets you have natural conversations with any Large Language Model. Instead of typing, you speak. Instead of reading, you listen.

**The core idea is simple**: Connect your voice to any LLM and let intelligence flow through conversation.

---

## Why Open Voice?

Today, voice AI is either:
- **Locked in proprietary platforms** (ChatGPT Voice, Gemini Live)
- **Requires complex infrastructure** (WebRTC servers, specialized frameworks)
- **Limited to specific models** (no choice, no flexibility)

Open Voice changes this.

---

## Core Principles

### 1. Local-First, Any LLM

Run everything on your machine:
- Local LLMs (Ollama, llama.cpp, LM Studio)
- Local speech recognition
- Local voice synthesis
- No internet required

Or connect to cloud when you want:
- Cloud APIs (OpenAI, Anthropic, Google, etc.)
- Custom endpoints
- Multiple providers simultaneously

**Your data stays yours. You're not locked in. Ever.**

### 2. Smart Model Selection

Not every question needs the most powerful model.

Open Voice intelligently routes your queries:
- **Simple questions** → Fast, efficient models
- **Complex tasks** → Capable, powerful models

This means:
- Lower costs
- Faster responses for simple queries
- Full power when you need it

### 3. Raw and Minimal

No heavy frameworks. No complex dependencies. No infrastructure requirements.

Just voice in, intelligence out.

---

## How It Works

```
        ┌─────────────────────────────────────┐
        │                                     │
        ▼                                     │
    You speak                                 │
        │                                     │
        ▼                                     │
   ┌─────────┐                                │
   │  Voice  │ ─── Real-time speech recognition
   │  Input  │                                │
   └────┬────┘                                │
        │                                     │
        ▼                                     │
   ┌─────────┐                                │
   │  Smart  │ ─── Analyzes complexity        │
   │ Router  │ ─── Selects best model         │
   └────┬────┘                                │
        │                                     │
        ▼                                     │
   ┌─────────┐                                │
   │   LLM   │ ─── Any model you configure    │
   │  (Any)  │ ─── Streams response           │
   └────┬────┘                                │
        │                                     │
        ▼                                     │
   ┌─────────┐                                │
   │  Voice  │ ─── Natural speech synthesis   │
   │ Output  │                                │
   └────┬────┘                                │
        │                                     │
        ▼                                     │
    You listen ───► You interrupt? ───────────┘
        │                  
        ▼                  
   Continue or                
    ask more                 
        │                    
        └─────────────────────────────────────┘
```

**Interruption handling**: Speak anytime - Open Voice listens, stops the current response, and processes your new input. The conversation flows naturally, just like talking to a human.

---

## Features

- **Real-time conversation** - Speak naturally, get immediate responses
- **Model agnostic** - Works with any LLM provider
- **Smart routing** - Automatic model selection based on query complexity
- **Streaming throughout** - Low latency from voice to voice
- **Simple setup** - Minimal configuration required
- **Open source** - MIT licensed, free forever

---

## Use Cases

- **Hands-free coding assistance** - Talk through problems while you code
- **Voice-first applications** - Build apps where voice is the primary interface
- **Accessibility** - Make LLMs accessible to those who prefer or need voice
- **Rapid prototyping** - Test voice interactions with any model
- **Learning and education** - Conversational tutoring with any AI

---

## Quick Start

Coming soon.

Open Voice is **local-first**. Speech recognition, voice synthesis, and LLM inference can all run on your machine. No API keys required. No cloud dependencies. Your conversations stay private.

Want to use cloud providers? That works too - Open Voice connects to any LLM endpoint you configure.

---

## The Vision

We believe voice is the most natural interface for intelligence.

Typing is a bottleneck. Reading is slow. But conversation? Conversation is how humans have shared knowledge for millennia.

Open Voice removes the barriers between you and AI. No keyboards required. Just your voice and unlimited intelligence.

**Talk to any LLM.**

---

## Contributing

Open Voice is an independent project developed during FOSSHack 2026. We welcome contributions!

- Issues and feature requests
- Pull requests
- Documentation improvements
- Testing and feedback

---

## License

MIT License - Use it, modify it, ship it.

---

<p align="center">
  <b>Open Voice</b><br>
  Talk to any LLM.
</p>
