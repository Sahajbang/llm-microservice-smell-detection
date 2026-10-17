import { describe, expect, it } from 'vitest'
import { classifyNodes, layoutGraph } from './graphLayout'
import type { Edge } from './types'

function edge(caller: string, callee: string, source: Edge['source']): Edge {
  return { caller, callee, source, file: 'x.java', line: 1, evidence: `${caller}->${callee}` }
}

describe('classifyNodes', () => {
  it('separates infra (docker-compose-only) services from services with real calls', () => {
    const edges = [
      edge('api-gateway', 'customers-service', 'webclient'),
      edge('api-gateway', 'config-server', 'docker-compose'),
      edge('customers-service', 'config-server', 'docker-compose'),
    ]
    const { service, infra } = classifyNodes(['api-gateway', 'customers-service', 'config-server'], edges)
    expect(service.sort()).toEqual(['api-gateway', 'customers-service'])
    expect(infra).toEqual(['config-server'])
  })

  it('treats a node with no edges at all as a service, not infra', () => {
    const { service, infra } = classifyNodes(['isolated-service'], [])
    expect(service).toEqual(['isolated-service'])
    expect(infra).toEqual([])
  })
})

describe('layoutGraph', () => {
  it('flags edges that participate in a detected cycle', () => {
    const edges = [
      edge('visits-service', 'customers-service', 'resttemplate'),
      edge('customers-service', 'visits-service', 'resttemplate'),
    ]
    const cycles = [{ smell: 'Cyclic Dependency', cycle: ['visits-service', 'customers-service', 'visits-service'] }]
    const layout = layoutGraph(['visits-service', 'customers-service'], edges, cycles)

    expect(layout.edges).toHaveLength(2)
    expect(layout.edges.every((e) => e.inCycle)).toBe(true)
    // Reverse edges between the same pair must curve apart, not overlap.
    const [a, b] = layout.edges
    expect(a.mid).not.toEqual(b.mid)
  })

  it('never overlaps nodes, however many services or long names there are', () => {
    const services = Array.from({ length: 40 }, (_, i) => `bookstore-very-long-service-name-${i}`)
    const edges = services.slice(1).map((s, i) => edge(services[i % 5], s, 'feign'))
    edges.push(edge(services[39], services[0], 'feign'))
    const nodes = Object.values(layoutGraph(services, edges, []).nodes)
    for (const a of nodes) {
      for (const b of nodes) {
        if (a === b) continue
        const apart = Math.abs(a.x - b.x) >= (a.width + b.width) / 2 || Math.abs(a.y - b.y) >= (a.height + b.height) / 2
        expect(apart, `${a.id} overlaps ${b.id}`).toBe(true)
      }
    }
  })

  it('routes every edge with straight horizontal/vertical segments that never cross a node', () => {
    const services = Array.from({ length: 40 }, (_, i) => `svc-${i}`)
    const edges = services.slice(1).map((s, i) => edge(services[i % 5], s, 'feign'))
    edges.push(edge(services[39], services[0], 'feign'), edge(services[2], services[30], 'feign'))
    const layout = layoutGraph(services, edges, [])
    const nodes = Object.values(layout.nodes)
    for (const e of layout.edges) {
      const pts = [...e.d.matchAll(/[ML] ([\d.e-]+) ([\d.e-]+)/g)].map((m) => ({ x: +m[1], y: +m[2] }))
      for (let i = 1; i < pts.length; i++) {
        const [a, b] = [pts[i - 1], pts[i]]
        expect(Math.abs(a.x - b.x) < 1e-6 || Math.abs(a.y - b.y) < 1e-6, `${e.caller}->${e.callee} has a diagonal`).toBe(true)
        for (const n of nodes) {
          if (n.id === e.caller || n.id === e.callee) continue
          const hit =
            Math.max(a.x, b.x) > n.x - n.width / 2 && Math.min(a.x, b.x) < n.x + n.width / 2 &&
            Math.max(a.y, b.y) > n.y - n.height / 2 && Math.min(a.y, b.y) < n.y + n.height / 2
          expect(hit, `${e.caller}->${e.callee} crosses ${n.id}`).toBe(false)
        }
      }
    }
  })

  it('ranks infra-to-infra startup edges onto separate rows', () => {
    const edges = [
      edge('api-gateway', 'customers-service', 'feign'),
      edge('api-gateway', 'config-server', 'docker-compose'),
      edge('discovery-server', 'config-server', 'docker-compose'),
    ]
    const layout = layoutGraph(['api-gateway', 'customers-service', 'config-server', 'discovery-server'], edges, [])
    expect(layout.nodes['discovery-server'].y).toBeLessThan(layout.nodes['config-server'].y)
  })

  it('places a caller above the service it calls', () => {
    const layout = layoutGraph(['customers-service', 'api-gateway'], [edge('api-gateway', 'customers-service', 'feign')], [])
    expect(layout.nodes['api-gateway'].y).toBeLessThan(layout.nodes['customers-service'].y)
  })

  it('does not flag an edge outside any detected cycle', () => {
    const edges = [edge('api-gateway', 'customers-service', 'webclient')]
    const layout = layoutGraph(['api-gateway', 'customers-service'], edges, [])
    expect(layout.edges[0].inCycle).toBe(false)
  })
})
