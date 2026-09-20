import { CaretDown } from '@phosphor-icons/react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { useFetch } from '../hooks/useFetch'
import { LoadingBlock, ErrorBlock } from '../components/States'
import { DetectionCard } from './Detection'
import { RefactoringCard } from './Refactoring'
import { detectionStages, refactoringStages } from '../stageUtils'
import type { RunDetail as RunDetailData } from '../types'

export function RunDetail() {
  const { id = '' } = useParams()
  const state = useFetch(() => api.run(id), [id])

  return (
    <div className="mx-auto max-w-7xl px-6 py-12">
      <Link to="/runs" className="text-sm text-accent hover:underline">
        &larr; All runs
      </Link>

      {state.status === 'loading' && (
        <div className="mt-6">
          <LoadingBlock />
        </div>
      )}
      {state.status === 'error' && (
        <div className="mt-6">
          <ErrorBlock message={state.error} />
        </div>
      )}

      {state.status === 'ready' && (
        <div className="mt-4">
          <h1 className="font-mono text-2xl text-text">{state.data.label}</h1>
          <p className="mt-1 font-mono text-sm text-text-tertiary">{state.data.timestamp}</p>

          <div className="mt-10 space-y-6">
            {detectionStages(state.data).map(([key, stage]) => (
              <DetectionCard key={key} stage={stage} />
            ))}
            {refactoringStages(state.data).map(([key, stage]) => (
              <RefactoringCard key={key} stage={stage} />
            ))}
            <OtherStages runDetail={state.data} />
          </div>
        </div>
      )}
    </div>
  )
}

function OtherStages({ runDetail }: { runDetail: RunDetailData }) {
  const recognized = new Set([
    ...detectionStages(runDetail).map(([k]) => k),
    ...refactoringStages(runDetail).map(([k]) => k),
  ])
  const rest = Object.entries(runDetail.stages).filter(([k]) => !recognized.has(k))
  if (rest.length === 0) return null

  return (
    <>
      {rest.map(([key, value]) => (
        <details key={key} className="group rounded-lg border border-border">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 font-mono text-xs uppercase tracking-wide text-text-secondary hover:text-text">
            {key}.json
            <CaretDown size={14} weight="bold" className="shrink-0 text-text-tertiary transition-transform group-open:rotate-180" />
          </summary>
          <pre className="max-h-96 overflow-auto whitespace-pre-wrap border-t border-border bg-bg-elevated-2 p-4 font-mono text-[12.5px] text-text-secondary">
            {JSON.stringify(value, null, 2)}
          </pre>
        </details>
      ))}
    </>
  )
}
