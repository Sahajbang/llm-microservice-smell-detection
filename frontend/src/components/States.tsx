export function LoadingBlock() {
  return (
    <div className="space-y-3" aria-busy="true" aria-label="Loading">
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-16 animate-pulse rounded-lg border border-border-subtle bg-bg-elevated" />
      ))}
    </div>
  )
}

export function ErrorBlock({ message }: { message: string }) {
  return (
    <div className="rounded-lg border border-danger/30 bg-danger-dim px-4 py-3 text-sm text-danger">
      <p className="font-mono text-xs uppercase tracking-wide">Failed to load</p>
      <p className="mt-1 text-danger/90">{message}</p>
      <p className="mt-2 text-xs text-danger/70">
        Is the API running? From the repo root: <code className="font-mono">python -m uvicorn api.main:app --reload --port 8000</code>
      </p>
    </div>
  )
}

export function EmptyBlock({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border px-6 py-10 text-center">
      <p className="text-sm font-medium text-text-secondary">{title}</p>
      <p className="mx-auto mt-1.5 max-w-md text-sm text-text-tertiary">{body}</p>
    </div>
  )
}
