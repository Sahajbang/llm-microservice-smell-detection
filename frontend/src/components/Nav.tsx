import { useEffect, useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { Play } from '@phosphor-icons/react'
import { api } from '../api'
import { cn } from '../lib/cn'
import type { Job } from '../types'

const LINKS = [
  { to: '/', label: 'Overview', end: true },
  { to: '/graph', label: 'Graph' },
  { to: '/detection', label: 'Detection' },
  { to: '/refactoring', label: 'Refactoring' },
  { to: '/runs', label: 'Runs' },
]

/** Poll for a running analysis so it stays reachable from any page. */
function useActiveJob(): Job | null {
  const [job, setJob] = useState<Job | null>(null)
  useEffect(() => {
    let cancelled = false
    const tick = async () => {
      try {
        const { active } = await api.jobs()
        if (!cancelled) setJob(active)
      } catch {
        if (!cancelled) setJob(null)
      }
    }
    void tick()
    const id = setInterval(tick, 4000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])
  return job
}

export function Nav() {
  const activeJob = useActiveJob()

  return (
    <header className="sticky top-0 z-10 border-b border-border bg-bg/85 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between gap-4 px-6">
        <NavLink to="/" className="shrink-0 font-semibold tracking-tight text-text">
          Microservice Smells
        </NavLink>
        <nav className="flex items-center gap-1">
          {LINKS.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.end}
              className={({ isActive }) =>
                cn(
                  'rounded-md px-3 py-1.5 font-mono text-[13px] uppercase tracking-wide transition-colors',
                  isActive ? 'text-accent' : 'text-text-tertiary hover:text-text',
                )
              }
            >
              {link.label}
            </NavLink>
          ))}

          {activeJob ? (
            <Link
              to={`/analyze/${activeJob.id}`}
              className="ml-2 inline-flex items-center gap-2 rounded-md border border-accent/40 bg-accent-dim/40 px-3 py-1.5 font-mono text-[12px] uppercase tracking-wide text-accent"
            >
              <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />
              {activeJob.phase}
            </Link>
          ) : (
            <NavLink
              to="/analyze"
              className="ml-2 inline-flex items-center gap-2 rounded-md bg-accent px-3 py-1.5 font-mono text-[12px] uppercase tracking-wide text-bg transition-opacity hover:opacity-90"
            >
              <Play size={12} weight="fill" />
              New analysis
            </NavLink>
          )}
        </nav>
      </div>
    </header>
  )
}
