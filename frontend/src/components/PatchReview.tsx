import { useState } from 'react'
import { CaretDown, Check, GitBranch, Warning, X } from '@phosphor-icons/react'
import { api } from '../api'
import { cn } from '../lib/cn'
import type { BuildRun, Job, PatchFile, PatchResult, PatchStatus, Verification } from '../types'

/** Unified diff, coloured by line prefix. */
export function DiffView({ diff, maxHeight = 'max-h-[28rem]' }: { diff: string; maxHeight?: string }) {
  if (!diff) return <p className="text-xs text-text-tertiary">Empty diff.</p>
  return (
    <pre
      className={cn(
        'overflow-auto rounded-md border border-border bg-bg-sunken py-2 font-mono text-[12px] leading-[1.55]',
        maxHeight,
      )}
    >
      {diff.split('\n').map((line, i) => (
        <div
          key={i}
          className={cn(
            'whitespace-pre px-3',
            line.startsWith('+++') || line.startsWith('---')
              ? 'font-semibold text-text'
              : line.startsWith('@@')
                ? 'text-accent'
                : line.startsWith('+')
                  ? 'bg-ok-dim/60 text-ok'
                  : line.startsWith('-')
                    ? 'bg-danger-dim/60 text-danger'
                    : 'text-text-secondary',
          )}
        >
          {line || ' '}
        </div>
      ))}
    </pre>
  )
}

const STATUS_STYLE: Record<PatchStatus, { label: string; tone: string }> = {
  proposed: { label: 'previewed', tone: 'border-border bg-bg-elevated-2 text-text-secondary' },
  rejected: { label: 'rejected', tone: 'border-border bg-bg-elevated-2 text-text-tertiary' },
  verified: { label: 'verified & committed', tone: 'border-ok/30 bg-ok-dim text-ok' },
  rolled_back: { label: 'rolled back', tone: 'border-warning/30 bg-warning-dim text-warning' },
  failed_kept: { label: 'kept, unverified', tone: 'border-danger/30 bg-danger-dim text-danger' },
  error: { label: 'error', tone: 'border-danger/30 bg-danger-dim text-danger' },
}

export function PatchStatusBadge({ status }: { status: PatchStatus }) {
  const s = STATUS_STYLE[status] ?? { label: status, tone: 'border-border text-text-secondary' }
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-0.5 font-mono text-xs uppercase tracking-wide',
        s.tone,
      )}
    >
      {s.label}
    </span>
  )
}

function FileList({ files }: { files: PatchFile[] }) {
  return (
    <ul className="space-y-1">
      {files.map((f) => (
        <li key={f.path} className="flex gap-3 font-mono text-xs">
          <span
            className={cn(
              'w-14 shrink-0 uppercase',
              f.action === 'create' ? 'text-ok' : f.action === 'delete' ? 'text-danger' : 'text-text-tertiary',
            )}
          >
            {f.action}
          </span>
          <span className="break-all text-text">{f.path}</span>
        </li>
      ))}
    </ul>
  )
}

function CheckTile({ label, ok, detail }: { label: string; ok: boolean | null; detail?: string | null }) {
  return (
    <div className="rounded-md border border-border-subtle bg-bg-elevated/50 px-4 py-3">
      <div className="font-mono text-[10px] uppercase tracking-wide text-text-tertiary">{label}</div>
      <div
        className={cn(
          'mt-1.5 flex items-center gap-1.5 text-sm',
          ok === true ? 'text-ok' : ok === false ? 'text-danger' : 'text-text-tertiary',
        )}
      >
        {ok === true ? <Check size={14} weight="bold" /> : ok === false ? <X size={14} weight="bold" /> : null}
        {ok === true ? 'pass' : ok === false ? 'fail' : 'skipped'}
      </div>
      {detail && <p className="mt-1 text-xs leading-relaxed text-text-tertiary">{detail}</p>}
    </div>
  )
}

function BuildOutput({ runs }: { runs: BuildRun[] }) {
  const failed = runs.filter((r) => r.passed === false && r.output_tail)
  if (failed.length === 0) return null
  return (
    <details className="group mt-3">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-xs font-medium text-text-secondary hover:text-text">
        <CaretDown size={12} weight="bold" className="transition-transform group-open:rotate-180" />
        Build output ({failed.length} failing)
      </summary>
      {failed.map((r, i) => (
        <div key={i} className="mt-2">
          <div className="font-mono text-[11px] text-text-tertiary">
            {r.command} &middot; exit {r.returncode ?? '—'} &middot; {r.cwd}
          </div>
          <pre className="mt-1 max-h-64 overflow-auto rounded-md border border-border bg-bg-sunken p-3 font-mono text-[11px] leading-relaxed text-text-secondary">
            {r.output_tail}
          </pre>
        </div>
      ))}
    </details>
  )
}

