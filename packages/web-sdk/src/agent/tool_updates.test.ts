import { describe, expect, test } from "bun:test"

import { buildToolUpdatePresentation, normalizeToolName } from "./tool_updates"
import type { LlmToolUpdateEvent } from "../protocol"

describe("tool update presentation", () => {
  test("maps websearch running to visible and spoken hints", () => {
    const result = buildToolUpdatePresentation(createToolEvent("websearch", "running"))
    expect(result.summary).toBe("Searching the web…")
    expect(result.spokenHint).toBe("Searching the web.")
  })

  test("maps completed websearch to a non-spoken completion summary", () => {
    const result = buildToolUpdatePresentation(createToolEvent("websearch", "completed"))
    expect(result.summary).toBe("Web search completed.")
    expect(result.spokenHint).toBeNull()
  })

  test("normalizes underscored tool names", () => {
    expect(normalizeToolName("web_fetch")).toBe("web fetch")
    expect(normalizeToolName("custom-tool_name")).toBe("custom tool name")
  })
})

function createToolEvent(tool_name: string, status: string): LlmToolUpdateEvent {
  return {
    type: "llm.tool.update",
    session_id: "sess-1",
    turn_id: "turn-1",
    generation_id: "gen-1",
    event_id: "evt-1",
    timestamp: new Date().toISOString(),
    tool_name,
    status,
    is_mcp: false,
  }
}
