import { OpenVoiceWebClient, type ConversationEvent } from "../src/index.ts"

const baseUrl = process.env.OPEN_VOICE_BASE_URL ?? "http://127.0.0.1:8011"

async function main(): Promise<void> {
  const client = new OpenVoiceWebClient({ baseUrl, fetch, webSocket: WebSocket })
  const events: ConversationEvent[] = []

  console.log("health", await client.http.health())
  const engines = await client.http.listEngines()
  console.log("engines", engines)

  const session = await client.connectSession({
    metadata: { source: "web-sdk-smoke" },
    onEvent: (event) => {
      events.push(event)
      console.log("event", event.type)
    },
  })

  await waitFor(() => events.some((event) => event.type === "session.ready"), 2000)

  const realStt = engines.stt.some((engine) => engine.default && engine.available)
  if (realStt) {
    console.log("real-stt", "ready; skipping synthetic audio assertion")
  } else {
    session.sendAudio({
      data: new Int16Array([1000, -1000, 1000, -1000, 1000, -1000]).buffer,
      sequence: 0,
      encoding: "pcm_s16le",
      sampleRateHz: 16000,
      channels: 1,
      durationMs: 20,
    })
    session.commit()

    await waitFor(() => events.some((event) => event.type === "stt.final"), 2000)
  }

  console.log("session", await client.http.getSession(session.sessionId))
  console.log("received", events.map((event) => event.type))

  await client.http.closeSession(session.sessionId)
  session.socket.close()
}

async function waitFor(check: () => boolean, timeoutMs: number): Promise<void> {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (check()) return
    await new Promise((resolve) => setTimeout(resolve, 25))
  }
  throw new Error(`Timed out after ${timeoutMs}ms`)
}

await main()
