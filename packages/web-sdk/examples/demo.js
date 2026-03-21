const els = {
  baseUrl: document.getElementById("baseUrl"),
  voiceId: document.getElementById("voiceId"),
  turnQueuePolicy: document.getElementById("turnQueuePolicy"),
  interruptMode: document.getElementById("interruptMode"),
  interruptCooldown: document.getElementById("interruptCooldown"),
  endpointingMode: document.getElementById("endpointingMode"),
  endpointingMinDelay: document.getElementById("endpointingMinDelay"),
  endpointingMaxDelay: document.getElementById("endpointingMaxDelay"),
  connectBtn: document.getElementById("connectBtn"),
  disconnectBtn: document.getElementById("disconnectBtn"),
  listenBtn: document.getElementById("listenBtn"),
  stopBtn: document.getElementById("stopBtn"),
  interruptBtn: document.getElementById("interruptBtn"),
  sessionStatus: document.getElementById("sessionStatus"),
  sessionId: document.getElementById("sessionId"),
  turnState: document.getElementById("turnState"),
  micMeter: document.getElementById("micMeter"),
  micMeta: document.getElementById("micMeta"),
  micState: document.getElementById("micState"),
  sttState: document.getElementById("sttState"),
  sttText: document.getElementById("sttText"),
  routerState: document.getElementById("routerState"),
  routerText: document.getElementById("routerText"),
  queueText: document.getElementById("queueText"),
  llmState: document.getElementById("llmState"),
  thinkingText: document.getElementById("thinkingText"),
  responseText: document.getElementById("responseText"),
  ttsState: document.getElementById("ttsState"),
  ttsMeta: document.getElementById("ttsMeta"),
  metricsText: document.getElementById("metricsText"),
  transcript: document.getElementById("transcript"),
  events: document.getElementById("events"),
  audioPlayer: document.getElementById("audioPlayer"),
  tabDetailed: document.getElementById("tabDetailed"),
  tabMinimal: document.getElementById("tabMinimal"),
  detailedView: document.getElementById("detailedView"),
  minimalView: document.getElementById("minimalView"),
  miniConnectBtn: document.getElementById("miniConnectBtn"),
  miniDisconnectBtn: document.getElementById("miniDisconnectBtn"),
  miniListenBtn: document.getElementById("miniListenBtn"),
  miniStopBtn: document.getElementById("miniStopBtn"),
  miniInterruptBtn: document.getElementById("miniInterruptBtn"),
  miniSessionBadge: document.getElementById("miniSessionBadge"),
  miniTurnBadge: document.getElementById("miniTurnBadge"),
  miniStatusText: document.getElementById("miniStatusText"),
  radialViz: document.getElementById("radialViz"),
  miniTranscript: document.getElementById("miniTranscript"),
}

const state = {
  socket: null,
  sessionId: null,
  mic: null,
  pcmPlayer: null,
  eventLines: [],
  responseText: "",
  thinkingText: "",
  toolLines: [],
  seenToolCalls: new Set(),
  seenToolSpeechAnnouncements: new Set(),
  micLevel: 0,
  activeAssistantTurnId: null,
  activeGenerationId: null,  // Track current generation for interruption
  rejectedGenerationIds: new Set(),  // Track interrupted generations to reject in-flight chunks
  lastUserFinal: {
    turnId: null,
    text: null,
  },
  thinkingPlayer: null,
  sessionState: {
    sessionStatus: "disconnected",
    turnPhase: "idle",
    vadSpeaking: false,
    vadProbability: null,
    routeName: null,
    provider: null,
    model: null,
    queuePending: 0,
    queuePolicy: "enqueue",
  },
}

const OPEN_VOICE_SYSTEM_PROMPT = [
  "You are Open Voice, a realtime voice-first assistant for conversation and web research.",
  "Prioritize natural spoken responses that are concise, clear, and interruption-friendly.",
  "If a newer user utterance arrives, immediately abandon stale context and continue from the latest user intent.",
  "For current events or other time-sensitive questions, always search the web before answering.",
  "Never guess or rely on stale memory for news, politics, markets, sports, weather, or other live facts.",
  "Use tools when needed, but never expose internal routing, model, or tool implementation details.",
].join(" ")

function formatMs(value) {
  if (value == null || Number.isNaN(value)) return "-"
  return `${Math.round(value)}ms`
}

function renderQueueAndMetrics() {
  els.queueText.textContent = `Queue: ${state.sessionState.queuePending || 0} pending turns (${state.sessionState.queuePolicy || "enqueue"})`
}

class StreamingPcmPlayer {
  constructor() {
    this.audioContext = null
    this.nextStartTime = 0
    this.fallbackChunks = []
  }

