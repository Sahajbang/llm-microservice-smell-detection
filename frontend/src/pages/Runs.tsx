import { Link } from 'react-router-dom'
import { api } from '../api'
import { useFetch } from '../hooks/useFetch'
import { LoadingBlock, ErrorBlock, EmptyBlock } from '../components/States'
import { SeverityBadge, StatusPill } from '../components/Badges'

export function Runs() {
  const state = useFetch(api.runs, [])

  return (
    <div className="mx-auto max-w-7xl px-6 py-12">
      <h1 className="text-3xl font-semibold tracking-tight text-text">Run history</h1>
      <p className="mt-3 max-w-2xl text-text-secondary">
        Step 7 &mdash; one timestamped directory per run, holding every stage's full input and output for
        reproducibility.
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

      {state.status === 'ready' &&
        (state.data.length === 0 ? (
          <div className="mt-10">
            <EmptyBlock title="No runs logged yet" body="Runs appear here once pipeline/run_logger.py writes to logs/runs/." />
          </div>
        ) : (
          <div className="mt-10 divide-y divide-border border-y border-border">
            {state.data.map((run) => (
              <Link
                key={run.id}
                to={`/runs/${run.id}`}
                className="flex flex-col gap-3 py-5 transition-colors hover:bg-bg-elevated sm:flex-row sm:items-center sm:justify-between"
              >
                <div>
                  <div className="font-mono text-sm text-text">{run.label}</div>
                  <div className="mt-0.5 font-mono text-xs text-text-tertiary">{run.timestamp}</div>
                  {run.cycle && (
                    <div className="mt-1.5 font-mono text-xs text-text-secondary">{run.cycle.join(' → ')}</div>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {run.detection && <SeverityBadge severity={run.detection.severity} />}
                  {run.detection && <StatusPill ok={run.detection.detected} yes="confirmed" no="rejected" />}
                  {run.refactoring && <StatusPill ok={run.scopeOk} yes="scope ok" no="scope failed" />}
                </div>
              </Link>
            ))}
          </div>
        ))}
    </div>
  )
}
