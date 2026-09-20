import { CaretDown } from '@phosphor-icons/react'
import type { DetectionStage, Finding, RefactoringStage } from '../types'
import { SeverityBadge, StatusPill } from './Badges'
import { CodeEvidence } from './CodeEvidence'
import { ReasoningTrace } from './ReasoningTrace'
import { cn } from '../lib/cn'

/** One badge per smell so multi-smell runs stay readable at a glance. */
const SMELL_TONE: Record<string, string> = {
  cyclic_dependency: 'border-danger/40 bg-danger-dim text-danger',
  hub_dependency: 'border-warning/40 bg-warning-dim text-warning',
  shared_persistence: 'border-accent/40 bg-accent-dim text-accent',
}

export function SmellBadge({ smell, name }: { smell: string; name: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-0.5 font-mono text-[11px] uppercase tracking-wide',
        SMELL_TONE[smell] ?? 'border-border bg-bg-elevated-2 text-text-secondary',
      )}
    >
      {name}
    </span>
  )
}

function ServicePath({ services }: { services: string[] }) {
  return (
    <p className="font-mono text-sm text-text">
      {services.map((s, i) => (
        <span key={`${s}-${i}`}>
          {i > 0 && <span className="mx-2 text-text-tertiary">&middot;</span>}
          {s}
        </span>
      ))}
    </p>
  )
}

function Metrics({ metrics }: { metrics: Record<string, unknown> }) {
  const entries = Object.entries(metrics).filter(([, v]) => v !== null && typeof v !== 'object')
  if (entries.length === 0) return null
  return (
    <dl className="mt-4 flex flex-wrap gap-x-6 gap-y-2">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt className="font-mono text-[10px] uppercase tracking-wide text-text-tertiary">{k.replace(/_/g, ' ')}</dt>
          <dd className="font-mono text-sm text-text-secondary">{String(v)}</dd>
        </div>
      ))}
    </dl>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-6">
      <div className="text-sm font-medium text-text-secondary">{title}</div>
      <div className="mt-3">{children}</div>
    </div>
  )
}

function RefactoringPlan({ finding }: { finding: Finding }) {
  const plan = finding.refactoring
  if (!plan) return null
  return (
    <Section title="Proposed refactoring">
      <ul className="space-y-1">
        {plan.affected_files.map((f) => (
          <li key={f} className="font-mono text-xs text-text">
            {f}
          </li>
        ))}
      </ul>
      <div className="mt-4 divide-y divide-border-subtle border-y border-border">
        {plan.changes.map((c, i) => (
          <div key={`${c.file}-${i}`} className="py-4">
            <div className="font-mono text-xs text-text">{c.method_or_class}</div>
            <div className="mt-0.5 font-mono text-[11px] text-text-tertiary">{c.file}</div>
            <p className="mt-2 text-sm leading-relaxed text-text-secondary">{c.description}</p>
          </div>
        ))}
      </div>
      <div className="mt-4 grid grid-cols-1 gap-6 sm:grid-cols-2">
        <div>
          <div className="text-sm font-medium text-text-secondary">Rationale</div>
          <p className="mt-2 text-sm leading-relaxed text-text-secondary">{plan.rationale}</p>
        </div>
        <div>
          <div className="text-sm font-medium text-text-secondary">Expected impact</div>
          <p className="mt-2 text-sm leading-relaxed text-text-secondary">{plan.expected_impact}</p>
        </div>
      </div>
    </Section>
  )
}

export function FindingCard({
  finding,
  detectionStage,
  refactoringStage,
  showRefactoring = true,
}: {
  finding: Finding
  detectionStage?: DetectionStage
  refactoringStage?: RefactoringStage
  showRefactoring?: boolean
}) {
  const llm = finding.llmDetection

  return (
    <div className="rounded-lg border border-border p-5">
      <div className="flex flex-wrap items-center gap-3">
        <SmellBadge smell={finding.smell} name={finding.smell_name} />
        <span className="font-mono text-[11px] text-text-tertiary">{finding.key}</span>
      </div>

      <div className="mt-3">
        <ServicePath services={finding.services} />
      </div>

      <p className="mt-3 max-w-3xl text-sm leading-relaxed text-text-secondary">{finding.description}</p>

      <Metrics metrics={finding.metrics} />

      {/* Deterministic prior and the LLM's own verdict, kept visibly separate --
          the model never overwrites what static analysis measured. */}
      <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="rounded-md border border-border-subtle bg-bg-elevated/50 px-4 py-3">
          <div className="font-mono text-[10px] uppercase tracking-wide text-text-tertiary">Static detector</div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <SeverityBadge severity={finding.severity} />
            <span className="font-mono text-xs text-text-secondary">
              confidence {Math.round(finding.confidence * 100)}%
            </span>
          </div>
        </div>
        <div className="rounded-md border border-border-subtle bg-bg-elevated/50 px-4 py-3">
          <div className="font-mono text-[10px] uppercase tracking-wide text-text-tertiary">LLM validation</div>
          {llm ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <SeverityBadge severity={llm.severity} />
              <span className="font-mono text-xs text-text-secondary">
                confidence {Math.round(llm.confidence * 100)}%
              </span>
              <StatusPill ok={llm.detected} yes="confirmed" no="dismissed" />
              <StatusPill
                ok={llm.refactoring_recommended}
                yes="refactoring recommended"
                no="no refactoring needed"
              />
            </div>
          ) : (
            <p className="mt-2 text-xs text-text-tertiary">
              Not validated — this run was deterministic only, or the call failed.
            </p>
          )}
        </div>
      </div>

      {llm?.rationale && <p className="mt-4 max-w-3xl text-sm leading-relaxed text-text-secondary">{llm.rationale}</p>}

      {finding.errors.length > 0 && (
        <ul className="mt-4 space-y-1">
          {finding.errors.map((e, i) => (
            <li key={i} className="rounded-md border border-warning/30 bg-warning-dim px-3 py-2 text-xs text-warning">
              <span className="font-mono uppercase">{e.stage}</span> {e.message}
            </li>
          ))}
        </ul>
      )}

      {finding.evidence.length > 0 && (
        <details className="group mt-6">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-text-secondary hover:text-text">
            <CaretDown size={14} weight="bold" className="transition-transform group-open:rotate-180" />
            Evidence ({finding.evidence.length})
          </summary>
          <div className="mt-3 space-y-3">
            {finding.evidence.map((e, i) => (
              <CodeEvidence key={`${e.file}-${e.line}-${i}`} edge={e} />
            ))}
          </div>
        </details>
      )}

      {showRefactoring && <RefactoringPlan finding={finding} />}

      {(detectionStage || refactoringStage) && (
        <div className="mt-6 space-y-3">
          {detectionStage && (
            <ReasoningTrace systemPrompt={detectionStage.system_prompt} attempts={detectionStage.attempts} />
          )}
          {refactoringStage && (
            <ReasoningTrace systemPrompt={refactoringStage.system_prompt} attempts={refactoringStage.attempts} />
          )}
        </div>
      )}
    </div>
  )
}
