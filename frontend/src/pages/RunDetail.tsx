import { CaretDown } from '@phosphor-icons/react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { useFetch } from '../hooks/useFetch'
import { LoadingBlock, ErrorBlock } from '../components/States'
import { DetectionCard, FindingsList } from './Detection'
import { RefactoringCard } from './Refactoring'
import { detectionStages, refactoringStages } from '../stageUtils'
import type { RunDetail as RunDetailData } from '../types'

function RunHeader({ run }: { run: RunDetailData }) {
  const { summary } = run
  const repo = summary.repository
  return (
    <>
      <h1 className="font-mono text-2xl text-text">{repo?.name ?? summary.label}</h1>
      <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 font-mono text-xs text-text-tertiary">
        <span>{summary.timestamp}</span>
        {repo && (
          <span>
            {repo.kind}
            {repo.branch ? ` · ${repo.branch}` : ''}
            {repo.commit ? ` · ${repo.commit.slice(0, 10)}` : ''}
          </span>
        )}
        {repo?.remote_url && <span className="truncate">{repo.remote_url}</span>}
        {summary.detectorsRun.length > 0 && <span>detectors: {summary.detectorsRun.join(', ')}</span>}
        {summary.llmEnabled === false && <span className="text-warning">LLM disabled</span>}
      </div>
      {summary.counts && (
        <div className="mt-6 grid grid-cols-2 divide-x divide-border-subtle rounded-lg border border-border sm:grid-cols-4">
          {[
            ['Findings', summary.counts.findings],
            ['Confirmed by LLM', summary.counts.confirmed_by_llm],
            ['Refactoring plans', summary.counts.refactoring_proposals],
            ['Errors', summary.counts.errors],
          ].map(([label, value]) => (
            <div key={label as string} className="px-5 py-4">
              <div className="font-mono text-[11px] uppercase tracking-wide text-text-tertiary">{label}</div>
              <div className="mt-1 font-mono text-2xl text-text">{value}</div>
            </div>
          ))}
        </div>
      )}
      {summary.errors.length > 0 && (
        <ul className="mt-4 space-y-1">
          {summary.errors.map((e, i) => (
            <li key={i} className="rounded-md border border-warning/30 bg-warning-dim px-3 py-2 text-xs text-warning">
              <span className="font-mono uppercase">{e.stage}/{e.component}</span> {e.message}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

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
          <RunHeader run={state.data} />

          <div className="mt-10 space-y-6">
            {state.data.summary.kind === 'pipeline' ? (
              <FindingsList run={state.data} showRefactoring />
            ) : (
              <>
                {detectionStages(state.data).map(([key, stage]) => (
                  <DetectionCard key={key} stage={stage} />
                ))}
                {refactoringStages(state.data).map(([key, stage]) => (
                  <RefactoringCard key={key} stage={stage} />
                ))}
              </>
            )}
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
