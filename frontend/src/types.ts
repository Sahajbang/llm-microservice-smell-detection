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

/** A deterministic detector's candidate, plus whatever the LLM concluded. */
export interface Finding {
  smell: string
  smell_name: string
  key: string
  services: string[]
  files: string[]
  severity: Severity
  confidence: number
  description: string
  evidence: Edge[]
  metrics: Record<string, unknown>
  llmDetection: (DetectionResult & { smell_key?: string; finding_key?: string }) | null
  refactoring: RefactoringResult | null
  errors: PipelineError[]
}

export interface PipelineError {
  stage: string
  component: string
  message: string
  detail: string | null
}

export interface RepositoryInfo {
  source: string
  kind: 'git' | 'local'
  root: string
  name: string
  branch: string | null
  commit: string | null
  remote_url: string | null
  is_temporary: boolean
}

export interface RunCounts {
  findings: number
  findings_by_smell: Record<string, number>
  confirmed_by_llm: number
  refactoring_proposals: number
  errors: number
}

export interface RunSummary {
  id: string
  timestamp: string
  label: string
  files: string[]
  /** `pipeline` runs carry findings/counts; `legacy` runs are Phase 1 logs. */
  kind: 'pipeline' | 'legacy'
  cycle: string[] | null
  detection: DetectionResult | null
  refactoring: RefactoringResult | null
  scopeOk: boolean | null
  repository: RepositoryInfo | null
  counts: RunCounts | null
  findings: Finding[]
  detectorsRun: string[]
  llmEnabled: boolean | null
  errors: PipelineError[]
}

export interface RunDetail {
  id: string
  timestamp: string
  label: string
  stages: Record<string, unknown>
  summary: RunSummary
}

export interface SmellSpec {
  key: string
  name: string
  definition: string
  evidence: string
  limits: string
}

export interface SmellCatalog {
  smells: SmellSpec[]
  llmAvailable: boolean
  llmEnabledByDefault: boolean
  defaultLocalRepo: string | null
  phases: { key: string; label: string }[]
}

export interface JobEvent {
  at: string
  phase: string
  label: string
  detail: string | null
}

export interface Job {
  id: string
  source: string
  smells: string[]
  llm: boolean
  branch: string | null
  status: 'running' | 'done' | 'failed'
  startedAt: string
  finishedAt: string | null
  runId: string | null
  phase: string
  events: JobEvent[]
  counts: RunCounts | null
  error: string | null
}

export interface Overview {
  services: string[]
  edgeCount: number
  sourceBreakdown: Record<string, number>
  cycles: Cycle[]
  runCount: number
  latestRun: RunSummary | null
}