  async appendPcm16(bytes, sampleRate) {
    if (!this.audioContext || this.audioContext.state === "closed") {
      this.audioContext = new (window.AudioContext || window.webkitAudioContext)({ sampleRate })
      this.nextStartTime = this.audioContext.currentTime
    }
    if (this.audioContext.state === "suspended") {
      await this.audioContext.resume()
    }

    const int16 = new Int16Array(bytes.buffer, bytes.byteOffset, bytes.byteLength / 2)
    const audioBuffer = this.audioContext.createBuffer(1, int16.length, sampleRate)
    const channel = audioBuffer.getChannelData(0)
    for (let i = 0; i < int16.length; i += 1) {
      channel[i] = int16[i] / 0x8000
    }

    const source = this.audioContext.createBufferSource()
    source.buffer = audioBuffer
    source.connect(this.audioContext.destination)
    const when = Math.max(this.nextStartTime, this.audioContext.currentTime + 0.01)
    source.start(when)
    this.nextStartTime = when + audioBuffer.duration

    this.fallbackChunks.push(bytes.slice())
    els.audioPlayer.src = URL.createObjectURL(makeWavBlob(this.fallbackChunks, sampleRate))
  }

  async close() {
    await this.audioContext?.close()
    this.audioContext = null
    this.nextStartTime = 0
    this.fallbackChunks = []
  }
}

class ThinkingAudioPlayer {
  constructor(options = {}) {
    this.audioUrl = options.audioUrl || "./assets/sfx/achievement-fx.wav"
    this.gapMs = Number.isFinite(options.gapMs) ? options.gapMs : 180
    this.volume = Number.isFinite(options.volume) ? options.volume : 0.2
    this.audio = null
    this.isPlaying = false
    this._restartTimer = null
  }

  _clearRestartTimer() {
    if (this._restartTimer) {
      clearTimeout(this._restartTimer)
      this._restartTimer = null
    }
  }

  _buildAudio() {
    const audio = new Audio(this.audioUrl)
    audio.preload = "auto"
    audio.volume = this.volume
    audio.addEventListener("ended", () => {
      if (!this.isPlaying) return
      this._clearRestartTimer()
      this._restartTimer = setTimeout(() => {
        if (!this.isPlaying || !this.audio) return
        this.audio.currentTime = 0
        this.audio.play().catch(() => {})
      }, this.gapMs)
    })
    return audio
  }

  async start() {
    if (this.isPlaying) return
    if (!this.audio) {
      this.audio = this._buildAudio()
    }
    this.isPlaying = true
    try {
      this.audio.currentTime = 0
      await this.audio.play()
    } catch {
      this.isPlaying = false
    }
  }

  stop() {
    if (!this.audio) {
      this._clearRestartTimer()
      this.isPlaying = false
      return
    }
    this.isPlaying = false
    this._clearRestartTimer()
    this.audio.pause()
    this.audio.currentTime = 0
  }
}

class BrowserMicInput {
  constructor(onChunk, onLevel) {
    this.onChunk = onChunk
    this.onLevel = onLevel
    this.sequence = 0
    this.ctx = null
    this.processor = null
    this.source = null
    this.stream = null
  }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: { ideal: 24000 },
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    })
    this.ctx = new AudioContext({ sampleRate: 24000 })
    this.source = this.ctx.createMediaStreamSource(this.stream)
    this.processor = this.ctx.createScriptProcessor(4096, 1, 1)
    this.processor.onaudioprocess = async (event) => {
      const channel = event.inputBuffer.getChannelData(0)
      let peak = 0
      const pcm = new Int16Array(channel.length)
      for (let i = 0; i < channel.length; i += 1) {
        const sample = Math.max(-1, Math.min(1, channel[i]))
        peak = Math.max(peak, Math.abs(sample))
        pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff
      }
      this.onLevel(peak)
      await this.onChunk({
        type: "audio.append",
        session_id: state.sessionId,
        chunk: {
          chunk_id: `${state.sessionId}:${this.sequence}`,
          sequence: this.sequence,
          encoding: "pcm_s16le",
          sample_rate_hz: this.ctx.sampleRate,
          channels: 1,
          duration_ms: (pcm.length / this.ctx.sampleRate) * 1000,
          transport: "inline-base64",
          data_base64: arrayBufferToBase64(pcm.buffer),
        },
      })
      this.sequence += 1
    }
    this.source.connect(this.processor)
    this.processor.connect(this.ctx.destination)
  }

  async stop() {
    this.processor?.disconnect()
    this.source?.disconnect()
    this.stream?.getTracks().forEach((track) => track.stop())
    await this.ctx?.close()
    this.processor = null
    this.source = null
    this.stream = null
    this.ctx = null
    this.onLevel(0)
  }
}

function setButtons(connected, listening = false) {
  els.connectBtn.disabled = connected
  els.disconnectBtn.disabled = !connected
  els.listenBtn.disabled = !connected || listening
  els.stopBtn.disabled = !listening
  els.interruptBtn.disabled = !connected
  if (els.miniConnectBtn) els.miniConnectBtn.disabled = connected
  if (els.miniDisconnectBtn) els.miniDisconnectBtn.disabled = !connected
  if (els.miniListenBtn) els.miniListenBtn.disabled = !connected || listening
  if (els.miniStopBtn) els.miniStopBtn.disabled = !listening
  if (els.miniInterruptBtn) els.miniInterruptBtn.disabled = !connected
}

