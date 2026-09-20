import { Link } from 'react-router-dom'
import { Play } from '@phosphor-icons/react'
import { api } from '../api'
import { useFetch } from '../hooks/useFetch'
import { LoadingBlock, ErrorBlock, EmptyBlock } from '../components/States'
import { SeverityBadge } from '../components/Badges'
import { SmellBadge } from '../components/FindingCard'
import type { RunSummary } from '../types'

function Stat({ label, value, tone }: { label: string; value: string | number; tone?: 'danger' | 'default' }) {
  return (
    <div className="px-6 py-5 first:pl-0 last:pr-0">
      <div className="font-mono text-[11px] uppercase tracking-wide text-text-tertiary">{label}</div>
      <div className={`mt-1 font-mono text-3xl ${tone === 'danger' ? 'text-danger' : 'text-text'}`}>{value}</div>
    </div>
  )
}

function LatestFindings({ run }: { run: RunSummary }) {
  if (run.findings.length === 0) {
    return (
      <EmptyBlock
        title="No smells detected in the latest run"
        body="Every enabled detector ran and produced no candidate. Analyze another repository to compare."
      />
    )
  }
  return (
    <div className="divide-y divide-border-subtle rounded-lg border border-border">
      {run.findings.map((f) => (
        <Link
          key={f.key}
          to={`/runs/${run.id}`}
          className="flex flex-col gap-2 px-5 py-4 transition-colors hover:bg-bg-elevated sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <SmellBadge smell={f.smell} name={f.smell_name} />
              <span className="font-mono text-sm text-text">{f.services.join(' · ')}</span>
            </div>
            <p className="mt-1.5 max-w-2xl text-[13px] leading-relaxed text-text-secondary">{f.description}</p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            <SeverityBadge severity={f.llmDetection?.severity ?? f.severity} />
            <span className="font-mono text-xs text-text-tertiary">
              {Math.round((f.llmDetection?.confidence ?? f.confidence) * 100)}%
            </span>
          </div>
        </Link>
      ))}
    </div>
  )
}

export function Overview() {
  const state = useFetch(api.overview, [])

  return (
    <div className="mx-auto max-w-7xl px-6 py-12">
      <h1 className="text-3xl font-semibold tracking-tight text-text sm:text-4xl">
        Microservice architecture smells
      </h1>
      <p className="mt-3 max-w-2xl text-text-secondary">
        Static extraction builds a service dependency graph from real call sites and datasource configuration;
        deterministic detectors propose candidates; an LLM validates each one against the code and proposes a
        scope-limited fix. Point it at the bundled repository or any public Git URL.
      </p>

      <Link
        to="/analyze"
        className="mt-6 inline-flex items-center gap-2 rounded-md bg-accent px-4 py-2.5 text-sm font-medium text-bg transition-opacity hover:opacity-90"
      >
        <Play size={15} weight="fill" />
        Start a new analysis
      </Link>

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
              label="Findings (latest run)"
              value={state.data.latestRun?.counts?.findings ?? state.data.cycles.length}
              tone={(state.data.latestRun?.counts?.findings ?? state.data.cycles.length) > 0 ? 'danger' : 'default'}
            />
            <Stat label="Runs logged" value={state.data.runCount} />
          </div>

          <section>
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="text-lg font-semibold text-text">Latest run</h2>
              {state.data.latestRun && (
                <span className="font-mono text-xs text-text-tertiary">
                  {state.data.latestRun.repository?.name ?? state.data.latestRun.label} &middot;{' '}
                  {state.data.latestRun.timestamp}
                </span>
              )}
            </div>
            <div className="mt-4">
              {!state.data.latestRun ? (
                <EmptyBlock
                  title="No runs logged yet"
                  body="Start an analysis to produce a timestamped entry under logs/runs/."
                />
              ) : state.data.latestRun.kind === 'pipeline' ? (
                <LatestFindings run={state.data.latestRun} />
              ) : state.data.cycles.length > 0 ? (
                <div className="rounded-lg border border-danger/30 bg-danger-dim/40 p-5">
                  {state.data.cycles.map((c) => (
                    <p key={c.cycle.join('>')} className="font-mono text-sm text-text">
                      {c.cycle.join(' → ')}
                    </p>
                  ))}
                  <div className="mt-4 flex gap-4 text-sm">
                    <Link to="/graph" className="text-accent hover:underline">
                      View graph
                    </Link>
                    <Link to="/detection" className="text-accent hover:underline">
                      View detection reasoning
                    </Link>
                  </div>
                </div>
              ) : (
                <EmptyBlock
                  title="No cyclic dependencies detected"
                  body="The current edge extraction produced an acyclic service graph."
                />
              )}
            </div>
          </section>

          <section>
            <h2 className="text-lg font-semibold text-text">Evidence sources</h2>
            <div className="mt-4 flex flex-wrap gap-2">
              {Object.entries(state.data.sourceBreakdown).map(([source, count]) => (
                <span
                  key={source}
                  className="rounded-full border border-border bg-bg-elevated px-3 py-1 font-mono text-xs text-text-secondary"
                >
                  {source} <span className="text-text">{count}</span>
                </span>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
