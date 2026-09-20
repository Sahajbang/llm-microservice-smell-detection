import { useMemo, useState } from 'react'
import type { GraphResponse } from '../types'
import { layoutGraph, type LayoutEdge } from '../graphLayout'
import { cn } from '../lib/cn'

const COLOR_ACCENT = '#4fd1ff'
const COLOR_DANGER = '#f43f5e'
const COLOR_MUTED = '#44444c'

function edgeColor(e: LayoutEdge) {
  if (e.inCycle) return COLOR_DANGER
  if (e.dockerOnly) return COLOR_MUTED
  return COLOR_ACCENT
}

export function ServiceGraph({
  graph,
  selected,
  onSelect,
}: {
  graph: GraphResponse
  selected: string | null
  onSelect: (node: string | null) => void
}) {
  const layout = useMemo(() => layoutGraph(graph.services, graph.edges, graph.cycles), [graph])
  const [hovered, setHovered] = useState<string | null>(null)
  const active = hovered ?? selected

  const cycleNodes = useMemo(() => {
    const set = new Set<string>()
    for (const c of graph.cycles) c.cycle.forEach((n) => set.add(n))
    return set
  }, [graph.cycles])

  const connected = useMemo(() => {
    if (!active) return null
    const set = new Set([active])
    for (const e of layout.edges) {
      if (e.caller === active) set.add(e.callee)
      if (e.callee === active) set.add(e.caller)
    }
    return set
  }, [active, layout])

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-bg-elevated">
      <svg
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        className="h-auto w-full"
        role="img"
        aria-label="Service dependency graph"
        onClick={() => onSelect(null)}
      >
        <defs>
          {(
            [
              ['arrow-accent', COLOR_ACCENT],
              ['arrow-danger', COLOR_DANGER],
              ['arrow-muted', COLOR_MUTED],
            ] as const
          ).map(([id, color]) => (
            <marker key={id} id={id} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M0,0 L10,5 L0,10 z" fill={color} />
            </marker>
          ))}
        </defs>

        <g>
          {layout.edges.map((e) => {
            const isActive = !active || e.caller === active || e.callee === active
            const markerId = e.inCycle ? 'arrow-danger' : e.dockerOnly ? 'arrow-muted' : 'arrow-accent'
            return (
              <path
                key={`${e.caller}->${e.callee}`}
                d={e.d}
                fill="none"
                stroke={edgeColor(e)}
                strokeWidth={e.inCycle ? 2.5 : e.dockerOnly ? 1.25 : 1.75}
                strokeDasharray={e.dockerOnly ? '4 4' : undefined}
                markerEnd={`url(#${markerId})`}
                className={cn('transition-opacity duration-150', e.inCycle && 'animate-pulse')}
                style={{ opacity: isActive ? (e.dockerOnly ? 0.6 : 1) : 0.12 }}
              />
            )
          })}
        </g>

        <g>
          {Object.values(layout.nodes).map((n) => {
            const isService = n.tier === 'service'
            const isSelected = selected === n.id
            const inCycle = cycleNodes.has(n.id)
            const dimmed = connected !== null && !connected.has(n.id)
            return (
              <g
                key={n.id}
                transform={`translate(${n.x - n.width / 2}, ${n.y - n.height / 2})`}
                className="cursor-pointer transition-opacity duration-150"
                style={{ opacity: dimmed ? 0.35 : 1 }}
                onClick={(evt) => {
                  evt.stopPropagation()
                  onSelect(selected === n.id ? null : n.id)
                }}
                onMouseEnter={() => setHovered(n.id)}
                onMouseLeave={() => setHovered(null)}
              >
                <rect
                  width={n.width}
                  height={n.height}
                  rx={8}
                  fill={isService ? '#1c1c20' : '#141417'}
                  stroke={isSelected ? COLOR_ACCENT : inCycle ? COLOR_DANGER : '#29292f'}
                  strokeWidth={isSelected ? 2 : 1.25}
                />
                <text
                  x={n.width / 2}
                  y={n.height / 2}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  className={cn(
                    'select-none font-mono',
                    isService ? 'fill-text text-[13px]' : 'fill-text-tertiary text-[11px]',
                  )}
                >
                  {n.id}
                </text>
              </g>
            )
          })}
        </g>
      </svg>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-border px-4 py-3 font-mono text-[11px] text-text-tertiary">
        <LegendLine color={COLOR_ACCENT} label="service call" />
        <LegendLine color={COLOR_DANGER} label="part of detected cycle" />
        <LegendLine color={COLOR_MUTED} dashed label="docker-compose startup order" />
      </div>
    </div>
  )
}

function LegendLine({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <svg width="20" height="8" aria-hidden="true">
        <line
          x1="0"
          y1="4"
          x2="20"
          y2="4"
          stroke={color}
          strokeWidth="2"
          strokeDasharray={dashed ? '3 3' : undefined}
        />
      </svg>
      {label}
    </span>
  )
}
