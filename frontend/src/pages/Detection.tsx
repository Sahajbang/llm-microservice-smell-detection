import { Link } from 'react-router-dom'
import { api } from '../api'
import { useFetch } from '../hooks/useFetch'
import { LoadingBlock, ErrorBlock, EmptyBlock } from '../components/States'
import { SeverityBadge, StatusPill } from '../components/Badges'
import { CyclePath } from '../components/CyclePath'
import { CodeEvidence } from '../components/CodeEvidence'
import { ReasoningTrace } from '../components/ReasoningTrace'
import { detectionStages } from '../stageUtils'
import type { DetectionStage } from '../types'

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

export function Detection() {
  const state = useFetch(api.latestRunDetail, [])

  return (
    <div className="mx-auto max-w-7xl px-6 py-12">
      <h1 className="text-3xl font-semibold tracking-tight text-text">Detection</h1>
      <p className="mt-3 max-w-2xl text-text-secondary">
        Step 4 &mdash; one LLM call per candidate cycle, grounded in the code evidence for each hop, asked to confirm
        whether the cycle is a genuine architectural problem and explain why.
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
            const stages = detectionStages(state.data)
            if (stages.length === 0) {
              return (
                <EmptyBlock
                  title="No detection run logged yet"
                  body="Run pipeline/llm_detection.py against a candidate cycle to produce a step4_detection_*.json entry under logs/runs/."
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
