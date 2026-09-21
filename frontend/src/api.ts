import type { GraphResponse, Job, Overview, RunDetail, RunSummary, SmellCatalog } from './types'

/** Carries the HTTP status so callers can tell a permanent failure (404: this
 *  resource is gone) from a transient one (network blip, 5xx) worth retrying. */
export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
    // FastAPI reports validation/conflict failures as {"detail": "..."};
    // surface that instead of a bare status code.
    const detail = await res
      .json()
      .then((b: { detail?: string }) => b?.detail)
      .catch(() => undefined)
    throw new ApiError(detail ?? `${path} responded ${res.status}`, res.status)
  }
  return res.json() as Promise<T>
}

export interface AnalyzeRequest {
  source: string
  smells?: string[]
  llm?: boolean
  branch?: string | null
  runName?: string
}

export const api = {
  overview: () => request<Overview>('/api/overview'),
  graph: (runId?: string) =>
    request<GraphResponse>(runId ? `/api/graph?run=${encodeURIComponent(runId)}` : '/api/graph'),
  runs: () => request<RunSummary[]>('/api/runs'),
  run: (id: string) => request<RunDetail>(`/api/runs/${encodeURIComponent(id)}`),
  latestRunDetail: async (): Promise<RunDetail | null> => {
    const runs = await request<RunSummary[]>('/api/runs')
    if (runs.length === 0) return null
    return request<RunDetail>(`/api/runs/${encodeURIComponent(runs[0].id)}`)
  },

  smells: () => request<SmellCatalog>('/api/smells'),
  analyze: (body: AnalyzeRequest) =>
    request<Job>('/api/analyze', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  job: (id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  jobs: () => request<{ active: Job | null; recent: Job[] }>('/api/jobs'),
}
