import { Link } from 'react-router-dom'
import { api } from '../api'
import { useFetch } from '../hooks/useFetch'
import { LoadingBlock, ErrorBlock, EmptyBlock } from '../components/States'
import { SeverityBadge } from '../components/Badges'

function Stat({ label, value, tone }: { label: string; value: string | number; tone?: 'danger' | 'default' }) {
  return (
    <div className="px-6 py-5 first:pl-0 last:pr-0">
      <div className="font-mono text-[11px] uppercase tracking-wide text-text-tertiary">{label}</div>
      <div className={`mt-1 font-mono text-3xl ${tone === 'danger' ? 'text-danger' : 'text-text'}`}>{value}</div>
    </div>
  )
}

export function Overview() {
  const state = useFetch(api.overview, [])

  return (
    <div className="mx-auto max-w-7xl px-6 py-12">
      <h1 className="text-3xl font-semibold tracking-tight text-text sm:text-4xl">Service Dependency Graph</h1>
      <p className="mt-3 max-w-2xl text-text-secondary">
        Cyclic Dependency detection across{' '}
        <span className="font-mono text-text">spring-petclinic-microservices</span>, grounded in static extraction
        and confirmed by an LLM detection agent. Phase 1 scope: one smell, one repository.
      </p>

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

      {state.status === 'ready' && (
        <div className="mt-10 space-y-10">
          <div className="grid grid-cols-2 divide-y divide-border-subtle border-y border-border sm:grid-cols-4 sm:divide-x sm:divide-y-0">
            <Stat label="Services" value={state.data.services.length} />
            <Stat label="Edges extracted" value={state.data.edgeCount} />
            <Stat
              label="Cycles detected"
              value={state.data.cycles.length}
              tone={state.data.cycles.length > 0 ? 'danger' : 'default'}
            />
            <Stat label="Runs logged" value={state.data.runCount} />
          </div>

          {state.data.cycles.length > 0 ? (
            <section>
              <h2 className="text-lg font-semibold text-text">Active finding</h2>
              <div className="mt-4 rounded-lg border border-danger/30 bg-danger-dim/40 p-5">
                {state.data.cycles.map((c) => (
                  <p key={c.cycle.join('>')} className="font-mono text-sm text-text">
                    {c.cycle.map((node, i) => (
                      <span key={i}>
                        {i > 0 && <span className="mx-2 text-danger">&rarr;</span>}
                        {node}
                      </span>
                    ))}
                  </p>
                ))}
                {state.data.latestRun?.detection && (
                  <div className="mt-4 flex flex-wrap items-center gap-3">
                    <SeverityBadge severity={state.data.latestRun.detection.severity} />
                    <span className="font-mono text-xs text-text-secondary">
                      confidence {Math.round(state.data.latestRun.detection.confidence * 100)}%
                    </span>
                  </div>
                )}
                <div className="mt-4 flex gap-4 text-sm">
                  <Link to="/graph" className="text-accent hover:underline">
                    View graph
                  </Link>
                  <Link to="/detection" className="text-accent hover:underline">
                    View detection reasoning
                  </Link>
                </div>
              </div>
            </section>
          ) : (
            <EmptyBlock
              title="No cyclic dependencies detected"
              body="The current edge extraction produced an acyclic service graph. Re-run the extractor after code changes to refresh this."
            />
          )}

          <section>
            <h2 className="text-lg font-semibold text-text">Most recent run</h2>
            {state.data.latestRun ? (
              <Link
                to={`/runs/${state.data.latestRun.id}`}
                className="mt-4 flex flex-col gap-1 rounded-lg border border-border px-5 py-4 transition-colors hover:border-accent/40"
              >
                <span className="font-mono text-sm text-text">{state.data.latestRun.label}</span>
                <span className="font-mono text-xs text-text-tertiary">{state.data.latestRun.timestamp}</span>
              </Link>
            ) : (
              <div className="mt-4">
                <EmptyBlock
                  title="No runs logged yet"
                  body="Run the Step 4/5 LLM agents to produce a timestamped entry under logs/runs/."
                />
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
