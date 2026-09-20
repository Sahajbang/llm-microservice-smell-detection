export function CyclePath({ cycle, tone = 'danger' }: { cycle: string[]; tone?: 'danger' | 'default' }) {
  return (
    <p className="font-mono text-sm text-text">
      {cycle.map((node, i) => (
        <span key={i}>
          {i > 0 && <span className={`mx-2 ${tone === 'danger' ? 'text-danger' : 'text-text-tertiary'}`}>&rarr;</span>}
          {node}
        </span>
      ))}
    </p>
  )
}
