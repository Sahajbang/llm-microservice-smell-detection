import type { Severity } from '../types'
import { cn } from '../lib/cn'

const SEVERITY_STYLES: Record<Severity, string> = {
  HIGH: 'text-danger bg-danger-dim border-danger/30',
  MEDIUM: 'text-warning bg-warning-dim border-warning/30',
  LOW: 'text-text-secondary bg-bg-elevated-2 border-border',
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-0.5 font-mono text-xs uppercase tracking-wide',
        SEVERITY_STYLES[severity],
      )}
    >
      {severity}
    </span>
  )
}

export function StatusPill({ ok, yes, no }: { ok: boolean | null; yes: string; no: string }) {
  if (ok === null) {
    return (
      <span className="inline-flex items-center rounded-full border border-border bg-bg-elevated-2 px-2.5 py-0.5 font-mono text-xs uppercase tracking-wide text-text-tertiary">
        unknown
      </span>
    )
  }
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full border px-2.5 py-0.5 font-mono text-xs uppercase tracking-wide',
        ok ? 'border-ok/30 bg-ok-dim text-ok' : 'border-danger/30 bg-danger-dim text-danger',
      )}
    >
      {ok ? yes : no}
    </span>
  )
}

const SOURCE_LABELS: Record<string, string> = {
  feign: 'Feign',
  resttemplate: 'RestTemplate',
  webclient: 'WebClient',
  'discovery-client': 'DiscoveryClient',
  'http-literal': 'HTTP literal',
  'docker-compose': 'docker-compose',
}

export function SourceBadge({ source }: { source: string }) {
  return (
    <span className="inline-flex items-center rounded-full border border-border bg-bg-elevated-2 px-2.5 py-0.5 font-mono text-[11px] text-text-secondary">
      {SOURCE_LABELS[source] ?? source}
    </span>
  )
}
