import { CaretDown } from '@phosphor-icons/react'
import type { LlmAttempt } from '../types'
import { StatusPill } from './Badges'

function Block({ label, text }: { label: string; text: string }) {
  return (
    <div>
      <div className="mb-1.5 font-mono text-[11px] uppercase tracking-wide text-text-tertiary">{label}</div>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-bg-elevated-2 p-3 font-mono text-[12.5px] leading-relaxed text-text-secondary">
        {text}
      </pre>
    </div>
  )
}

export function ReasoningTrace({
  systemPrompt,
  attempts,
}: {
  systemPrompt: string
  attempts: LlmAttempt[]
}) {
  return (
    <details className="group rounded-lg border border-border">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 font-mono text-xs uppercase tracking-wide text-text-secondary transition-colors hover:text-text">
        <span>
          LLM reasoning trace &middot; {attempts.length} attempt{attempts.length === 1 ? '' : 's'}
        </span>
        <CaretDown
          size={14}
          weight="bold"
          className="shrink-0 text-text-tertiary transition-transform group-open:rotate-180"
        />
      </summary>
      <div className="space-y-6 border-t border-border px-4 py-4">
        <Block label="System prompt" text={systemPrompt} />
        {attempts.map((a) => (
          <div key={a.attempt} className="space-y-3 border-t border-border-subtle pt-4 first:border-t-0 first:pt-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-xs text-text-secondary">Attempt {a.attempt}</span>
              <span className="font-mono text-xs text-text-tertiary">{a.model}</span>
              <StatusPill ok={a.parse_ok} yes="parsed" no="parse failed" />
              {a.scope_ok !== undefined && <StatusPill ok={a.scope_ok} yes="in scope" no="out of scope" />}
            </div>
            <Block label="Prompt" text={a.prompt} />
            {a.reasoning && <Block label="Reasoning" text={a.reasoning} />}
            <Block label="Raw response" text={a.raw_response} />
          </div>
        ))}
      </div>
    </details>
  )
}
