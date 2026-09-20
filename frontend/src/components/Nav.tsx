import { NavLink } from 'react-router-dom'
import { cn } from '../lib/cn'

const LINKS = [
  { to: '/', label: 'Overview', end: true },
  { to: '/graph', label: 'Graph' },
  { to: '/detection', label: 'Detection' },
  { to: '/refactoring', label: 'Refactoring' },
  { to: '/runs', label: 'Runs' },
]

export function Nav() {
  return (
    <header className="sticky top-0 z-10 border-b border-border bg-bg/85 backdrop-blur">
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
        <NavLink to="/" className="font-semibold tracking-tight text-text">
          Dependency Graph
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
                  isActive
                    ? 'text-accent'
                    : 'text-text-tertiary hover:text-text',
                )
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  )
}
