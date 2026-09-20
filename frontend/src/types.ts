export type EdgeSource =
  | 'feign'
  | 'resttemplate'
  | 'webclient'
  | 'discovery-client'
  | 'http-literal'
  | 'docker-compose'

export interface Edge {
  caller: string
  callee: string
  source: EdgeSource
  file: string
  line: number
  evidence: string
}

export interface Cycle {
  smell: string
  cycle: string[]
}

export interface GraphResponse {
  services: string[]
  edges: Edge[]
  cycles: Cycle[]
}

export type Severity = 'LOW' | 'MEDIUM' | 'HIGH'

export interface DetectionResult {
  smell: string
  detected: boolean
  confidence: number
  severity: Severity
  rationale: string
  refactoring_recommended: boolean
}

export interface RefactoringChange {
  file: string
  method_or_class: string
  description: string
}

export interface RefactoringResult {
  smell: string
  affected_files: string[]
  changes: RefactoringChange[]
  rationale: string
  expected_impact: string
}

export interface LlmAttempt {
  attempt: number
  prompt: string
  raw_response: string
  reasoning?: string
  model: string
  parse_ok: boolean
  scope_ok?: boolean
}

export interface DetectionStage {
  cycle: string[]
  evidence: Edge[]
  system_prompt: string
  attempts: LlmAttempt[]
  result: DetectionResult
}

export interface RefactoringStage {
  cycle: string[]
  evidence: Edge[]
  detection: DetectionResult & { cycle: string[] }
  allowed_module_prefixes: string[]
  system_prompt: string
  attempts: LlmAttempt[]
  result: RefactoringResult
}

export interface RunSummary {
  id: string
  timestamp: string
  label: string
  files: string[]
  cycle: string[] | null
  detection: DetectionResult | null
  refactoring: RefactoringResult | null
  scopeOk: boolean | null
}

export interface RunDetail {
  id: string
  timestamp: string
  label: string
  stages: Record<string, unknown>
}

export interface Overview {
  services: string[]
  edgeCount: number
  sourceBreakdown: Record<string, number>
  cycles: Cycle[]
  runCount: number
  latestRun: RunSummary | null
}
