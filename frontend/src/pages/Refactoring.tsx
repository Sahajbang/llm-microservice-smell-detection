import { Link } from 'react-router-dom'
import { api } from '../api'
import { useFetch } from '../hooks/useFetch'
import { LoadingBlock, ErrorBlock, EmptyBlock } from '../components/States'
import { StatusPill } from '../components/Badges'
import { CyclePath } from '../components/CyclePath'
import { ReasoningTrace } from '../components/ReasoningTrace'
import { refactoringStages } from '../stageUtils'
import type { RefactoringStage } from '../types'

export function RefactoringCard({ stage }: { stage: RefactoringStage }) {
  const { result } = stage
  const lastAttempt = stage.attempts[stage.attempts.length - 1]

  return (
    <div className="rounded-lg border border-border p-5">
      <CyclePath cycle={stage.cycle} />

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <StatusPill ok={lastAttempt?.scope_ok ?? null} yes="scope check passed" no="scope check failed" />
        <span className="font-mono text-xs text-text-tertiary">
          allowed: {stage.allowed_module_prefixes.join(', ')}
        </span>
      </div>

      <div className="mt-6">
        <div className="text-sm font-medium text-text-secondary">Affected files</div>
        <ul className="mt-2 space-y-1">
          {result.affected_files.map((f) => (
            <li key={f} className="font-mono text-xs text-text">
              {f}
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-6 divide-y divide-border-subtle border-y border-border">
        {result.changes.map((c) => (
          <div key={`${c.file}-${c.method_or_class}`} className="py-4">
            <div className="font-mono text-xs text-text">{c.method_or_class}</div>
            <div className="mt-0.5 font-mono text-[11px] text-text-tertiary">{c.file}</div>
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">{c.description}</p>
          </div>
        ))}
      </div>

      <div className="mt-6 grid grid-cols-1 gap-6 sm:grid-cols-2">
        <div>
          <div className="text-sm font-medium text-text-secondary">Rationale</div>
          <p className="mt-2 text-sm leading-relaxed text-text-secondary">{result.rationale}</p>
        </div>
        <div>
          <div className="text-sm font-medium text-text-secondary">Expected impact</div>
          <p className="mt-2 text-sm leading-relaxed text-text-secondary">{result.expected_impact}</p>
        </div>
      </div>

      <div className="mt-6">
        <ReasoningTrace systemPrompt={stage.system_prompt} attempts={stage.attempts} />
      </div>
    </div>
  )
}

export function Refactoring() {
  const state = useFetch(api.latestRunDetail, [])

  return (
    <div className="mx-auto max-w-7xl px-6 py-12">
      <h1 className="text-3xl font-semibold tracking-tight text-text">Refactoring</h1>
      <p className="mt-3 max-w-2xl text-text-secondary">
        Step 5 &mdash; given a confirmed cycle, the refactoring agent proposes the minimal scope-limited change that
        breaks it. Every file it names is checked against the services in the cycle before acceptance.
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
        <div className="mt-10">
          {(() => {
            const stages = refactoringStages(state.data)
            if (stages.length === 0) {
              return (
                <EmptyBlock
                  title="No refactoring proposal logged yet"
                  body="Run pipeline/llm_refactoring.py against a confirmed cycle to produce a step5_refactoring_*.json entry under logs/runs/."
                />
              )
            }
            return (
              <div className="space-y-6">
                {stages.map(([key, stage]) => (
                  <RefactoringCard key={key} stage={stage} />
                ))}
                <Link to="/runs" className="inline-block text-sm text-accent hover:underline">
                  View all runs &rarr;
                </Link>
              </div>
            )
          })()}
        </div>
      )}
    </div>
  )
}
