import { Link } from 'react-router-dom'
import { api } from '../api'
import { useFetch } from '../hooks/useFetch'
import { LoadingBlock, ErrorBlock, EmptyBlock } from '../components/States'
import { SeverityBadge, StatusPill } from '../components/Badges'
import { CyclePath } from '../components/CyclePath'
import { CodeEvidence } from '../components/CodeEvidence'
import { ReasoningTrace } from '../components/ReasoningTrace'
import { FindingCard } from '../components/FindingCard'
import { detectionStageFor, detectionStages, refactoringStageFor } from '../stageUtils'
import type { DetectionStage, RunDetail } from '../types'

/** Phase 1 run shape: one cycle, one detection call, no finding registry. */
export function DetectionCard({ stage }: { stage: DetectionStage }) {
  const { result } = stage
  return (
    <div className="rounded-lg border border-border p-5">
      <CyclePath cycle={stage.cycle} />

      <div className="mt-4 flex flex-wrap items-center gap-3">
        <SeverityBadge severity={result.severity} />
        <span className="font-mono text-xs text-text-secondary">
          confidence {Math.round(result.confidence * 100)}%
        </span>
        <StatusPill ok={result.detected} yes="confirmed" no="not confirmed" />
        <StatusPill ok={result.refactoring_recommended} yes="refactoring recommended" no="no refactoring needed" />
      </div>

      <p className="mt-4 max-w-3xl text-sm leading-relaxed text-text-secondary">{result.rationale}</p>

      <div className="mt-6">
        <div className="text-sm font-medium text-text-secondary">Code evidence</div>
        <div className="mt-3 space-y-3">
          {stage.evidence.map((e) => (
            <CodeEvidence key={`${e.caller}-${e.callee}-${e.file}-${e.line}`} edge={e} />
          ))}
        </div>
      </div>

      <div className="mt-6">
        <ReasoningTrace systemPrompt={stage.system_prompt} attempts={stage.attempts} />
      </div>
    </div>
  )
}

/** Findings from a multi-smell orchestrator run, any smell, newest run only. */
export function FindingsList({ run, showRefactoring }: { run: RunDetail; showRefactoring?: boolean }) {
  return (
    <div className="space-y-6">
      {run.summary.findings.map((f) => (
        <FindingCard
          key={f.key}
          finding={f}
          detectionStage={detectionStageFor(run, f.key)}
          refactoringStage={showRefactoring ? refactoringStageFor(run, f.key) : undefined}
          showRefactoring={showRefactoring}
        />
      ))}
    </div>
  )
}

export function RunContextBar({ run }: { run: RunDetail }) {
  const { summary } = run
  const repo = summary.repository
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-border bg-bg-elevated/40 px-5 py-3">
      <span className="font-mono text-xs text-text">
        {repo ? repo.name : summary.label}
        {repo?.branch && <span className="text-text-tertiary">@{repo.branch}</span>}
      </span>
      <span className="font-mono text-[11px] text-text-tertiary">{summary.timestamp}</span>
      {summary.detectorsRun.length > 0 && (
        <span className="font-mono text-[11px] text-text-tertiary">
          detectors: {summary.detectorsRun.join(', ')}
        </span>
      )}
      {summary.llmEnabled === false && (
        <span className="font-mono text-[11px] text-warning">LLM validation was disabled for this run</span>
      )}
      <Link to={`/runs/${summary.id}`} className="ml-auto text-xs text-accent hover:underline">
        Full run log &rarr;
      </Link>
    </div>
  )
}

export function Detection() {
  const state = useFetch(api.latestRunDetail, [])

  return (
    <div className="mx-auto max-w-7xl px-6 py-12">
      <h1 className="text-3xl font-semibold tracking-tight text-text">Detection</h1>
      <p className="mt-3 max-w-2xl text-text-secondary">
        Deterministic detectors propose candidates from the extracted evidence; the LLM then judges each candidate
        against the code behind it. Both verdicts are kept — the model never overwrites what static analysis measured.
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
            const run = state.data
            if (run?.summary.kind === 'pipeline') {
              if (run.summary.findings.length === 0) {
                return (
                  <div className="space-y-6">
                    <RunContextBar run={run} />
                    <EmptyBlock
                      title="No smells detected in the latest run"
                      body="Every enabled detector ran and none produced a candidate. That is a result, not a failure — try another repository from the New analysis page."
                    />
                  </div>
                )
              }
              return (
                <div className="space-y-6">
                  <RunContextBar run={run} />
                  <FindingsList run={run} showRefactoring={false} />
                </div>
              )
            }

            const stages = detectionStages(run)
            if (stages.length === 0) {
              return (
                <EmptyBlock
                  title="No detection run logged yet"
                  body="Start a run from the New analysis page, or run python -m pipeline.run_pipeline target-repo from the repo root."
                />
              )
            }
            return (
              <div className="space-y-6">
                {stages.map(([key, stage]) => (
                  <DetectionCard key={key} stage={stage} />
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
