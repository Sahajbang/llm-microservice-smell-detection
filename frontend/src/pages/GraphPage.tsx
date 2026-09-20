import { useMemo, useState } from 'react'
import { api } from '../api'
import { useFetch } from '../hooks/useFetch'
import { LoadingBlock, ErrorBlock, EmptyBlock } from '../components/States'
import { ServiceGraph } from '../components/ServiceGraph'
import { CodeEvidence } from '../components/CodeEvidence'
import type { Edge, GraphResponse } from '../types'

function NodeDetail({ graph, node }: { graph: GraphResponse; node: string }) {
  const outgoing = useMemo(() => graph.edges.filter((e) => e.caller === node), [graph, node])
  const incoming = useMemo(() => graph.edges.filter((e) => e.callee === node), [graph, node])

  return (
    <div className="space-y-6">
      <div>
        <div className="font-mono text-[11px] uppercase tracking-wide text-text-tertiary">Selected service</div>
        <div className="mt-1 font-mono text-lg text-text">{node}</div>
      </div>
      <EdgeGroup title={`Calls (${outgoing.length})`} edges={outgoing} direction="out" />
      <EdgeGroup title={`Called by (${incoming.length})`} edges={incoming} direction="in" />
    </div>
  )
}

function EdgeGroup({ title, edges, direction }: { title: string; edges: Edge[]; direction: 'out' | 'in' }) {
  if (edges.length === 0) {
    return (
      <div>
        <div className="text-sm font-medium text-text-secondary">{title}</div>
        <p className="mt-2 text-sm text-text-tertiary">None found by the extractor.</p>
      </div>
    )
  }
  return (
    <div>
      <div className="text-sm font-medium text-text-secondary">{title}</div>
      <div className="mt-2 space-y-3">
        {edges.map((e) => (
          <div key={`${e.caller}-${e.callee}-${e.file}-${e.line}`}>
            <div className="mb-1 font-mono text-xs text-text-tertiary">
              {direction === 'out' ? '→ ' : '← '}
              {direction === 'out' ? e.callee : e.caller}
            </div>
            <CodeEvidence edge={e} />
          </div>
        ))}
      </div>
    </div>
  )
}

export function GraphPage() {
  const runs = useFetch(api.runs, [])
  // Default to the newest orchestrator run, so a repository analyzed by URL
  // shows its own graph -- those clones are deleted after the run, and
  // logs/edges_current.json only ever holds the local target repo.
  const [runId, setRunId] = useState<string | null>(null)
  const pipelineRuns = runs.status === 'ready' ? runs.data.filter((r) => r.kind === 'pipeline') : []
  const effectiveRunId = runId ?? pipelineRuns[0]?.id ?? null

  const state = useFetch(() => api.graph(effectiveRunId ?? undefined), [effectiveRunId])
  const [selected, setSelected] = useState<string | null>(null)

  return (
    <div className="mx-auto max-w-7xl px-6 py-12">
      <h1 className="text-3xl font-semibold tracking-tight text-text">Dependency graph</h1>
      <p className="mt-3 max-w-2xl text-text-secondary">
        Nodes are services discovered from module names; edges are call sites the extractor matched. Click a service
        to inspect its calls and the code evidence behind each edge.
      </p>

      {pipelineRuns.length > 0 && (
        <label className="mt-6 inline-flex items-center gap-3">
          <span className="font-mono text-[11px] uppercase tracking-wide text-text-tertiary">Graph from run</span>
          <select
            value={effectiveRunId ?? ''}
            onChange={(e) => setRunId(e.target.value)}
            className="rounded-md border border-border bg-bg-sunken px-3 py-1.5 font-mono text-xs text-text outline-none focus:border-accent/60"
          >
            {pipelineRuns.map((r) => (
              <option key={r.id} value={r.id}>
                {r.repository?.name ?? r.label} — {r.timestamp}
              </option>
            ))}
            <option value="">Phase 1 extractor snapshot</option>
          </select>
        </label>
      )}

      {state.status === 'loading' && (
        <div className="mt-10">
          <LoadingBlock />
        </div>
      )}
      {state.status === 'error' && (
        <div className="mt-10">
          <ErrorBlock message={state.error} />
        </div>
      )}

      {state.status === 'ready' &&
        (state.data.services.length === 0 ? (
          <div className="mt-10">
            <EmptyBlock
              title="No extracted edges found"
              body="Run pipeline/extractor.py against the target repo and write its output to logs/edges_current.json."
            />
          </div>
        ) : (
          <div className="mt-10 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_340px]">
            <ServiceGraph graph={state.data} selected={selected} onSelect={setSelected} />
            <aside className="rounded-lg border border-border p-5">
              {selected ? (
                <NodeDetail graph={state.data} node={selected} />
              ) : (
                <p className="text-sm text-text-tertiary">Select a service in the graph to inspect its dependencies.</p>
              )}
            </aside>
          </div>
        ))}
    </div>
  )
}
