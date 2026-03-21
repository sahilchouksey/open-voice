import type { CSSProperties } from "react"
import { useEffect, useMemo, useState } from "react"

export type MinimalVisualizerState =
  | "idle"
  | "ready"
  | "listening"
  | "thinking"
  | "speaking"

interface GridVisualizerProps {
  state: MinimalVisualizerState
  level: number
  bands?: number[]
  rowCount?: number
  columnCount?: number
  radius?: number
  interval?: number
}

interface Coordinate {
  x: number
  y: number
}

type GridState = "initializing" | "listening" | "thinking" | "speaking"

function resolveGridState(state: MinimalVisualizerState): GridState {
  if (state === "speaking") return "speaking"
  if (state === "thinking") return "thinking"
  if (state === "listening" || state === "ready") return "listening"
  return "initializing"
}

function generateConnectingSequence(rows: number, columns: number, radius: number): Coordinate[] {
  const seq: Coordinate[] = []
  const centerY = Math.floor(rows / 2)

  const topLeft = {
    x: Math.max(0, centerY - radius),
    y: Math.max(0, centerY - radius),
  }
  const bottomRight = {
    x: columns - 1 - topLeft.x,
    y: Math.min(rows - 1, centerY + radius),
  }

  for (let x = topLeft.x; x <= bottomRight.x; x += 1) {
    seq.push({ x, y: topLeft.y })
  }
  for (let y = topLeft.y + 1; y <= bottomRight.y; y += 1) {
    seq.push({ x: bottomRight.x, y })
  }
  for (let x = bottomRight.x - 1; x >= topLeft.x; x -= 1) {
    seq.push({ x, y: bottomRight.y })
  }
  for (let y = bottomRight.y - 1; y > topLeft.y; y -= 1) {
    seq.push({ x: topLeft.x, y })
  }

  return seq
}

function generateListeningSequence(rows: number, columns: number): Coordinate[] {
  const center = { x: Math.floor(columns / 2), y: Math.floor(rows / 2) }
  const noIndex = { x: -1, y: -1 }
  return [center, noIndex, noIndex, noIndex, noIndex, noIndex, noIndex, noIndex, noIndex]
}

function generateThinkingSequence(rows: number, columns: number): Coordinate[] {
  const seq: Coordinate[] = []
  const y = Math.floor(rows / 2)
  for (let x = 0; x < columns; x += 1) {
    seq.push({ x, y })
  }
  for (let x = columns - 1; x >= 0; x -= 1) {
    seq.push({ x, y })
  }
  return seq
}

export function GridVisualizer({
  state,
  level,
  bands,
  rowCount = 7,
  columnCount = 7,
  radius,
  interval = 100,
}: GridVisualizerProps) {
  const gridState = resolveGridState(state)
  const total = rowCount * columnCount

  const [index, setIndex] = useState(0)
  const [sequence, setSequence] = useState<Coordinate[]>(() => [
    { x: Math.floor(columnCount / 2), y: Math.floor(rowCount / 2) },
  ])

  useEffect(() => {
    const maxRadius = Math.floor(Math.max(rowCount, columnCount) / 2)
    const clampedRadius = radius ? Math.min(radius, maxRadius) : maxRadius

    if (gridState === "thinking") {
      setSequence(generateThinkingSequence(rowCount, columnCount))
    } else if (gridState === "initializing") {
      setSequence(generateConnectingSequence(rowCount, columnCount, clampedRadius))
    } else if (gridState === "listening") {
      setSequence(generateListeningSequence(rowCount, columnCount))
    } else {
      setSequence([{ x: Math.floor(columnCount / 2), y: Math.floor(rowCount / 2) }])
    }

    setIndex(0)
  }, [columnCount, gridState, radius, rowCount])

  useEffect(() => {
    if (gridState === "speaking") {
      return
    }

    const timer = window.setInterval(() => {
      setIndex((prev) => prev + 1)
    }, interval)

    return () => window.clearInterval(timer)
  }, [gridState, interval])

  const highlightedCoordinate = sequence[index % sequence.length] ?? {
    x: Math.floor(columnCount / 2),
    y: Math.floor(rowCount / 2),
  }

  const normalizedLevel = Math.max(0, Math.min(1, level))
  const volumeBands = useMemo(() => {
    if (bands && bands.length > 0) {
      return Array.from({ length: columnCount }, (_, i) => {
        const value = bands[i % bands.length] ?? 0
        return Math.max(0, Math.min(1, value))
      })
    }

    return Array.from({ length: columnCount }, (_, i) => {
      const center = (columnCount - 1) / 2
      const distance = Math.abs(i - center) / Math.max(1, center)
      const shape = Math.max(0.5, 1 - distance * 0.45)
      return Math.max(0, Math.min(1, normalizedLevel * shape))
    })
  }, [bands, columnCount, normalizedLevel])

  return (
    <div
      className="grid-viz"
      style={
        {
          "--level": String(level),
          gridTemplateColumns: `repeat(${columnCount}, 1fr)`,
        } as CSSProperties
      }
      aria-hidden="true"
    >
      {Array.from({ length: total }).map((_, i) => {
        const row = Math.floor(i / columnCount)
        let isHighlighted = false
        let transitionDuration = interval / 100

        if (gridState === "speaking") {
          const rowMidPoint = Math.floor(rowCount / 2)
          const volumeChunks = 1 / (rowMidPoint + 1)
          const distanceToMid = Math.abs(rowMidPoint - row)
          const threshold = distanceToMid * volumeChunks
          isHighlighted = (volumeBands[i % columnCount] ?? 0) >= threshold
        } else {
          isHighlighted =
            highlightedCoordinate.x === i % columnCount &&
            highlightedCoordinate.y === Math.floor(i / columnCount)
          transitionDuration = interval / (isHighlighted ? 1000 : 100)
        }

        return (
          <span
            key={i}
            className="grid-dot"
            data-highlighted={isHighlighted ? "true" : "false"}
            style={
              {
                transitionDuration: `${transitionDuration}s`,
              } as CSSProperties
            }
          />
        )
      })}
    </div>
  )
}
