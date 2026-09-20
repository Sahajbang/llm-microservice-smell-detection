import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Check, FolderOpen, GitBranch, MagnifyingGlass, Play } from '@phosphor-icons/react'
import { api } from '../api'
import { useFetch } from '../hooks/useFetch'
import { ErrorBlock, LoadingBlock } from '../components/States'
import { AnalysisProgress } from '../components/AnalysisProgress'
import { cn } from '../lib/cn'
import type { Job, SmellCatalog, SmellSpec } from '../types'

const POLL_MS = 1200

/** Poll one job until it stops running. */
function useJob(jobId: string | undefined): { job: Job | null; error: string | null } {
  const [job, setJob] = useState<Job | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!jobId) {
      setJob(null)
      return
    }
    let cancelled = false
    let timer: number | undefined

    const tick = async () => {
      try {
        const next = await api.job(jobId)
        if (cancelled) return
        setJob(next)
        setError(null)
        if (next.status === 'running') timer = window.setTimeout(tick, POLL_MS)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : String(err))
        timer = window.setTimeout(tick, POLL_MS * 4)
      }
    }
    void tick()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [jobId])

  return { job, error }
}

function SourceCard({
  selected,
  onSelect,
  icon,
  title,
  body,
  children,
}: {
  selected: boolean
  onSelect: () => void
  icon: React.ReactNode
  title: string
  body: string
  children?: React.ReactNode
}) {
  return (
    <div
      className={cn(
        'rounded-lg border p-5 transition-colors',
        selected ? 'border-accent/50 bg-accent-dim/20' : 'border-border bg-bg-elevated/40 hover:border-border',
      )}
    >
      <button type="button" onClick={onSelect} className="flex w-full items-start gap-3 text-left">
        <span
          className={cn(
            'mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md border',
            selected ? 'border-accent/50 bg-accent/15 text-accent' : 'border-border bg-bg-elevated text-text-tertiary',
          )}
        >
          {icon}
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-medium text-text">{title}</span>
          <span className="mt-1 block text-[13px] leading-relaxed text-text-secondary">{body}</span>
        </span>
      </button>
      {selected && children && <div className="mt-4 space-y-3">{children}</div>}
    </div>
  )
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  mono = true,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  mono?: boolean
}) {
  return (
    <label className="block">
      <span className="font-mono text-[11px] uppercase tracking-wide text-text-tertiary">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        className={cn(
          'mt-1.5 w-full rounded-md border border-border bg-bg-sunken px-3 py-2 text-sm text-text outline-none placeholder:text-text-tertiary/70 focus:border-accent/60',
          mono && 'font-mono',
        )}
      />
    </label>
  )
}

