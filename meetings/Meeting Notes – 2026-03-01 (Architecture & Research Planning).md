# Initial Discussion: Architecture and Services – 2026-03-01

## Meeting Information

**Meeting Date/Time:** March 1, 2026 | 2:00 PM – 3:00 PM IST \
**Meeting Purpose:** Initial architectural discussion and research planning for Open Voice project \
**Meeting Location:** Google Meet \
**Note Taker:** Sunil Vishwakarma 

## Repository

Project Repository: https://github.com/sahilchouksey/open-voice

---

## Attendees

- Sahil Chouksey (Maintainer)
- Sunil Vishwakarma (Contributor)

---

## Meeting Context

This was the first project meeting for **FOSS United FOSSHack 2026**.
The team is currently in the **research and architecture phase**. No implementation has started yet.

The primary goal is to design a **clean, scalable, and readable architecture** that:

- Follows strong coding and folder structure practices
- Is easy for contributors to understand
- Avoids redundant or tightly coupled designs
- Supports long-term maintainability
- Is optimized for low-latency real-time voice interaction

The long-term objective is to build an **open-source, platform-agnostic SDK** that enables real-time voice conversations with LLMs using fully open-source components.

---

## High-Level System Overview

The system will implement a real-time conversational voice pipeline:

**User Speech → STT → Router → LLM (via SDK) → TTS → Audio Response**

The architecture must also support:

- Continuous conversation loops
- User interruptions during responses
- Session management handled by the SDK
- Low-latency streaming where possible

---

## Core Components (Proposed)

| Component       | Description                                                 |
| --------------- | ----------------------------------------------------------- |
| STT             | Converts user speech into text                              |
| Router          | Routes requests and manages conversation flow               |
| LLM Integration | Handles interaction with the LLM through an open-source SDK |
| TTS             | Converts model responses back into speech                   |

### LLM Integration

The system will use an open-source **SDK** for:

- Session management
- Conversation state handling
- Model communication
- Server orchestration (handled out-of-the-box)

---

## Technology Exploration (Research Phase)

### STT (Speech-to-Text)

Currently exploring:

- Whisper v3 Turbo (primary candidate)
- Other open-source alternatives (under evaluation)

### TTS (Text-to-Speech)

Initial candidates:

- Kokoro TTS
- Chatterbox
- Additional open-source alternatives are being researched

### Routing

- Will use an open-source routing approach
- Final framework/tooling yet to be decided

---

## Architecture Principles (Under Discussion)

- Modular design with clearly defined service boundaries
- Internal complexity encapsulated within components
- Interfaces exposed between components
- Components may depend on each other only through defined interfaces
- Architectural decisions will be documented and finalized internally

---

## Weekly Plan (March 1 – March 7)

**Primary Goal:**
Build a **Proof of Concept (POC)** for the full voice pipeline to validate feasibility and latency.

### Focus Areas

- Research open-source options for each component
- Evaluate performance and latency tradeoffs
- Share findings daily between team members
- Finalize service choices based on research
- Begin POC implementation for:

  ##### STT → Router → LLM → TTS pipeline

### Responsibilities

| Member            | Focus Area                              |
| ----------------- | --------------------------------------- |
| Sahil Chouksey    | STT research, TTS research              |
| Sunil Vishwakarma | Router design, LLM integration research |

---

## Workflow & Contribution Plan

- Daily commits to maintain consistent progress
- Research findings to be shared and discussed internally
- Architectural documents to be created and refined
- Decisions to be finalized collaboratively before implementation
- Meetings will be documented in the `/meetings` directory

---

## Action Items

| Done? | Item                          | Responsible | Due Date    |
| ----- | ----------------------------- | ----------- | ----------- |
|  ☑    | Research STT options          | Sahil       | This week   |
|  ☐    | Research TTS options          | Sahil       | This week   |
|  ☐    | Research routing approach     | Sunil       | This week   |
|  ☐    | Research LLM SDK integration  | Sunil       | This week   |
|  ☐    | Share daily findings          | Both        | Daily       |
|  ☐    | Build end-to-end POC pipeline | Both        | End of week |

---

## Other Notes

- Final product will be an SDK, but initial focus is validating the full pipeline through a working POC.
- Latency optimization is a key success criterion.
- Component selections are provisional and subject to change based on research outcomes.

