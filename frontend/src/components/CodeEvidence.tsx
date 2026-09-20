import type { Edge } from '../types'
import { SourceBadge } from './Badges'

export function CodeEvidence({ edge }: { edge: Edge }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="flex items-center justify-between gap-3 border-b border-border bg-bg-elevated px-3 py-2">
        <span className="truncate font-mono text-xs text-text-secondary" title={edge.file}>
          {edge.file}
          {edge.line > 0 && <span className="text-text-tertiary">:{edge.line}</span>}
        </span>
        <SourceBadge source={edge.source} />
      </div>
      <pre className="whitespace-pre-wrap break-all bg-bg-elevated-2 px-3 py-2.5 font-mono text-[13px] leading-relaxed text-text">
        <code>{edge.evidence}</code>
      </pre>
    </div>
  )
}