function SmellPicker({
  smells,
  selected,
  onToggle,
}: {
  smells: SmellSpec[]
  selected: string[]
  onToggle: (key: string) => void
}) {
  const [query, setQuery] = useState('')

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return smells
    return smells.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.key.toLowerCase().includes(q) ||
        s.definition.toLowerCase().includes(q),
    )
  }, [smells, query])

  return (
    <div className="space-y-3">
      <div className="relative">
        <MagnifyingGlass
          size={15}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary"
        />
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search smells — try 'cycle', 'database', 'coupling'"
          className="w-full rounded-md border border-border bg-bg-sunken py-2 pl-9 pr-3 text-sm text-text outline-none placeholder:text-text-tertiary/70 focus:border-accent/60"
        />
      </div>

      {matches.length === 0 ? (
        <p className="px-1 text-[13px] text-text-tertiary">
          No detector matches &ldquo;{query}&rdquo;. Only smells with real detector code behind them are listed.
        </p>
      ) : (
        <ul className="space-y-2">
          {matches.map((s) => {
            const on = selected.includes(s.key)
            return (
              <li key={s.key}>
                <button
                  type="button"
                  onClick={() => onToggle(s.key)}
                  aria-pressed={on}
                  className={cn(
                    'flex w-full items-start gap-3 rounded-md border p-3 text-left transition-colors',
                    on ? 'border-accent/50 bg-accent-dim/20' : 'border-border hover:border-border',
                  )}
                >
                  <span
                    className={cn(
                      'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border',
                      on ? 'border-accent bg-accent text-bg' : 'border-border',
                    )}
                  >
                    {on && <Check size={11} weight="bold" />}
                  </span>
                  <span className="min-w-0">
                    <span className="flex flex-wrap items-baseline gap-2">
                      <span className="text-sm font-medium text-text">{s.name}</span>
                      <span className="font-mono text-[11px] text-text-tertiary">{s.key}</span>
                    </span>
                    <span className="mt-1 block line-clamp-2 text-[13px] leading-relaxed text-text-secondary">
                      {s.definition}
                    </span>
                    {s.limits && (
                      <span className="mt-1.5 block text-[11px] leading-relaxed text-text-tertiary">
                        Limit: {s.limits}
                      </span>
                    )}
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function Setup({ catalog, onStarted }: { catalog: SmellCatalog; onStarted: (job: Job) => void }) {
  const [mode, setMode] = useState<'local' | 'git'>(catalog.defaultLocalRepo ? 'local' : 'git')
  const [localPath, setLocalPath] = useState(catalog.defaultLocalRepo ?? 'target-repo')
  const [url, setUrl] = useState('')
  const [branch, setBranch] = useState('')
  const [scope, setScope] = useState<'all' | 'select'>('all')
  const [selected, setSelected] = useState<string[]>([])
  const [llm, setLlm] = useState(catalog.llmAvailable)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const source = mode === 'local' ? localPath : url
  const smells = scope === 'all' ? undefined : selected
  const canStart = source.trim().length > 0 && (scope === 'all' || selected.length > 0) && !submitting

  const start = async () => {
    setSubmitting(true)
    setError(null)
    try {
      const job = await api.analyze({
        source,
        smells,
        llm,
        branch: mode === 'git' && branch.trim() ? branch.trim() : null,
        runName: mode === 'git' ? 'dashboard-git' : 'dashboard',
      })
      onStarted(job)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setSubmitting(false)
    }
  }

  return (
    <div className="space-y-10">
      <section>
        <SectionHeading step={1} title="Choose a repository" />
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <SourceCard
            selected={mode === 'local'}
            onSelect={() => setMode('local')}
            icon={<FolderOpen size={16} weight="bold" />}
            title="Bundled target repository"
            body="Analyze the Spring Boot microservices repo checked into this project. No network access, fastest path to a result."
          >
            <Field label="Path (inside the project)" value={localPath} onChange={setLocalPath} />
          </SourceCard>

          <SourceCard
            selected={mode === 'git'}
            onSelect={() => setMode('git')}
            icon={<GitBranch size={16} weight="bold" />}
            title="Git repository URL"
            body="Shallow-clone any public HTTPS repository into a temporary workspace, analyze it, then delete the clone."
          >
            <Field
              label="Repository URL"
              value={url}
              onChange={setUrl}
              placeholder="https://github.com/owner/repo.git"
            />
            <Field label="Branch (optional)" value={branch} onChange={setBranch} placeholder="main" />
          </SourceCard>
        </div>
      </section>

      <section>
        <SectionHeading step={2} title="Choose what to look for" />
        <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
          <SourceCard
            selected={scope === 'all'}
            onSelect={() => setScope('all')}
            icon={<Play size={16} weight="fill" />}
            title="Full analysis"
            body={`Run every detector (${catalog.smells.map((s) => s.name).join(', ')}) over the same extracted evidence.`}
          />
          <SourceCard
            selected={scope === 'select'}
            onSelect={() => setScope('select')}
            icon={<MagnifyingGlass size={16} weight="bold" />}
            title="Specific smells"
            body="Search the detector catalogue and run only the ones you pick — useful for isolating one signal."
          >
            <SmellPicker
              smells={catalog.smells}
              selected={selected}
              onToggle={(key) =>
                setSelected((prev) => (prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]))
              }
            />
          </SourceCard>
        </div>
      </section>

      <section>
        <SectionHeading step={3} title="LLM validation" />
        <div className="mt-4 rounded-lg border border-border p-5">
          <label className="flex cursor-pointer items-start gap-3">
            <input
              type="checkbox"
              checked={llm}
              disabled={!catalog.llmAvailable}
              onChange={(e) => setLlm(e.target.checked)}
              className="mt-1 h-4 w-4 accent-accent"
            />
            <span>
              <span className="block text-sm font-medium text-text">
                Send each finding to the LLM for validation and a refactoring proposal
              </span>
              <span className="mt-1 block text-[13px] leading-relaxed text-text-secondary">
                Off, the run is purely deterministic: detectors only, no API calls, results in seconds. On, every
                candidate gets one validation call and every confirmed finding one refactoring call.
              </span>
              {!catalog.llmAvailable && (
                <span className="mt-2 block font-mono text-[11px] text-warning">
                  NVIDIA_API_KEY is not set, so LLM validation is unavailable. Set it in .env and restart the API.
                </span>
              )}
            </span>
          </label>
        </div>
      </section>

      {error && (
        <div className="rounded-lg border border-danger/30 bg-danger-dim px-4 py-3 text-sm text-danger">{error}</div>
      )}

      <div className="flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={start}
          disabled={!canStart}
          className={cn(
            'inline-flex items-center gap-2 rounded-md px-5 py-2.5 text-sm font-medium transition-opacity',
            canStart ? 'bg-accent text-bg hover:opacity-90' : 'cursor-not-allowed bg-bg-elevated-2 text-text-tertiary',
          )}
        >
          <Play size={15} weight="fill" />
          {submitting ? 'Starting…' : 'Start analysis'}
        </button>
        <span className="font-mono text-[11px] text-text-tertiary">
          equivalent: python -m pipeline.run_pipeline {source || '<repo>'}
          {smells ? ` --smells ${smells.join(' ')}` : ''}
          {llm ? '' : ' --no-llm'}
        </span>
      </div>
    </div>
  )
}

function SectionHeading({ step, title }: { step: number; title: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="flex h-6 w-6 items-center justify-center rounded-md border border-border bg-bg-elevated font-mono text-[11px] text-text-tertiary">
        {step}
      </span>
      <h2 className="text-lg font-semibold text-text">{title}</h2>
    </div>
  )
}

export function Analyze() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const catalog = useFetch(api.smells, [])
  const { job, error } = useJob(jobId)

  return (
    <div className="mx-auto max-w-5xl px-6 py-12">
      <h1 className="text-3xl font-semibold tracking-tight text-text">New analysis</h1>
      <p className="mt-3 max-w-2xl text-text-secondary">
        Point the pipeline at a repository, pick the smells to look for, and watch each stage of the run as it
        happens. Every run writes a full, timestamped log under <span className="font-mono text-text">logs/runs/</span>.
      </p>

      <div className="mt-10">
        {jobId ? (
          job ? (
            <AnalysisProgress
              job={job}
              phases={catalog.status === 'ready' ? catalog.data.phases : []}
              onReset={() => navigate('/analyze')}
            />
          ) : error ? (
            <ErrorBlock message={error} />
          ) : (
            <LoadingBlock />
          )
        ) : catalog.status === 'loading' ? (
          <LoadingBlock />
        ) : catalog.status === 'error' ? (
          <ErrorBlock message={catalog.error} />
        ) : (
          <Setup catalog={catalog.data} onStarted={(j) => navigate(`/analyze/${j.id}`)} />
        )}
      </div>
    </div>
  )
}
