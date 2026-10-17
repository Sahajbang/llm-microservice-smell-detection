import type { Cycle, Edge } from './types'

export interface LayoutNode {
  id: string
  tier: 'service' | 'infra'
  x: number
  y: number
  width: number
  height: number
  fontSize: number
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

export interface Layout {
  nodes: Record<string, LayoutNode>
  edges: LayoutEdge[]
  width: number
  height: number
}

const FONT_SIZE = { service: 13, infra: 11 } as const
// ponytail: monospace glyph-width estimate, measure with canvas.measureText if labels ever clip
const MONO_CHAR_EM = 0.62
// Preferred width:height of the whole drawing, so wide layers wrap instead of becoming a thin strip.
const TARGET_ASPECT = 2

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

function makeNode(id: string, tier: 'service' | 'infra'): LayoutNode {
  const fontSize = FONT_SIZE[tier]
  const padX = fontSize * 1.4
  return { id, tier, x: 0, y: 0, width: id.length * fontSize * MONO_CHAR_EM + padX * 2, height: fontSize * 3.3, fontSize }
}

/**
 * Longest-path layering after dropping DFS back edges, so callers sit above callees and a
 * cycle becomes one downward edge plus one upward edge instead of an unrankable loop.
 */
function assignLayers(names: string[], pairs: [string, string][]): Map<string, number> {
  const out = new Map(names.map((n) => [n, [] as string[]]))
  const indeg = new Map(names.map((n) => [n, 0]))
  for (const [a, b] of pairs) {
    out.get(a)!.push(b)
    indeg.set(b, indeg.get(b)! + 1)
  }

  const state = new Map<string, 'open' | 'done'>()
  const postOrder: string[] = []
  const dagOut = new Map(names.map((n) => [n, [] as string[]]))
  const visit = (n: string) => {
    state.set(n, 'open')
    for (const m of out.get(n)!) {
      if (state.get(m) === 'open') continue // back edge: ignore for ranking
      dagOut.get(n)!.push(m)
      if (!state.has(m)) visit(m)
    }
    state.set(n, 'done')
    postOrder.push(n)
  }
  // Roots first so the natural entry points (gateways) end up on top.
  for (const n of [...names].sort((a, b) => indeg.get(a)! - indeg.get(b)!)) if (!state.has(n)) visit(n)

  const layer = new Map(names.map((n) => [n, 0]))
  for (const n of postOrder.reverse()) {
    for (const m of dagOut.get(n)!) layer.set(m, Math.max(layer.get(m)!, layer.get(n)! + 1))
  }
  return layer
}

/** Barycenter sweeps: order each layer by the mean position of its neighbours in the layer above/below. */
function orderLayers(layers: string[][], neighbours: Map<string, Set<string>>) {
  const pos = new Map<string, number>()
  const index = () => layers.forEach((l) => l.forEach((n, i) => pos.set(n, i / Math.max(l.length - 1, 1))))
  index()
  const sweep = (from: number, to: number, step: number) => {
    for (let i = from; i !== to; i += step) {
      const ref = new Set(layers[i - step])
      const bary = new Map<string, number>()
      for (const n of layers[i]) {
        const ps = [...(neighbours.get(n) ?? [])].filter((m) => ref.has(m)).map((m) => pos.get(m)!)
        bary.set(n, ps.length ? ps.reduce((s, p) => s + p, 0) / ps.length : pos.get(n)!)
      }
      layers[i].sort((a, b) => bary.get(a)! - bary.get(b)!)
      index()
    }
  }
  for (let iter = 0; iter < 4; iter++) {
    sweep(1, layers.length, 1)
    sweep(layers.length - 2, -1, -1)
  }
}

export function layoutGraph(services: string[], edges: Edge[], cycles: Cycle[]): Layout {
  const { service, infra } = classifyNodes(services, edges)
  const nodes: Record<string, LayoutNode> = {}
  for (const s of service) nodes[s] = makeNode(s, 'service')
  for (const s of infra) nodes[s] = makeNode(s, 'infra')

  const groups = new Map<string, Edge[]>()
  for (const e of edges) {
    if (!nodes[e.caller] || !nodes[e.callee] || e.caller === e.callee) continue
    const key = `${e.caller}->${e.callee}`
    const list = groups.get(key)
    if (list) list.push(e)
    else groups.set(key, [e])
  }
  const pairs = [...groups.keys()].map((k) => k.split('->') as [string, string])

  const link = (map: Map<string, Set<string>>, a: string, b: string) => {
    if (!map.has(a)) map.set(a, new Set())
    if (!map.has(b)) map.set(b, new Set())
    map.get(a)!.add(b)
    map.get(b)!.add(a)
  }
  const neighbours = new Map<string, Set<string>>()
  for (const [a, b] of pairs) link(neighbours, a, b)

  // Modules with no links go on their own bottom row so they never sit in the way of routed edges.
  const linked = service.filter((s) => neighbours.has(s))
  const unlinked = service.filter((s) => !neighbours.has(s))
  const linkedSet = new Set(linked)
  const layerOf = assignLayers(linked, pairs.filter(([a, b]) => linkedSet.has(a) && linkedSet.has(b)))
  const infraSet = new Set(infra)
  const infraLayerOf = assignLayers(infra, pairs.filter(([a, b]) => infraSet.has(a) && infraSet.has(b)))
  const serviceLayers: string[][] = []
  for (const s of linked) (serviceLayers[layerOf.get(s)!] ??= []).push(s)
  const infraLayers: string[][] = []
  for (const s of infra) (infraLayers[infraLayerOf.get(s)!] ??= []).push(s)
  const ranked = [...serviceLayers, ...infraLayers, unlinked].filter((l) => l?.length)
  orderLayers(ranked, neighbours)

  // All spacing derives from node size and edge density, so it scales with whatever the repo holds.
  const all = Object.values(nodes)
  const unit = Math.max(...all.map((n) => n.height))
  const gapX = unit * 1.2
  const footprint = all.reduce((s, n) => s + (n.width + gapX) * (n.height + unit * 2), 0)
  const maxRowWidth = Math.max(...all.map((n) => n.width), Math.sqrt(footprint * TARGET_ASPECT))

  const rows: string[][] = ranked.flatMap((layer) => {
    const wrapped: string[][] = [[]]
    let w = 0
    for (const n of layer) {
      const nw = nodes[n].width
      if (wrapped.at(-1)!.length && w + gapX + nw > maxRowWidth) {
        wrapped.push([])
        w = 0
      }
      w += (wrapped.at(-1)!.length ? gapX : 0) + nw
      wrapped.at(-1)!.push(n)
    }
    return wrapped
  })

  // An edge skipping rows gets a thin waypoint in every row it crosses, so it is routed
  // through the gaps between nodes rather than drawn behind them.
  const rowOf = new Map<string, number>()
  rows.forEach((row, r) => row.forEach((n) => rowOf.set(n, r)))
  const waypointWidth = unit * 0.3
  const chains = new Map<string, string[]>()
  const chainNeighbours = new Map<string, Set<string>>()
  for (const [a, b] of pairs) {
    const ra = rowOf.get(a)!
    const rb = rowOf.get(b)!
    const step = Math.sign(rb - ra)
    const chain = [a]
    for (let r = ra + step; step && r !== rb; r += step) {
      const id = `waypoint:${a}->${b}#${r}`
      rows[r].push(id)
      rowOf.set(id, r)
      chain.push(id)
    }
    chain.push(b)
    chains.set(`${a}->${b}`, chain)
    for (let i = 0; i < chain.length - 1; i++) link(chainNeighbours, chain[i], chain[i + 1])
  }
  orderLayers(rows, chainNeighbours)

  const widthOf = (id: string) => nodes[id]?.width ?? waypointWidth
  const rowWidth = (row: string[]) => row.reduce((s, n) => s + widthOf(n), 0) + gapX * (row.length - 1)
  const contentWidth = Math.max(...rows.map(rowWidth))
  const margin = unit
  const pos = new Map<string, { x: number; y: number }>()
  const rowHeight = rows.map((row) => Math.max(...row.map((n) => nodes[n]?.height ?? 0)))

  rows.forEach((row) => {
    let x = margin + (contentWidth - rowWidth(row)) / 2
    for (const n of row) {
      pos.set(n, { x: x + widthOf(n) / 2, y: 0 })
      x += widthOf(n) + gapX
    }
  })

  // Orthogonal routing: every chain link runs between two adjacent rows as
  // vertical -> horizontal -> vertical. Each node side spreads its attachments
  // across its width, and each horizontal run gets its own track in the gap,
  // so no two edges share a segment and none passes behind a node.
  type Link = { from: string; to: string; gap: number; down: boolean; xs: number; xe: number; track: number }
  const linksOf = new Map<string, Link[]>()
  const sides = new Map<string, { link: Link; end: 'xs' | 'xe'; otherX: number }[]>()
  for (const [key, chain] of chains) {
    const ls: Link[] = []
    for (let i = 0; i < chain.length - 1; i++) {
      const [from, to] = [chain[i], chain[i + 1]]
      const down = rowOf.get(to)! > rowOf.get(from)!
      const l: Link = { from, to, gap: Math.min(rowOf.get(from)!, rowOf.get(to)!), down, xs: pos.get(from)!.x, xe: pos.get(to)!.x, track: -1 }
      ls.push(l)
      for (const [node, end, other, side] of [
        [from, 'xs', to, down ? 'bottom' : 'top'],
        [to, 'xe', from, down ? 'top' : 'bottom'],
      ] as const) {
        if (!nodes[node]) continue
        const k = `${node}|${side}`
        if (!sides.has(k)) sides.set(k, [])
        sides.get(k)!.push({ link: l, end, otherX: pos.get(other)!.x })
      }
    }
    linksOf.set(key, ls)
  }
  for (const [k, ends] of sides) {
    const n = nodes[k.slice(0, k.lastIndexOf('|'))]
    const left = pos.get(n.id)!.x - n.width / 2
    ends.sort((p, q) => p.otherX - q.otherX)
    ends.forEach((e, i) => (e.link[e.end] = left + (n.width * (i + 1)) / (ends.length + 1)))
  }

  const trackSpacing = unit * 0.4
  const minRunGap = unit * 0.5
  const tracksInGap = rows.map(() => 0)
  const allLinks = [...linksOf.values()].flat()
  const upperX = (l: Link) => (l.down ? l.xs : l.xe)
  const lowerX = (l: Link) => (l.down ? l.xe : l.xs)
  const alignTolerance = unit * 0.2
  for (let g = 0; g < rows.length - 1; g++) {
    const runs = allLinks
      .filter((l) => l.gap === g && Math.abs(l.xs - l.xe) > 0.5)
      .sort((p, q) => Math.min(p.xs, p.xe) - Math.min(q.xs, q.xe))
    // If A drops from the upper row where B rises to the lower row, A's run must sit
    // above B's, otherwise their vertical stubs overlap on the same x.
    const mustBeAbove = new Map(runs.map((b) => [b, runs.filter((a) => a !== b && Math.abs(upperX(a) - lowerX(b)) < alignTolerance)]))
    // Left-edge channel routing: fill one track at a time, left to right.
    let t = 0
    for (let pending = runs; pending.length; t++) {
      let lastHi = -Infinity
      for (const l of pending) {
        const ready = mustBeAbove.get(l)!.every((a) => a.track !== -1 && a.track < t)
        if (ready && Math.min(l.xs, l.xe) > lastHi + minRunGap) {
          l.track = t
          lastHi = Math.max(l.xs, l.xe)
        }
      }
      // ponytail: a constraint cycle is broken by placing one run anyway; its stub may then overlap
      if (pending.every((l) => l.track === -1)) pending[0].track = t
      pending = pending.filter((l) => l.track === -1)
    }
    tracksInGap[g] = t
  }

  const gapTop: number[] = []
  const gapHeight: number[] = []
  let y = margin
  rows.forEach((row, r) => {
    for (const n of row) {
      pos.get(n)!.y = y + rowHeight[r] / 2
      if (nodes[n]) Object.assign(nodes[n], pos.get(n))
    }
    y += rowHeight[r]
    if (r < rows.length - 1) {
      gapTop[r] = y
      gapHeight[r] = Math.max(unit * 1.2, (tracksInGap[r] + 1) * trackSpacing)
      y += gapHeight[r]
    }
  })

  const portY = (id: string, side: 'top' | 'bottom') => {
    const n = nodes[id]
    if (!n) return pos.get(id)!.y
    return side === 'top' ? n.y - n.height / 2 : n.y + n.height / 2
  }

  const cyclePairs = new Set<string>()
  for (const c of cycles) {
    for (let i = 0; i < c.cycle.length - 1; i++) cyclePairs.add(`${c.cycle[i]}->${c.cycle[i + 1]}`)
  }

  const layoutEdges: LayoutEdge[] = []
  for (const [key, group] of groups) {
    const [caller, callee] = key.split('->')
    const pts: { x: number; y: number }[] = []
    for (const l of linksOf.get(key)!) {
      const ys = portY(l.from, l.down ? 'bottom' : 'top')
      const ye = portY(l.to, l.down ? 'top' : 'bottom')
      pts.push({ x: l.xs, y: ys })
      if (l.track >= 0) {
        const ty = gapTop[l.gap] + (gapHeight[l.gap] * (l.track + 1)) / (tracksInGap[l.gap] + 1)
        pts.push({ x: l.xs, y: ty }, { x: l.xe, y: ty })
      }
      pts.push({ x: l.track >= 0 ? l.xe : l.xs, y: ye })
    }
    const d = pts.map((p, i) => `${i ? 'L' : 'M'} ${p.x} ${p.y}`).join(' ')
    const sources = Array.from(new Set(group.map((e) => e.source)))
    layoutEdges.push({
      caller,
      callee,
      sources,
      evidence: group,
      d,
      mid: pts[Math.floor(pts.length / 2)],
      inCycle: cyclePairs.has(key),
      dockerOnly: sources.every((s) => s === 'docker-compose'),
    })
  }

  return { nodes, edges: layoutEdges, width: contentWidth + margin * 2, height: y + margin }
}
