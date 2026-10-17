export type EdgeSource =
  | 'feign'
  | 'resttemplate'
  | 'webclient'
  | 'discovery-client'
  | 'http-literal'
  | 'gateway-route'
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
  /** Present only when the run generated a patch (apply mode other than `never`). */
  patch?: PatchResult | null
  errors: PipelineError[]
}

export type ApplyMode = 'never' | 'dry-run' | 'ask' | 'auto'
export type FailurePolicy = 'rollback' | 'keep'
export type VerifyLevel = 'none' | 'compile' | 'test'
export type PatchStatus = 'proposed' | 'rejected' | 'verified' | 'rolled_back' | 'failed_kept' | 'error'

export interface PatchFile {
  path: string
  action: 'modify' | 'create' | 'delete'
}

export interface BuildRun {
  build_system: string
  cwd: string
  command?: string
  returncode?: number | null
  /** null = the build did not run (tool missing, or already failing before the patch). */
  passed: boolean | null
  duration_s?: number
  output_tail?: string
  skipped?: string
}

export interface Verification {
  passed: boolean
  build: {
    status: 'passed' | 'failed' | 'skipped'
    level: VerifyLevel
    reason: string | null
    runs: BuildRun[]
  }
  redetection: {
    resolved: boolean
    still_detected: boolean
    remaining_findings: string[]
    errors: string[]
  }
}

export interface PatchResult {
  status: PatchStatus
  files: PatchFile[]
  diff: string
  decision: string | null
  verification: Verification | null
  commit: string | null
  branch: string | null
  failure_policy?: FailurePolicy
  error?: string
}

export interface RefactorSummary {
  mode: ApplyMode
  on_failure: FailurePolicy
  verify_level: VerifyLevel
  /** Set only when the branch was kept, i.e. something was committed to it. */
  branch?: string | null
  base_commit?: string
  commits?: string[]
  inspect?: string
  clone_kept_at?: string
  error?: string
}

/** The question an `ask`-mode run is blocked on. */
export interface PendingDecision {
  id: string
  kind: 'apply' | 'failure'
  options: string[]
  default: string
  finding_key: string
  smell_name: string
  services: string[]
  files: PatchFile[]
  diff: string
  rationale?: string | null
  verification?: Verification
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
  patches_by_status?: Partial<Record<PatchStatus, number>>
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
  refactor?: RefactorSummary | null
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
  applyMode: ApplyMode
  onFailure: FailurePolicy
  verifyLevel: VerifyLevel
  status: 'running' | 'done' | 'failed'
  startedAt: string
  finishedAt: string | null
  runId: string | null
  phase: string
  events: JobEvent[]
  counts: RunCounts | null
  refactor: RefactorSummary | null
  pendingDecision: PendingDecision | null
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