function setBadge(node, mode, text) {
  node.className = `badge ${mode}`
  node.textContent = text
}

function appendEvent(event) {
  if (event.type === "vad.state" && event.kind === "inference") {
    return
  }
  state.eventLines.push(JSON.stringify(event, null, 2))
  state.eventLines = state.eventLines.slice(-30)
  els.events.textContent = state.eventLines.join("\n\n")
}

function appendTranscript(role, text) {
  const bubble = document.createElement("div")
  bubble.className = `bubble ${role}`
  bubble.textContent = text
  els.transcript.appendChild(bubble)
  if (els.miniTranscript) {
    const miniBubble = document.createElement("div")
    miniBubble.className = `bubble ${role}`
    miniBubble.textContent = text
    els.miniTranscript.appendChild(miniBubble)
  }
}

function activateTab(mode) {
  if (!els.tabDetailed || !els.tabMinimal || !els.detailedView || !els.minimalView) {
    return
  }
  const detailed = mode !== "minimal"
  els.tabDetailed.classList.toggle("active", detailed)
  els.tabDetailed.setAttribute("aria-selected", detailed ? "true" : "false")
  els.tabMinimal.classList.toggle("active", !detailed)
  els.tabMinimal.setAttribute("aria-selected", detailed ? "false" : "true")
  els.detailedView.hidden = !detailed
  els.minimalView.hidden = detailed
  els.detailedView.classList.toggle("active", detailed)
  els.minimalView.classList.toggle("active", !detailed)
}

function updateMinimalUi() {
  if (!els.radialViz) return
  const phase = state.sessionState.turnPhase
  const connected = state.sessionState.sessionStatus !== "disconnected"

  let mode = "idle"
  if (!connected) {
    mode = "idle"
  } else if (phase === "agent_speaking") {
    mode = "speaking"
  } else if (phase === "processing" || phase === "speech_ended") {
    mode = "thinking"
  } else if (phase === "user_speaking") {
    mode = "listening"
  } else {
    mode = "ready"
  }

  els.radialViz.className = `radial-viz ${mode}`
  const micLevel = Math.max(0.14, Math.min(1, (state.micLevel || 0) / 100))
  els.radialViz.style.setProperty("--mic-level", String(micLevel))

  if (els.miniSessionBadge) {
    const sessionText = state.sessionState.sessionStatus || "disconnected"
    const sessionKind = connected ? "live" : "idle"
    setBadge(els.miniSessionBadge, sessionKind, sessionText)
  }

  if (els.miniTurnBadge) {
    const labelMap = {
      idle: "idle",
      listening: "listening",
      user_speaking: "you",
      speech_ended: "processing",
      processing: "thinking",
      agent_speaking: "agent",
    }
    const turnText = labelMap[phase] || "idle"
    const turnKind = ["agent_speaking", "user_speaking", "processing", "speech_ended"].includes(phase) ? "busy" : "idle"
    setBadge(els.miniTurnBadge, turnKind, turnText)
  }

  if (els.miniStatusText) {
    if (!connected) {
      els.miniStatusText.textContent = "Connect to start a voice session."
    } else if (phase === "user_speaking") {
      els.miniStatusText.textContent = "Listening to you..."
    } else if (phase === "processing" || phase === "speech_ended") {
      els.miniStatusText.textContent = "Thinking..."
    } else if (phase === "agent_speaking") {
      els.miniStatusText.textContent = "Assistant is speaking."
    } else {
      els.miniStatusText.textContent = "Ready for your next turn."
    }
  }
}

function appendUserFinalTranscript(event) {
  const turnId = event.turn_id || null
  const text = event.text || ""
  if (!text.trim()) return
  if (state.lastUserFinal.turnId === turnId && state.lastUserFinal.text === text) {
    return
  }
  state.lastUserFinal = { turnId, text }
  appendTranscript("user", text)
}

function safeStringify(value, max = 260) {
  if (value == null) return ""
  try {
    const raw = typeof value === "string" ? value : JSON.stringify(value)
    if (raw.length <= max) return raw
    return `${raw.slice(0, max)}...`
  } catch {
    return String(value)
  }
}

function renderThinkingPanel() {
  const parts = []
  const reasoning = state.thinkingText.trim()
  if (reasoning) parts.push(reasoning)
  if (state.toolLines.length > 0) {
    parts.push(`Tools:\n${state.toolLines.join("\n")}`)
  }
  els.thinkingText.textContent = parts.join("\n\n") || "No reasoning yet."
}

function resetAssistantPanels(turnId = null) {
  if (turnId && state.activeAssistantTurnId === turnId) {
    return
  }
  state.activeAssistantTurnId = turnId
  state.responseText = ""
  state.thinkingText = ""
  state.toolLines = []
  // FIX: Clear seen tool calls for new turn to avoid cross-turn duplication
  state.seenToolCalls = new Set()
  state.seenToolSpeechAnnouncements = new Set()
  els.responseText.textContent = "-"
  renderThinkingPanel()
}

function appendToolLine(text) {
  if (!text) return
  state.toolLines.push(text)
  state.toolLines = state.toolLines.slice(-14)
  renderThinkingPanel()
}

