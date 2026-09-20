import { describe, expect, it } from 'vitest'
import { detectionStageFor, refactoringStageFor } from './stageUtils'
import type { RunDetail } from './types'

/** Finding keys contain a ':' that the run logger rewrites to '-' when it
 *  names the stage file, so looking a trace up by the raw key must fail. */
const KEY = 'cyclic_dependency:customers-service-visits-service'
const SLUG = 'cyclic_dependency-customers-service-visits-service'

function runWith(stages: Record<string, unknown>): RunDetail {
  return { id: 'r', timestamp: 't', label: 'l', stages } as RunDetail
}

describe('stage lookup by finding key', () => {
  it('finds a detection trace under the slugified stage name', () => {
    const run = runWith({
      [`detection_${SLUG}`]: { result: { detected: true }, attempts: [] },
    })
    expect(detectionStageFor(run, KEY)).toBeDefined()
  })

  it('finds a refactoring trace under the slugified stage name', () => {
    const run = runWith({
      [`refactoring_${SLUG}`]: { result: { affected_files: [] }, attempts: [] },
    })
    expect(refactoringStageFor(run, KEY)).toBeDefined()
  })

  it('returns undefined when the run logged no trace for that finding', () => {
    const run = runWith({ [`detection_${SLUG}`]: { result: { detected: true }, attempts: [] } })
    expect(detectionStageFor(run, 'hub_dependency:api-gateway')).toBeUndefined()
    expect(refactoringStageFor(run, KEY)).toBeUndefined()
  })

  it('does not mistake a detection stage for a refactoring one', () => {
    const run = runWith({ [`refactoring_${SLUG}`]: { result: { detected: true }, attempts: [] } })
    expect(refactoringStageFor(run, KEY)).toBeUndefined()
  })
})
