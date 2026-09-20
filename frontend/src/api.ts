import type { GraphResponse, Overview, RunDetail, RunSummary } from './types'

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) {
    throw new Error(`${path} responded ${res.status}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  overview: () => getJson<Overview>('/api/overview'),
  graph: () => getJson<GraphResponse>('/api/graph'),
  runs: () => getJson<RunSummary[]>('/api/runs'),
  run: (id: string) => getJson<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),
  latestRunDetail: async (): Promise<RunDetail | null> => {
    const runs = await getJson<RunSummary[]>('/api/runs')
    if (runs.length === 0) return null
    return getJson<RunDetail>(`/api/runs/${encodeURIComponent(runs[0].id)}`)
  },
}
