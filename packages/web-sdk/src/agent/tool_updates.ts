import type { LlmToolUpdateEvent } from "../protocol"

export interface ToolUpdatePresentation {
  summary: string | null
  spokenHint: string | null
}

export function buildToolUpdatePresentation(event: LlmToolUpdateEvent): ToolUpdatePresentation {
  const toolName = normalizeToolName(event.tool_name)
  const status = typeof event.status === "string" ? event.status.toLowerCase() : null

  if (toolName === "web search") {
    if (status === "pending") {
      return { summary: "Preparing web search…", spokenHint: null }
    }
    if (status === "running") {
      return { summary: "Searching the web…", spokenHint: "Searching the web." }
    }
    if (status === "completed") {
      return { summary: "Web search completed.", spokenHint: null }
    }
    if (status === "failed") {
      return { summary: "Web search failed.", spokenHint: "The web search failed." }
    }
  }

  if (toolName === "web fetch") {
    if (status === "pending") {
      return { summary: "Preparing page fetch…", spokenHint: null }
    }
    if (status === "running") {
      return { summary: "Reading the page…", spokenHint: "Reading the page." }
    }
    if (status === "completed") {
      return { summary: "Page fetch completed.", spokenHint: null }
    }
    if (status === "failed") {
      return { summary: "Page fetch failed.", spokenHint: "The page fetch failed." }
    }
  }

  const title = capitalize(toolName)
  if (status === "pending") {
    return { summary: `${title} pending…`, spokenHint: null }
  }
  if (status === "running") {
    return { summary: `${title} running…`, spokenHint: `Using ${toolName}.` }
  }
  if (status === "completed") {
    return { summary: `${title} completed.`, spokenHint: null }
  }
  if (status === "failed") {
    return { summary: `${title} failed.`, spokenHint: `${title} failed.` }
  }

  return { summary: title, spokenHint: null }
}

export function normalizeToolName(toolName: string): string {
  const normalized = toolName.trim().toLowerCase().replace(/[_-]+/g, " ")
  if (normalized === "websearch") return "web search"
  if (normalized === "webfetch") return "web fetch"
  return normalized || "tool"
}

function capitalize(value: string): string {
  if (!value) return "Tool"
  return value.charAt(0).toUpperCase() + value.slice(1)
}