function formatToolUpdate(event) {
  const status = event.status || "update"
  const callId = event.call_id ? ` #${event.call_id}` : ""
  const source = event.is_mcp ? "mcp" : "tool"
  const base = `[${source}:${status}] ${event.tool_name || "unknown"}${callId}`
  const out = safeStringify(event.tool_output)
  const err = safeStringify(event.tool_error)
  if (err) return `${base} error=${err}`
  if (out) return `${base} output=${out}`
  return base
}

function stopToolAnnouncement() {
  if (!window.speechSynthesis) return
  window.speechSynthesis.cancel()
}

function toolStatusBucket(status) {
  const normalized = (status || "").toLowerCase()
  if (["running", "started", "pending", "in_progress", "executing", "working"].includes(normalized)) {
    return "start"
  }
  if (["completed", "done", "success", "succeeded", "ok", "finished"].includes(normalized)) {
    return "end"
  }
  if (["error", "failed", "failure", "cancelled", "canceled", "timeout"].includes(normalized)) {
    return "error"
  }
  return null
}

function toolAnnouncementText(event, bucket) {
  const toolName = (event.tool_name || "").toLowerCase()
  const webSearchTool = toolName.includes("web") || toolName.includes("search")

  if (bucket === "start") {
    return webSearchTool ? "Searching the web now." : "Let me check that now."
  }
  if (bucket === "end") {
    return webSearchTool ? "Done searching the web." : "Done checking that."
  }
  if (bucket === "error") {
    return "I hit an issue while checking that."
  }
  return ""
}

function announceToolUpdate(event) {
  if (!window.speechSynthesis) return

  const bucket = toolStatusBucket(event.status)
  if (!bucket) return

  const keyBase = event.call_id || event.tool_name || "tool"
  const key = `${keyBase}:${bucket}`
  if (state.seenToolSpeechAnnouncements.has(key)) {
    return
  }
  state.seenToolSpeechAnnouncements.add(key)

  const text = toolAnnouncementText(event, bucket)
  if (!text) return

  try {
    stopToolAnnouncement()
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.rate = 1.02
    utterance.pitch = 1.0
    utterance.volume = 0.75
    window.speechSynthesis.speak(utterance)
  } catch {
    // Ignore speech synthesis failures in demo mode.
  }
}

function formatUsage(event) {
  const usage = event.usage || {}
  const input = usage.input_tokens ?? 0
  const output = usage.output_tokens ?? 0
  const total = usage.total_tokens ?? input + output
  return `[tokens] in=${input} out=${output} total=${total}`
}

function syncUiFromSessionState() {
  els.sessionStatus.textContent = state.sessionState.sessionStatus

  if (state.sessionState.turnPhase === "user_speaking") {
    setBadge(els.turnState, "live", "speaking")
  } else if (state.sessionState.turnPhase === "speech_ended") {
    setBadge(els.turnState, "busy", "speech ended")
  } else if (state.sessionState.turnPhase === "processing") {
    setBadge(els.turnState, "busy", "processing")
  } else if (state.sessionState.turnPhase === "agent_speaking") {
    setBadge(els.turnState, "busy", "responding")
  } else if (state.sessionState.turnPhase === "listening") {
    setBadge(els.turnState, "busy", "listening")
  } else {
    setBadge(els.turnState, "idle", "idle")
  }

  if (state.sessionState.vadSpeaking) {
    setBadge(els.sttState, "busy", "speech")
  }

  if (state.sessionState.routeName) {
    setBadge(els.routerState, "live", state.sessionState.routeName)
  }
  if (state.sessionState.provider || state.sessionState.model) {
    els.routerText.textContent = `${state.sessionState.provider || "-"}/${state.sessionState.model || "-"}`
  }

  renderQueueAndMetrics()
  updateMinimalUi()
}

function parseBaseUrl() {
  return els.baseUrl.value.trim() || `${location.protocol}//${location.host}`
}

function wsUrl(baseUrl, sessionId) {
  const url = new URL(baseUrl)
  if (url.protocol === "http:") url.protocol = "ws:"
  if (url.protocol === "https:") url.protocol = "wss:"
  url.pathname = "/v1/realtime/conversation"
  if (sessionId) url.searchParams.set("session_id", sessionId)
  return url.toString()
}

async function requestJson(path, init) {
  const response = await fetch(`${parseBaseUrl()}${path}`, init)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return await response.json()
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer)
  let binary = ""
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary)
}

function base64ToBytes(text) {
  const binary = atob(text)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return bytes
}

function writeString(view, offset, value) {
  for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i))
}

function makeWavBlob(chunks, sampleRate) {
  const byteLength = chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0)
  const wav = new ArrayBuffer(44 + byteLength)
  const view = new DataView(wav)
  const payload = new Uint8Array(wav, 44)
  let offset = 0
  for (const chunk of chunks) {
    payload.set(chunk, offset)
    offset += chunk.byteLength
  }
  writeString(view, 0, "RIFF")
  view.setUint32(4, 36 + byteLength, true)
  writeString(view, 8, "WAVE")
  writeString(view, 12, "fmt ")
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeString(view, 36, "data")
  view.setUint32(40, byteLength, true)
  return new Blob([wav], { type: "audio/wav" })
}

