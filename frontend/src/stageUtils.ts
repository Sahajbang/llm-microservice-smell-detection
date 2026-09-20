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
