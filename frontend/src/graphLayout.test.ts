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

  it('does not flag an edge outside any detected cycle', () => {
    const edges = [edge('api-gateway', 'customers-service', 'webclient')]
    const layout = layoutGraph(['api-gateway', 'customers-service'], edges, [])
    expect(layout.edges[0].inCycle).toBe(false)
  })
})