export function VerificationView({ verification }: { verification: Verification }) {
  const { build, redetection } = verification
  const buildOk = build.status === 'passed' ? true : build.status === 'failed' ? false : null
  const buildDetail =
    build.status === 'skipped'
      ? build.reason
      : `${build.level} · ${build.runs.map((r) => r.build_system).join(', ')}`
  const remaining = redetection.still_detected ? 'the same finding is still detected' : null
  return (
    <div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <CheckTile label="Build" ok={buildOk} detail={buildDetail} />
        <CheckTile
          label="Smell re-detection"
          ok={redetection.resolved}
          detail={remaining ?? (redetection.errors.length ? redetection.errors.join('; ') : 'finding no longer detected')}
        />
      </div>
      <BuildOutput runs={build.runs} />
    </div>
  )
}

/** The patch a run generated for one finding, and what happened to it. */
export function PatchResultView({ patch }: { patch: PatchResult }) {
  return (
    <div className="mt-6 rounded-md border border-border p-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium text-text-secondary">Applied patch</span>
        <PatchStatusBadge status={patch.status} />
        {patch.branch && patch.commit && (
          <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-text-tertiary">
            <GitBranch size={12} weight="bold" />
            {patch.branch} @ {patch.commit.slice(0, 10)}
          </span>
        )}
      </div>
      {patch.error && <p className="mt-3 text-xs text-danger">{patch.error}</p>}
      {patch.files.length > 0 && (
        <div className="mt-4">
          <FileList files={patch.files} />
        </div>
      )}
      {patch.verification && (
        <div className="mt-4">
          <VerificationView verification={patch.verification} />
        </div>
      )}
      {patch.diff && (
        <details className="group mt-4">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-medium text-text-secondary hover:text-text">
            <CaretDown size={14} weight="bold" className="transition-transform group-open:rotate-180" />
            Diff
          </summary>
          <div className="mt-3">
            <DiffView diff={patch.diff} />
          </div>
        </details>
      )}
    </div>
  )
}

const ANSWER_LABEL: Record<string, string> = {
  approve: 'Approve & apply',
  reject: 'Reject',
  approve_all: 'Approve all remaining',
  rollback: 'Roll back',
  keep: 'Keep on branch',
}

/** Shown while an `ask`-mode run is blocked waiting for a human. */
export function ApprovalPanel({ job }: { job: Job }) {
  const pending = job.pendingDecision
  const [sentFor, setSentFor] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  if (!pending) return null

  const busy = sentFor === pending.id
  const send = async (answer: string) => {
    setSentFor(pending.id)
    setError(null)
    try {
      await api.decide(job.id, pending.id, answer)
    } catch (err) {
      setSentFor(null)
      setError(err instanceof Error ? err.message : String(err))
    }
  }

  const isApply = pending.kind === 'apply'
  return (
    <section
      aria-live="polite"
      className="rounded-lg border border-accent/40 bg-accent-dim/15 p-5"
    >
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-[11px] uppercase tracking-wide text-accent">Your decision is needed</span>
        <span className="font-mono text-[11px] text-text-tertiary">{pending.finding_key}</span>
      </div>
      <h3 className="mt-2 text-lg font-semibold text-text">
        {isApply
          ? `Apply this patch for ${pending.smell_name}?`
          : 'This patch failed verification. Keep it or roll it back?'}
      </h3>
      {isApply ? (
        <p className="mt-1 max-w-3xl text-sm leading-relaxed text-text-secondary">
          It will be written to a separate git worktree on its own branch, built, and re-checked for the smell. Your
          working tree is not touched.
          {pending.rationale && <span className="mt-2 block text-text-tertiary">{pending.rationale}</span>}
        </p>
      ) : (
        <p className="mt-1 max-w-3xl text-sm leading-relaxed text-text-secondary">
          Keeping commits it to the refactor branch marked <span className="font-mono">[unverified]</span>, and later
          patches are built on top of it. Rolling back reverts it.
        </p>
      )}

      <div className="mt-4">
        <FileList files={pending.files} />
      </div>
      {!isApply && pending.verification && (
        <div className="mt-4">
          <VerificationView verification={pending.verification} />
        </div>
      )}
      <div className="mt-4">
        <DiffView diff={pending.diff} maxHeight="max-h-[24rem]" />
      </div>

      {error && (
        <div className="mt-4 flex gap-2 rounded-md border border-danger/30 bg-danger-dim px-3 py-2 text-sm text-danger">
          <Warning size={16} weight="bold" className="mt-0.5 shrink-0" />
          {error}
        </div>
      )}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        {pending.options.map((option) => {
          const primary = option === 'approve' || (!isApply && option === pending.default)
          return (
            <button
              key={option}
              type="button"
              disabled={busy}
              onClick={() => void send(option)}
              className={cn(
                'rounded-md px-4 py-2 text-sm font-medium transition-opacity disabled:cursor-not-allowed disabled:opacity-50',
                primary
                  ? 'bg-accent text-bg hover:opacity-90'
                  : 'border border-border text-text-secondary hover:border-accent/40 hover:text-text',
              )}
            >
              {ANSWER_LABEL[option] ?? option}
            </button>
          )
        })}
        {busy && <span className="font-mono text-[11px] text-text-tertiary">sent, waiting for the run&hellip;</span>}
      </div>
    </section>
  )
}
