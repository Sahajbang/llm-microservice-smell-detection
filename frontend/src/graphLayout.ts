import type { Cycle, Edge } from './types'

export interface LayoutNode {
  id: string
  tier: 'service' | 'infra'
  x: number
  y: number
  width: number
  height: number
}

export interface LayoutEdge {
  caller: string
  callee: string
  sources: string[]
  evidence: Edge[]
  d: string
  mid: { x: number; y: number }
  inCycle: boolean
  dockerOnly: boolean
}

export const CANVAS_WIDTH = 1000
const SERVICE_Y = 120
const INFRA_Y = 420
const NODE_W_SERVICE = 156
const NODE_H_SERVICE = 44
const NODE_W_INFRA = 130
const NODE_H_INFRA = 34
const PAD_X = 100
export const CANVAS_HEIGHT = INFRA_Y + 90

/** A service is "infra" if every edge touching it is docker-compose (startup-order only). */
export function classifyNodes(
  services: string[],
  edges: Edge[],
): { service: string[]; infra: string[] } {
  const nonDockerTouch = new Set<string>()
  const anyTouch = new Set<string>()
  for (const e of edges) {
    anyTouch.add(e.caller)
    anyTouch.add(e.callee)
    if (e.source !== 'docker-compose') {
      nonDockerTouch.add(e.caller)
      nonDockerTouch.add(e.callee)
    }
  }
  const service: string[] = []
  const infra: string[] = []
  for (const s of services) {
    if (nonDockerTouch.has(s) || !anyTouch.has(s)) service.push(s)
    else infra.push(s)
  }
  return { service, infra }
}

function rowPositions(
  names: string[],
  tier: 'service' | 'infra',
  y: number,
  w: number,
  h: number,
): Record<string, LayoutNode> {
  const nodes: Record<string, LayoutNode> = {}
  const usable = CANVAS_WIDTH - PAD_X * 2
  names.forEach((name, i) => {
    const x = names.length === 1 ? CANVAS_WIDTH / 2 : PAD_X + (usable * i) / (names.length - 1)
    nodes[name] = { id: name, tier, x, y, width: w, height: h }
  })
  return nodes
}

function borderPoint(node: LayoutNode, ux: number, uy: number) {
  const hw = node.width / 2
  const hh = node.height / 2
  const scale = Math.min(ux !== 0 ? hw / Math.abs(ux) : Infinity, uy !== 0 ? hh / Math.abs(uy) : Infinity)
  return { x: node.x + ux * scale, y: node.y + uy * scale }
}

function curvedPath(from: LayoutNode, to: LayoutNode, offset: number) {
  const dx = to.x - from.x
  const dy = to.y - from.y
  const dist = Math.max(Math.hypot(dx, dy), 1)
  const ux = dx / dist
  const uy = dy / dist
  const start = borderPoint(from, ux, uy)
  const end = borderPoint(to, -ux, -uy)
  const px = -uy
  const py = ux
  const mx = (start.x + end.x) / 2 + px * offset
  const my = (start.y + end.y) / 2 + py * offset
  return { d: `M ${start.x} ${start.y} Q ${mx} ${my} ${end.x} ${end.y}`, mid: { x: mx, y: my } }
}

export interface Layout {
  nodes: Record<string, LayoutNode>
  edges: LayoutEdge[]
  width: number
  height: number
}

export function layoutGraph(services: string[], edges: Edge[], cycles: Cycle[]): Layout {
  const { service, infra } = classifyNodes(services, edges)
  const nodes = {
    ...rowPositions(service, 'service', SERVICE_Y, NODE_W_SERVICE, NODE_H_SERVICE),
    ...rowPositions(infra, 'infra', INFRA_Y, NODE_W_INFRA, NODE_H_INFRA),
  }

  const cyclePairs = new Set<string>()
  for (const c of cycles) {
    for (let i = 0; i < c.cycle.length - 1; i++) {
      cyclePairs.add(`${c.cycle[i]}->${c.cycle[i + 1]}`)
    }
  }

  const groups = new Map<string, Edge[]>()
  for (const e of edges) {
    const key = `${e.caller}->${e.callee}`
    const list = groups.get(key)
    if (list) list.push(e)
    else groups.set(key, [e])
  }

  const layoutEdges: LayoutEdge[] = []
  for (const [key, group] of groups) {
    const [caller, callee] = key.split('->')
    const from = nodes[caller]
    const to = nodes[callee]
    if (!from || !to) continue
    const reverseExists = groups.has(`${callee}->${caller}`)
    // A constant offset is enough to separate a reverse pair: the
    // perpendicular direction itself flips sign between A->B and B->A, so
    // curving both by the same offset naturally bows them to opposite sides.
    const offset = reverseExists ? 26 : 0
    const { d, mid } = curvedPath(from, to, offset)
    const sources = Array.from(new Set(group.map((e) => e.source)))
    layoutEdges.push({
      caller,
      callee,
      sources,
      evidence: group,
      d,
      mid,
      inCycle: cyclePairs.has(key),
      dockerOnly: sources.every((s) => s === 'docker-compose'),
    })
  }

  return { nodes, edges: layoutEdges, width: CANVAS_WIDTH, height: CANVAS_HEIGHT }
}
