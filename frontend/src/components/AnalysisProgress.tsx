import { useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, Warning, X } from '@phosphor-icons/react'
import type { Job, JobEvent } from '../types'
import { cn } from '../lib/cn'

/** Elapsed seconds, ticking while the job runs. */
function useElapsed(startedAt: string, finishedAt: string | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (finishedAt) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [finishedAt])
  const end = finishedAt ? Date.parse(finishedAt) : now
  return Math.max(0, Math.round((end - Date.parse(startedAt)) / 1000))
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return m > 0 ? `${m}m ${String(s).padStart(2, '0')}s` : `${s}s`
}

type StationState = 'done' | 'active' | 'pending' | 'skipped' | 'failed'

function Station({
  label,
  index,
  total,
  state,
  events,
}: {
  label: string
  index: number
  total: number
  state: StationState
  events: JobEvent[]
}) {
  const isLast = index === total - 1
  return (
    <li className="relative flex gap-4 pb-8 last:pb-0">
      {/* Conduit to the next station: animated only while signal is flowing. */}
      {!isLast && (
        <span
          aria-hidden
          className={cn(
            'absolute left-[13px] top-7 bottom-1 w-px',
            state === 'done'
              ? 'bg-accent/50'
              : state === 'active'
                ? 'conduit w-[2px] left-3'
                : 'bg-border',
          )}
        />
      )}

      <span
        className={cn(
          'relative z-10 mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border font-mono text-[11px]',
          state === 'done' && 'border-accent/60 bg-accent/15 text-accent',
          state === 'active' && 'station-active border-accent bg-accent/25 text-accent',
          state === 'pending' && 'border-border bg-bg-elevated text-text-tertiary',
          state === 'skipped' && 'border-border bg-bg-elevated text-text-tertiary/60',
          state === 'failed' && 'border-danger bg-danger-dim text-danger',
        )}
      >
        {state === 'done' && <Check size={13} weight="bold" />}
        {state === 'failed' && <X size={13} weight="bold" />}
        {state !== 'done' && state !== 'failed' && index + 1}
      </span>

      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-3">
          <span
            className={cn(
              'text-sm font-medium',
              state === 'pending' || state === 'skipped' ? 'text-text-tertiary' : 'text-text',
            )}
          >
            {label}
          </span>
          {state === 'active' && (
            <span className="font-mono text-[11px] uppercase tracking-wide text-accent">running</span>
          )}
          {state === 'skipped' && (
            <span className="font-mono text-[11px] uppercase tracking-wide text-text-tertiary">skipped</span>
          )}
        </div>
        {events.length > 0 && (
          <ul className="mt-1.5 space-y-1">
            {events.map((e, i) => (
              <li key={`${e.at}-${i}`} className="text-[13px] leading-snug text-text-secondary">
                {e.label}
                {e.detail && <span className="ml-2 font-mono text-[11px] text-text-tertiary">{e.detail}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  )
}

function EventFeed({ events, running }: { events: JobEvent[]; running: boolean }) {
  const endRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [events.length])

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-lg border border-border bg-bg-sunken">
      <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
        <span className="font-mono text-[11px] uppercase tracking-wide text-text-tertiary">Stage log</span>
        <span className="font-mono text-[11px] text-text-tertiary">
          {events.length} event{events.length === 1 ? '' : 's'}
        </span>
      </div>
      <div className="max-h-[22rem] flex-1 overflow-y-auto px-4 py-3">
        <ol className="space-y-2">
          {events.map((e, i) => (
            <li key={`${e.at}-${i}`} className="event-in font-mono text-[12px] leading-relaxed">
              <span className="text-text-tertiary">{e.at.slice(11, 19)}</span>{' '}
              <span className="text-accent/80">{e.phase}</span>{' '}
              <span className="text-text-secondary">{e.label}</span>
              {e.detail && <div className="pl-[4.5rem] text-text-tertiary">{e.detail}</div>}
            </li>
          ))}
          {running && (
            <li className="font-mono text-[12px] text-text-tertiary">
              <span className="inline-block animate-pulse">working&hellip;</span>
            </li>
          )}
        </ol>
        <div ref={endRef} />
      </div>
    </div>
  )
}

export function AnalysisProgress({
  job,
  phases,
  onReset,
}: {
  job: Job
  phases: { key: string; label: string }[]
  onReset: () => void
}) {
  const elapsed = useElapsed(job.startedAt, job.finishedAt)
  const running = job.status === 'running'

  const eventsByPhase = useMemo(() => {
    const map = new Map<string, JobEvent[]>()
    for (const e of job.events) {
      const list = map.get(e.phase) ?? []
      list.push(e)
      map.set(e.phase, list)
    }
    return map
  }, [job.events])

  const currentIndex = Math.max(
    0,
    phases.findIndex((p) => p.key === job.phase),
  )

  const stationState = (index: number): StationState => {
    if (job.status === 'failed' && index === currentIndex) return 'failed'
    if (!running && job.status === 'done') {
      // A phase the run never reached (e.g. LLM disabled) is skipped, not pending.
      return eventsByPhase.has(phases[index].key) || index === phases.length - 1 ? 'done' : 'skipped'
    }
    if (index < currentIndex) return 'done'
    if (index === currentIndex) return running ? 'active' : 'done'
    return 'pending'
  }

  return (
    <div className="space-y-6">
      {/* Header: what is being analyzed, and for how long. */}
      <div
        className={cn(
          'relative overflow-hidden rounded-lg border px-5 py-4',
          running && 'sweep border-accent/30 bg-accent-dim/20',
          job.status === 'done' && 'border-ok/30 bg-ok-dim/25',
          job.status === 'failed' && 'border-danger/30 bg-danger-dim/40',
        )}
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="font-mono text-[11px] uppercase tracking-wide text-text-tertiary">
              {running ? 'Analysis in progress' : job.status === 'done' ? 'Analysis complete' : 'Analysis failed'}
            </div>
            <div className="mt-1 truncate font-mono text-lg text-text" title={job.source}>
              {job.source}
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {job.smells.map((s) => (
                <span
                  key={s}
                  className="rounded-full border border-border bg-bg-elevated-2 px-2 py-0.5 font-mono text-[11px] text-text-secondary"
                >
                  {s}
                </span>
              ))}
              {!job.llm && (
                <span className="rounded-full border border-border bg-bg-elevated-2 px-2 py-0.5 font-mono text-[11px] text-text-tertiary">
                  static only
                </span>
              )}
            </div>
          </div>
          <div className="text-right">
            <div className="font-mono text-3xl tabular-nums text-text">{formatDuration(elapsed)}</div>
            <div className="font-mono text-[11px] uppercase tracking-wide text-text-tertiary">elapsed</div>
          </div>
        </div>
      </div>

      {job.error && (
        <div className="flex gap-3 rounded-lg border border-danger/30 bg-danger-dim px-4 py-3 text-sm text-danger">
          <Warning size={18} weight="bold" className="mt-0.5 shrink-0" />
          <p className="break-words">{job.error}</p>
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <ol className="rounded-lg border border-border p-5">
          {phases.map((p, i) => (
            <Station
              key={p.key}
              label={p.label}
              index={i}
              total={phases.length}
              state={stationState(i)}
              events={eventsByPhase.get(p.key) ?? []}
            />
          ))}
        </ol>
        <EventFeed events={job.events} running={running} />
      </div>

      {job.counts && (
        <div className="grid grid-cols-2 divide-x divide-border-subtle rounded-lg border border-border sm:grid-cols-4">
          <Count label="Findings" value={job.counts.findings} />
          <Count label="Confirmed by LLM" value={job.counts.confirmed_by_llm} />
          <Count label="Refactoring plans" value={job.counts.refactoring_proposals} />
          <Count label="Errors" value={job.counts.errors} tone={job.counts.errors > 0 ? 'danger' : undefined} />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-4">
        {!running && job.runId && (
          <Link
            to={`/runs/${job.runId}`}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-bg transition-opacity hover:opacity-90"
          >
            View full results
          </Link>
        )}
        {!running && (
          <button
            type="button"
            onClick={onReset}
            className="rounded-md border border-border px-4 py-2 text-sm text-text-secondary transition-colors hover:border-accent/40 hover:text-text"
          >
            Run another analysis
          </button>
        )}
        {job.runId && (
          <span className="font-mono text-[11px] text-text-tertiary">logs/runs/{job.runId}/</span>
        )}
      </div>
    </div>
  )
}

function Count({ label, value, tone }: { label: string; value: number; tone?: 'danger' }) {
  return (
    <div className="px-5 py-4">
      <div className="font-mono text-[11px] uppercase tracking-wide text-text-tertiary">{label}</div>
      <div className={cn('mt-1 font-mono text-2xl', tone === 'danger' ? 'text-danger' : 'text-text')}>{value}</div>
    </div>
  )
}
