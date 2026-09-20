import type { DetectionStage, RefactoringStage, RunDetail } from './types'

function isObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object'
}

/** Detection and refactoring stages share a {cycle, evidence, system_prompt, attempts, result} shape;
 *  they're told apart by a field unique to each stage's `result`. */
function isDetectionStage(v: unknown): v is DetectionStage {
  if (!isObject(v) || !isObject(v.result) || !Array.isArray(v.attempts)) return false
  return 'detected' in v.result
}

function isRefactoringStage(v: unknown): v is RefactoringStage {
  if (!isObject(v) || !isObject(v.result) || !Array.isArray(v.attempts)) return false
  return 'affected_files' in v.result
}

export function detectionStages(run: RunDetail | null | undefined): [string, DetectionStage][] {
  if (!run) return []
  return Object.entries(run.stages).filter(
    (entry): entry is [string, DetectionStage] => isDetectionStage(entry[1]),
  )
}

export function refactoringStages(run: RunDetail | null | undefined): [string, RefactoringStage][] {
  if (!run) return []
  return Object.entries(run.stages).filter(
    (entry): entry is [string, RefactoringStage] => isRefactoringStage(entry[1]),
  )
}

/** Mirror of `pipeline.run_logger._slugify`, which turns a stage name into a
 *  filename. Finding keys contain a ':' that becomes '-' on disk, so a
 *  lookup by raw key would never match. */
function slugify(name: string): string {
  return name.replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').toLowerCase() || 'run'
}

/** The LLM traces a multi-smell run logged for one finding, if it has any.
 *  The orchestrator names these `detection_<key>` / `refactoring_<key>`. */
export function detectionStageFor(run: RunDetail | null | undefined, key: string): DetectionStage | undefined {
  const value = run?.stages[slugify(`detection_${key}`)]
  return isDetectionStage(value) ? value : undefined
}

export function refactoringStageFor(run: RunDetail | null | undefined, key: string): RefactoringStage | undefined {
  const value = run?.stages[slugify(`refactoring_${key}`)]
  return isRefactoringStage(value) ? value : undefined
}