function setMicLevel(level) {
  const pct = Math.min(100, Math.round(level * 140))
  state.micLevel = pct
  els.micMeter.style.width = `${pct}%`
  if (state.sessionState.turnPhase !== "speech_ended") {
    els.micMeta.textContent = pct > 0 ? `Live mic level ${pct}%` : "Waiting for speech..."
  }
  updateMinimalUi()
}

function sendJson(payload) {
  if (!state.socket || state.socket.readyState !== WebSocket.OPEN) {
    throw new Error("WebSocket not connected")
  }
  state.socket.send(JSON.stringify(payload))
}

async function connect() {
  const sessionState = await requestJson("/v1/sessions", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      metadata: {
        source: "demo",
        voice_id: els.voiceId.value,
        language: "en-US",
      },
      runtime_config: {
        llm: {
          enable_fast_ack: false,
          opencode_mode: "build",
          opencode_force_system_override: true,
          system_prompt: OPEN_VOICE_SYSTEM_PROMPT,
        },
        turn_detection: {
          mode: "hybrid",
          transcript_timeout_ms: 250,
          min_silence_duration_ms: 500,
          min_speech_duration_ms: 80,
        },
        turn_queue: {
          policy: els.turnQueuePolicy.value || "enqueue",
        },
        interruption: {
          mode: els.interruptMode?.value || "immediate",
          cooldown_ms: parseInt(els.interruptCooldown?.value || "300", 10),
        },
        endpointing: {
          mode: els.endpointingMode?.value || "fixed",
          min_delay: parseFloat(els.endpointingMinDelay?.value || "0.5"),
          max_delay: parseFloat(els.endpointingMaxDelay?.value || "3.0"),
        },
      },
    }),
  })
  state.sessionId = sessionState.session_id
  state.sessionState.queuePending = 0
  state.sessionState.queuePolicy = els.turnQueuePolicy.value || "enqueue"
  const socket = new WebSocket(wsUrl(parseBaseUrl(), state.sessionId))
  state.socket = socket
  state.pcmPlayer = new StreamingPcmPlayer()
  state.thinkingPlayer = new ThinkingAudioPlayer()

  await new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true })
    socket.addEventListener("error", () => reject(new Error("WebSocket failed")), { once: true })
  })

  socket.addEventListener("message", async (event) => {
    const payload = JSON.parse(event.data)
    appendEvent(payload)
    await handleEvent(payload)
  })

  socket.addEventListener("close", async () => {
    stopToolAnnouncement()
    await state.mic?.stop().catch(() => {})
    state.mic = null
    await state.pcmPlayer?.close().catch(() => {})
    state.pcmPlayer = null
    state.socket = null
    state.sessionId = null
    setButtons(false)
    els.sessionStatus.textContent = "disconnected"
  })

  sendJson({
    type: "session.start",
    session_id: state.sessionId,
    metadata: { source: "demo", voice_id: els.voiceId.value, language: "en-US" },
    config: {
      llm: {
        enable_fast_ack: false,
        opencode_mode: "build",
        opencode_force_system_override: true,
        system_prompt: OPEN_VOICE_SYSTEM_PROMPT,
      },
      turn_detection: {
        mode: "hybrid",
        transcript_timeout_ms: 1000,
        min_silence_duration_ms: 2000,
        min_speech_duration_ms: 80,
      },
      turn_queue: {
        policy: els.turnQueuePolicy.value || "enqueue",
      },
      interruption: {
        mode: els.interruptMode?.value || "immediate",
        cooldown_ms: parseInt(els.interruptCooldown?.value || "300", 10),
      },
      endpointing: {
        mode: els.endpointingMode?.value || "fixed",
        min_delay: parseFloat(els.endpointingMinDelay?.value || "0.5"),
        max_delay: parseFloat(els.endpointingMaxDelay?.value || "3.0"),
      },
    },
  })

  els.sessionId.textContent = state.sessionId
  els.sessionStatus.textContent = "connected"
  setButtons(true, false)
}

async function disconnect() {
  await state.mic?.stop().catch(() => {})
  state.mic = null
  stopToolAnnouncement()
  state.thinkingPlayer?.stop()
  state.thinkingPlayer = null
  if (state.socket?.readyState === WebSocket.OPEN && state.sessionId) {
    sendJson({ type: "session.close", session_id: state.sessionId })
    state.socket.close()
  }
  await state.pcmPlayer?.close().catch(() => {})
  state.pcmPlayer = null
  // Clear rejected generations on disconnect
  state.rejectedGenerationIds.clear()
}

async function startListening() {
  if (!window.isSecureContext) {
    throw new Error("Microphone requires HTTPS (or localhost). Use a secure URL.")
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error("Microphone API unavailable in this browser/context")
  }
  state.mic = new BrowserMicInput(async (message) => sendJson(message), setMicLevel)
  await state.mic.start()
  els.sessionStatus.textContent = "listening"
  setBadge(els.turnState, "live", "speaking")
  setButtons(true, true)
}

