import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CornersOut, Minus, Plus } from '@phosphor-icons/react'
import type { GraphResponse } from '../types'
import { layoutGraph, type LayoutEdge } from '../graphLayout'
import { cn } from '../lib/cn'

const COLOR_ACCENT = '#4fd1ff'
const COLOR_DANGER = '#f43f5e'
const COLOR_MUTED = '#44444c'

type View = { x: number; y: number; w: number; h: number }

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

  const svgRef = useRef<SVGSVGElement>(null)
  const fit = useMemo<View>(() => ({ x: 0, y: 0, w: layout.width, h: layout.height }), [layout])
  const [view, setView] = useState<View>(fit)
  const [prevFit, setPrevFit] = useState(fit)
  if (prevFit !== fit) {
    setPrevFit(fit)
    setView(fit)
  }
  const dragged = useRef(false)
  const [panning, setPanning] = useState(false)

  const zoomAt = useCallback(
    (clientX: number, clientY: number, factor: number) => {
      const ctm = svgRef.current?.getScreenCTM()
      if (!ctm) return
      const p = new DOMPoint(clientX, clientY).matrixTransform(ctm.inverse())
      // Zoom in until the smallest node roughly fills the view; zoom out to a few times the fit.
      const minW = Math.min(...Object.values(layout.nodes).map((n) => n.width)) * 1.5
      const maxW = layout.width * 4
      setView((v) => {
        const k = Math.min(Math.max(factor, minW / v.w), maxW / v.w)
        return { x: p.x - (p.x - v.x) * k, y: p.y - (p.y - v.y) * k, w: v.w * k, h: v.h * k }
      })
    },
    [layout],
  )

  const zoomCenter = (factor: number) => {
    const r = svgRef.current?.getBoundingClientRect()
    if (r) zoomAt(r.left + r.width / 2, r.top + r.height / 2, factor)
  }

  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      zoomAt(e.clientX, e.clientY, Math.exp(e.deltaY * 0.0015))
    }
    svg.addEventListener('wheel', onWheel, { passive: false })
    return () => svg.removeEventListener('wheel', onWheel)
  }, [zoomAt])

  const startPan = (e: React.PointerEvent<SVGSVGElement>) => {
    const ctm = svgRef.current?.getScreenCTM()
    if (e.button !== 0 || !ctm) return
    const start = { cx: e.clientX, cy: e.clientY, v: view, scale: 1 / ctm.a }
    dragged.current = false
    const move = (m: PointerEvent) => {
      const dx = m.clientX - start.cx
      const dy = m.clientY - start.cy
      if (!dragged.current && Math.hypot(dx, dy) < 4) return
      dragged.current = true
      setPanning(true)
      setView({ ...start.v, x: start.v.x - dx * start.scale, y: start.v.y - dy * start.scale })
    }
    const up = () => {
      setPanning(false)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

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
    <div className="relative min-w-0 overflow-hidden rounded-lg border border-border bg-bg-elevated">
      <div className="absolute right-3 top-3 z-10 flex gap-1">
        <ZoomButton label="Zoom in" onClick={() => zoomCenter(1 / 1.3)}>
          <Plus size={14} />
        </ZoomButton>
        <ZoomButton label="Zoom out" onClick={() => zoomCenter(1.3)}>
          <Minus size={14} />
        </ZoomButton>
        <ZoomButton label="Fit to view" onClick={() => setView(fit)}>
          <CornersOut size={14} />
        </ZoomButton>
      </div>
      <svg
        ref={svgRef}
        viewBox={`${view.x} ${view.y} ${view.w} ${view.h}`}
        className={cn('block w-full touch-none select-none', panning ? 'cursor-grabbing' : 'cursor-grab')}
        style={{ aspectRatio: `${layout.width} / ${layout.height}`, maxHeight: '75vh', minHeight: 280 }}
        role="img"
        aria-label="Service dependency graph"
        onPointerDown={startPan}
        onClickCapture={(e) => {
          if (dragged.current) {
            e.stopPropagation()
            dragged.current = false
          }
        }}
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
                  fontSize={n.fontSize}
                  className={cn('select-none font-mono', isService ? 'fill-text' : 'fill-text-tertiary')}
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

function ZoomButton({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="grid size-7 place-items-center rounded-md border border-border bg-bg-elevated/90 text-text-tertiary transition-colors hover:text-text"
    >
      {children}
    </button>
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
