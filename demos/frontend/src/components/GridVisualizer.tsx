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
  speakingRole?: "agent" | "user"
  rowCount?: number
  columnCount?: number
  radius?: number
  interval?: number
}

const SPEAKING_WAVE_SPEED = 0.28

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

function generateCenterOutColumnOrder(columnCount: number): number[] {
  const centerLeft = Math.floor((columnCount - 1) / 2)
  const centerRight = Math.ceil((columnCount - 1) / 2)
  const order: number[] = []

  for (let offset = 0; order.length < columnCount; offset += 1) {
    const left = centerLeft - offset
    const right = centerRight + offset
    if (left >= 0) {
      order.push(left)
    }
    if (right < columnCount && right !== left) {
      order.push(right)
    }
  }

  return order
}

function smoothColumnEnergy(values: number[]): number[] {
  return values.map((value, index, source) => {
    const left = source[Math.max(0, index - 1)] ?? value
    const right = source[Math.min(source.length - 1, index + 1)] ?? value
    return Math.max(0, Math.min(1, value * 0.6 + left * 0.2 + right * 0.2))
  })
}

function buildCenteredColumnEnergy(
  bands: number[] | undefined,
  columnCount: number,
  normalizedLevel: number,
): number[] {
  const order = generateCenterOutColumnOrder(columnCount)
  const seed = Array.from({ length: columnCount }, () => Math.max(0.04, normalizedLevel * 0.12))

  if (bands && bands.length > 0) {
    for (let step = 0; step < order.length; step += 1) {
      const column = order[step]!
      const sourceIndex = Math.min(
        bands.length - 1,
        Math.floor((step / Math.max(1, order.length - 1)) * (bands.length - 1)),
      )
      const sample = Math.max(0, Math.min(1, bands[sourceIndex] ?? 0))
      seed[column] = Math.max(seed[column] ?? 0, sample)

      const left = column - 1
      const right = column + 1
      if (left >= 0) {
        seed[left] = Math.max(seed[left] ?? 0, sample * 0.72)
      }
      if (right < columnCount) {
        seed[right] = Math.max(seed[right] ?? 0, sample * 0.72)
      }
    }

    return smoothColumnEnergy(smoothColumnEnergy(seed))
  }

  const center = (columnCount - 1) / 2
  const fallback = Array.from({ length: columnCount }, (_, index) => {
    const distance = Math.abs(index - center) / Math.max(1, center)
    const resonance = Math.max(0.18, 1 - distance * 0.82)
    return Math.max(0, Math.min(1, normalizedLevel * resonance))
  })

  return smoothColumnEnergy(fallback)
}

export function GridVisualizer({
  state,
  level,
  bands,
  speakingRole = "agent",
  rowCount = 7,
  columnCount = 7,
  radius,
  interval = 100,
}: GridVisualizerProps) {
  const gridState = resolveGridState(state)
  const total = rowCount * columnCount

  const [index, setIndex] = useState(0)
  const [waveTick, setWaveTick] = useState(0)
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

  useEffect(() => {
    if (gridState !== "speaking" || speakingRole !== "agent") {
      setWaveTick(0)
      return
    }

    const timer = window.setInterval(() => {
      setWaveTick((prev) => prev + 1)
    }, Math.max(40, Math.floor(interval * 0.55)))

    return () => window.clearInterval(timer)
  }, [gridState, interval, speakingRole])

  const highlightedCoordinate = sequence[index % sequence.length] ?? {
    x: Math.floor(columnCount / 2),
    y: Math.floor(rowCount / 2),
  }

  const normalizedLevel = Math.max(0, Math.min(1, level))
  const volumeBands = useMemo(
    () => buildCenteredColumnEnergy(bands, columnCount, normalizedLevel),
    [bands, columnCount, normalizedLevel],
  )

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
        const col = i % columnCount
        const rowMidPoint = Math.floor(rowCount / 2)
        const colMidPoint = (columnCount - 1) / 2
        const columnDistance = Math.abs(col - colMidPoint) / Math.max(1, colMidPoint)
        const rowDistance = Math.abs(rowMidPoint - row) / Math.max(1, rowMidPoint)

        if (gridState === "speaking" && speakingRole === "user") {
          const columnEnergy = volumeBands[col] ?? 0
          const resonance = Math.max(0.22, 1 - columnDistance * 0.58)
          const energy = Math.max(0, Math.min(1, columnEnergy * (0.88 + resonance * 0.34)))
          const threshold = Math.max(0.08, rowDistance * 0.72 + columnDistance * 0.2)
          isHighlighted = energy >= threshold
        } else if (gridState === "speaking") {
          const bandLevel = volumeBands[col] ?? 0

          const wavePhase = waveTick * SPEAKING_WAVE_SPEED - col * 0.65 - row * 0.4
          const waveBoost = ((Math.sin(wavePhase) + 1) / 2) * 0.16
          const resonance = Math.max(0.2, 1 - columnDistance * 0.62)
          const energy = Math.max(
            0,
            Math.min(1, bandLevel * (0.9 + resonance * 0.36) + waveBoost),
          )

          const threshold = Math.max(0.08, rowDistance * 0.7 + columnDistance * 0.18)
          isHighlighted = energy >= threshold
          transitionDuration = Math.max(0.09, interval / 1200)
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
            data-grid-state={gridState}
            data-grid-speaker={gridState === "speaking" ? speakingRole : "none"}
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