async function stopListening() {
  await state.mic?.stop()
  state.mic = null
  setBadge(els.turnState, "idle", "idle")
  setButtons(true, false)
}

async function handleEvent(event) {
  if (event.type === "session.ready") {
    state.sessionState.sessionStatus = "ready"
    state.sessionState.turnPhase = "listening"
    syncUiFromSessionState()
    return
  }
  if (event.type === "session.status") {
    state.sessionState.sessionStatus = event.status
    if (event.status === "thinking") {
      state.sessionState.turnPhase = "processing"
    } else if (event.status === "speaking") {
      state.sessionState.turnPhase = "agent_speaking"
    } else if (event.status === "listening" || event.status === "ready") {
      state.sessionState.turnPhase = state.sessionState.vadSpeaking ? "user_speaking" : "listening"
    } else {
      state.sessionState.turnPhase = "idle"
    }
    syncUiFromSessionState()
    return
  }
  if (event.type === "vad.state") {
    if (typeof event.speaking === "boolean") {
      state.sessionState.vadSpeaking = event.speaking
    }
    state.sessionState.vadProbability = event.probability ?? null
    
    // Check if speech just ended
    if (!event.speaking && event.kind === "end_of_speech") {
      state.sessionState.turnPhase = "speech_ended"
      setBadge(els.micState, "live", "stopped")
      els.micMeta.textContent = "Speech ended, processing..."
    } else if (event.speaking) {
      state.sessionState.turnPhase = "user_speaking"
      setBadge(els.micState, "busy", "speaking")
      els.micMeta.textContent = "Live mic level " + (state.micLevel || 0) + "%"
    } else {
      state.sessionState.turnPhase = state.sessionState.sessionStatus === "listening" ? "listening" : state.sessionState.turnPhase
    }
    syncUiFromSessionState()
    return
  }
  if (event.type === "stt.partial") {
    setBadge(els.sttState, "busy", "listening")
    els.sttText.textContent = event.text || "..."
    state.sessionState.turnPhase = state.sessionState.vadSpeaking ? "user_speaking" : "listening"
    syncUiFromSessionState()
    return
  }
  if (event.type === "stt.final") {
    const incomingGenerationId = event.generation_id || null
    const incomingTurnId = event.turn_id || null
    const sameGeneration = Boolean(
      incomingGenerationId && incomingGenerationId === state.activeGenerationId
    )
    const sameTurn = Boolean(incomingTurnId && incomingTurnId === state.activeAssistantTurnId)
    const shouldInterruptCurrentAudio = Boolean(state.activeGenerationId) && !(
      sameGeneration || (!incomingGenerationId && sameTurn)
    )

    if (shouldInterruptCurrentAudio) {
      console.log(`[INTERRUPT] stt.final indicates new turn; stopping generation ${state.activeGenerationId}`)
      state.rejectedGenerationIds.add(state.activeGenerationId)
      await state.pcmPlayer?.close().catch(() => {})
      state.pcmPlayer = new StreamingPcmPlayer()
      state.thinkingPlayer?.stop()
      if (state.rejectedGenerationIds.size > 10) {
        const iterator = state.rejectedGenerationIds.values()
        state.rejectedGenerationIds.delete(iterator.next().value)
      }
    }
    resetAssistantPanels(event.turn_id || null)
    setBadge(els.sttState, "live", "final")
    setBadge(els.routerState, "busy", "routing")
    setBadge(els.llmState, "busy", "queued")
    setBadge(els.ttsState, "idle", "waiting")
    els.sttText.textContent = event.text || "-"
    els.routerText.textContent = "Selecting route..."
    els.ttsMeta.textContent = "Waiting for assistant response..."
    appendUserFinalTranscript(event)
    state.sessionState.turnPhase = "processing"
    if (incomingGenerationId) {
      state.activeGenerationId = incomingGenerationId
    }
    syncUiFromSessionState()
    return
  }
  if (event.type === "route.selected") {
    state.sessionState.routeName = event.route_name
    state.sessionState.provider = event.provider || null
    state.sessionState.model = event.model || null
    els.routerText.textContent = `${state.sessionState.provider || "-"}/${state.sessionState.model || "-"}`
    setBadge(els.routerState, "live", event.route_name || "selected")
    setBadge(els.llmState, "busy", "thinking")
    // Track generation_id if provided
    if (event.generation_id) {
      state.activeGenerationId = event.generation_id
    }
    syncUiFromSessionState()
    return
  }
  if (event.type === "turn.queued") {
    state.sessionState.queuePending = event.queue_size || 0
    if (event.policy) state.sessionState.queuePolicy = event.policy
    els.metricsText.textContent = `Queued (${event.source || "unknown"}) - pending ${event.queue_size}`
    syncUiFromSessionState()
    return
  }
  if (event.type === "turn.metrics") {
    const parts = [
      `queue=${formatMs(event.queue_delay_ms)}`,
      `stt->route=${formatMs(event.stt_to_route_ms)}`,
      `route->llm1=${formatMs(event.route_to_llm_first_delta_ms)}`,
      `llm1->tts1=${formatMs(event.llm_first_delta_to_tts_first_chunk_ms)}`,
      `stt->tts1=${formatMs(event.stt_to_tts_first_chunk_ms)}`,
      `turn->llm1=${formatMs(event.turn_to_first_llm_delta_ms)}`,
      `turn_total=${formatMs(event.turn_to_complete_ms)}`,
      `cancelled=${event.cancelled ? "yes" : "no"}`,
    ]
    if (event.reason) parts.push(`reason=${event.reason}`)
    els.metricsText.textContent = parts.join(" | ")
    if (!event.cancelled && state.sessionState.queuePending > 0) {
      state.sessionState.queuePending = Math.max(0, state.sessionState.queuePending - 1)
    }
    syncUiFromSessionState()
    return
  }
  if (event.type === "llm.phase") {
    if (event.turn_id) {
      resetAssistantPanels(event.turn_id)
    }
    if (event.phase === "thinking") {
      setBadge(els.llmState, "busy", "thinking")
      // Start thinking audio
      state.thinkingPlayer?.start().catch(() => {})
    } else if (event.phase === "generating") {
      setBadge(els.llmState, "live", "generating")
      // Stop thinking audio when generating starts
      state.thinkingPlayer?.stop()
    } else {
      setBadge(els.llmState, "live", event.phase)
      // Stop thinking audio for any other phase
      state.thinkingPlayer?.stop()
    }
    state.sessionState.turnPhase = event.phase === "thinking" ? "processing" : state.sessionState.turnPhase
    // Track generation_id if provided
    if (event.generation_id) {
      state.activeGenerationId = event.generation_id
    }
    syncUiFromSessionState()
    return
  }
  if (event.type === "llm.reasoning.delta") {
    if (event.turn_id) {
      resetAssistantPanels(event.turn_id)
    }
    state.thinkingText += event.delta || ""
    renderThinkingPanel()
    return
  }
  if (event.type === "llm.response.delta") {
    if (event.turn_id) {
      resetAssistantPanels(event.turn_id)
    }
    if (els.responseText.textContent === "-") {
      els.responseText.textContent = ""
    }
    // FIX: Filter out markdown artifacts and asterisks from display
    let delta = event.delta || ""
    delta = delta.replace(/\*\*/g, "")  // Remove bold markers
    delta = delta.replace(/\*/g, "")    // Remove asterisks
    delta = delta.replace(/__/g, "")    // Remove underline markers
    delta = delta.replace(/`/g, "")     // Remove backticks
    state.responseText += delta
    els.responseText.textContent = state.responseText || "-"
    setBadge(els.llmState, "live", "generating")
    els.ttsMeta.textContent = "Generating response..."
    state.sessionState.turnPhase = "processing"
    // Track generation_id if provided
    if (event.generation_id) {
      state.activeGenerationId = event.generation_id
    }
    syncUiFromSessionState()
    return
  }
  if (event.type === "llm.tool.update") {
    if (event.turn_id) {
      resetAssistantPanels(event.turn_id)
    }
    // FIX: Deduplicate tool updates by tracking call_id + status
    const toolKey = `${event.call_id || 'unknown'}-${event.status || 'update'}`
    if (!state.seenToolCalls) {
      state.seenToolCalls = new Set()
    }
    // Only add if we haven't seen this exact tool call status before
    if (!state.seenToolCalls.has(toolKey)) {
      state.seenToolCalls.add(toolKey)
      appendToolLine(formatToolUpdate(event))
    }
    announceToolUpdate(event)
    setBadge(els.llmState, "busy", "tool")
    return
  }
  if (event.type === "llm.usage") {
    appendToolLine(formatUsage(event))
    return
  }
  if (event.type === "llm.summary") {
    const provider = event.provider || "-"
    const model = event.model || "-"
    appendToolLine(`[summary] ${provider}/${model}`)
    const systemStack = event.metadata?.opencode_system_stack
    if (Array.isArray(systemStack) && systemStack.length > 0) {
      appendToolLine(`[summary] system stack (${systemStack.length}): ${systemStack.join(" | ")}`)
      console.log("[OPENCODE] summary system stack", systemStack)
    } else {
      console.log("[OPENCODE] summary system stack unavailable", event.metadata || null)
    }
    return
  }
  if (event.type === "llm.completed") {
    if (event.turn_id) {
      resetAssistantPanels(event.turn_id)
    }
    const finalText = (event.text && event.text.trim()) || state.responseText.trim()
    if (finalText) {
      state.responseText = finalText
      els.responseText.textContent = finalText
      appendTranscript("assistant", finalText)
    }
    stopToolAnnouncement()
    setBadge(els.llmState, "live", "complete")
    return
  }
  if (event.type === "tts.chunk" && event.chunk?.data_base64) {
    // CRITICAL FIX: Validate generation_id to reject in-flight chunks from interrupted generation
    const chunkGenerationId = event.generation_id
    // Reject if this generation was previously interrupted
    if (chunkGenerationId && state.rejectedGenerationIds.has(chunkGenerationId)) {
      console.log(`[INTERRUPT] Rejecting TTS chunk from rejected generation ${chunkGenerationId}`)
      return
    }
    // Reject if this chunk is from a different generation than currently active
    if (chunkGenerationId && state.activeGenerationId && chunkGenerationId !== state.activeGenerationId) {
      console.log(`[INTERRUPT] Rejecting TTS chunk from old generation ${chunkGenerationId}, current: ${state.activeGenerationId}`)
      return
    }

    if (!state.activeGenerationId && chunkGenerationId) {
      state.activeGenerationId = chunkGenerationId
    }

    if (!state.activeGenerationId && !chunkGenerationId) {
      console.log(`[INTERRUPT] Rejecting TTS chunk - no active generation (audio stopped by stt.final)`)
      return
    }
    stopToolAnnouncement()
    // Stop thinking audio when TTS starts (assistant is now speaking)
    state.thinkingPlayer?.stop()
    setBadge(els.ttsState, "busy", "playing")
    els.ttsMeta.textContent = "Streaming audio..."
    state.sessionState.turnPhase = "agent_speaking"
    syncUiFromSessionState()
    const bytes = base64ToBytes(event.chunk.data_base64)
    await state.pcmPlayer?.appendPcm16(bytes, event.chunk.sample_rate_hz || 24000)
    return
  }
  if (event.type === "tts.completed") {
    setBadge(els.ttsState, "live", "complete")
    els.ttsMeta.textContent = `${(event.duration_ms || 0).toFixed(0)} ms generated`
    state.sessionState.turnPhase = state.sessionState.sessionStatus === "listening" ? "listening" : "idle"
    syncUiFromSessionState()
    return
  }
  if (event.type === "conversation.interrupted") {
    state.sessionState.turnPhase = "idle"
    state.sessionState.queuePending = 0
    setBadge(els.turnState, "idle", "interrupted")
    // CRITICAL FIX: Stop audio playback immediately on interrupt
    // This is a backup for cases where stt.final didn't handle it
    await state.pcmPlayer?.close().catch(() => {})
    state.pcmPlayer = new StreamingPcmPlayer()
    // Stop thinking audio on interrupt
    state.thinkingPlayer?.stop()
    stopToolAnnouncement()
    // Clear any pending audio chunks
    els.ttsMeta.textContent = "Interrupted - audio cleared"
    // CRITICAL FIX: Add current generation to rejected set
    // Note: Don't clear activeGenerationId here - let stt.final handle that
    // This prevents race where TTS chunks arrive between interrupt and next stt.final
    if (state.activeGenerationId) {
      state.rejectedGenerationIds.add(state.activeGenerationId)
      console.log(`[INTERRUPT] Added generation ${state.activeGenerationId} to rejected set`)
    }
    // Cleanup old rejected IDs to prevent memory leak (keep last 10)
    if (state.rejectedGenerationIds.size > 10) {
      const iterator = state.rejectedGenerationIds.values()
      state.rejectedGenerationIds.delete(iterator.next().value)
    }
    renderQueueAndMetrics()
    return
  }
  if (event.type === "error") {
    setBadge(els.sttState, "error", "error")
    setBadge(els.routerState, "error", "error")
    setBadge(els.llmState, "error", "error")
    setBadge(els.ttsState, "error", "error")
    els.sessionStatus.textContent = `error: ${event.message}`
  }
}

els.connectBtn.addEventListener("click", () => connect().catch((error) => {
  els.sessionStatus.textContent = `connect failed: ${error.message}`
}))
els.disconnectBtn.addEventListener("click", () => void disconnect())
els.listenBtn.addEventListener("click", () => {
  void startListening().catch((error) => {
    const msg = error?.message || String(error)
    els.sessionStatus.textContent = `listen failed: ${msg}`
    setBadge(els.micState, "error", "mic error")
    setButtons(true, false)
  })
})
els.stopBtn.addEventListener("click", () => void stopListening())
els.interruptBtn.addEventListener("click", () => {
  if (!state.sessionId) return
  sendJson({ type: "conversation.interrupt", session_id: state.sessionId, reason: "demo" })
})

els.tabDetailed?.addEventListener("click", () => activateTab("detailed"))
els.tabMinimal?.addEventListener("click", () => activateTab("minimal"))

els.miniConnectBtn?.addEventListener("click", () => els.connectBtn.click())
els.miniDisconnectBtn?.addEventListener("click", () => els.disconnectBtn.click())
els.miniListenBtn?.addEventListener("click", () => els.listenBtn.click())
els.miniStopBtn?.addEventListener("click", () => els.stopBtn.click())
els.miniInterruptBtn?.addEventListener("click", () => els.interruptBtn.click())

setButtons(false)
setBadge(els.turnState, "idle", "idle")
setBadge(els.sttState, "idle", "waiting")
setBadge(els.routerState, "idle", "waiting")
setBadge(els.llmState, "idle", "waiting")
setBadge(els.ttsState, "idle", "waiting")
activateTab("detailed")
updateMinimalUi()
