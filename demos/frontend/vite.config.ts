import { defineConfig } from "vite"
import { fileURLToPath } from "node:url"

const demoRoot = fileURLToPath(new URL(".", import.meta.url))
const runtimeProxyTarget = process.env.OPEN_VOICE_DEMO_RUNTIME_URL ?? "http://127.0.0.1:8011"

export default defineConfig({
  root: demoRoot,
  server: {
    host: true,
    allowedHosts: [
      ".ngrok-free.app",
      ".ts.net",
      "open-voice.sahilchouksey.in",
      "open-voice-backend.sahilchouksey.in",
      ".sahilchouksey.in",
    ],
    proxy: {
      "/v1": {
        target: runtimeProxyTarget,
        changeOrigin: true,
        ws: true,
      },
      "/health": {
        target: runtimeProxyTarget,
        changeOrigin: true,
      },
    },
    port: 4173,
  },
  preview: {
    host: true,
    port: 4173,
  },
})
