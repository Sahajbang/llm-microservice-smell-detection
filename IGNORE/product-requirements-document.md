# Product Requirements Document (PRD)
## LLM-Guided Microservice Code Smell Detection & Autonomous Refactoring

**Document type:** Research prototype PRD (thesis project)
**Status:** Draft — Phase 1 scoping

---

## 1. Overview

This project builds a research prototype that detects architectural smells across microservice boundaries, uses an LLM to reason about whether a detected smell is a genuine problem, proposes a constrained refactoring, applies the change in an isolated environment, and automatically verifies whether the change is safe and effective.

This is explicitly a **research prototype**, not a production-grade autonomous software engineering tool.

---

## 2. Problem Statement / Research Gap

Existing literature has two disconnected strands:

- **Static/architectural smell detection tools** (e.g., DesigniteJava, Arcan, MSANose) can detect microservice-level smells but stop at detection — they don't reason about or perform refactoring.
- **LLM-based refactoring approaches** operate mostly at the class/method level and generally do not reason over cross-service dependencies, nor do they include automated verification of architecture-level changes.

**No existing published work combines cross-service, dependency-aware LLM reasoning with verifiable architecture-level refactoring.** This is the precise gap this project targets.

**Research question:** Can an LLM make better architectural smell detection and refactoring decisions when grounded in static-analysis evidence and a Service Dependency Graph (SDG), and can its proposed cross-service refactorings be automatically verified?

---

## 3. Goals

- Detect architectural smells (starting with **Cyclic Dependency**, then **Hub-like Dependency**) across microservice boundaries using a Service Dependency Graph.
- Ground LLM reasoning in deterministic static-analysis evidence and graph context, rather than treating the LLM as an unquestionable detector.
- Generate constrained, scope-limited refactoring plans — not unrestricted rewrites.
- Automatically verify proposed refactorings via compile/test/re-detection gates before acceptance.
- Produce a reproducible, measurable experimental pipeline suitable for thesis-level evaluation.

## 3.1 Non-Goals

- This system does **not** aim to autonomously refactor arbitrary microservice architectures at production scale.
- This system does **not** attempt to support every known microservice smell in its first version — scope is deliberately limited to two smells initially.
- This system does **not** aim to eliminate human oversight from the research/development process — manual work occurs at build-time (extraction rules, prompt tuning, ground-truth labeling), even though the deployed pipeline runs autonomously per repository at inference time.

---

## 4. Target Users

- Primary: the thesis author and collaborators (Hetarth, Ayush), and thesis evaluators/reviewers.
- Secondary (framing only, not a build target): software architects and engineering teams who might use a mature version of such a tool to monitor and remediate microservice architectural decay.

---

## 5. Proposed Solution — System Overview

**Conceptual pipeline:**

Repository → Evidence Collection → Architectural Representation (SDG) → Smell Detection → LLM Reasoning (Detection Agent) → Constrained Refactoring (Refactoring Agent) → Isolated Application → Automated Verification → Accept/Reject → Evaluation & Logging

**Key design principle:** Static analysis provides deterministic evidence ("something suspicious exists here"); the SDG provides architectural context ("here is how services are connected"); the LLM provides reasoning ("is this actually a problem, why, and what should be done").

See the accompanying System Architecture Diagram for the full component-level breakdown.

---

## 6. Scope by Phase

### Phase 1 (MVP — build first)
- One repository, one smell (Cyclic Dependency).
- Grep/regex-based dependency extraction (Feign, RestTemplate, WebClient, docker-compose).
- NetworkX-based graph construction and cycle detection.
- Single-call LLM detection and refactoring agents (no orchestration framework).
- Manual patch application, compile, and test verification.
- Basic JSON logging of every run.

### Phase 2+ (incremental additions)
- Hub-like Dependency detection.
- Automated patch application and verification loop (replacing manual git apply).
- RAG layer (AST chunking + CodeBERT + FAISS) — introduced only once relevant code no longer fits in context, or evaluation spans multiple repos.
- LangGraph orchestration with bounded retries.
- Cross-validation against established static tools (Arcan, MSANose, DesigniteJava, Code2DFD).
- EvoSuite-generated regression tests.
- Multi-repo evaluation corpus with ground-truth labeling and inter-rater reliability (Cohen's κ).
- MLflow experiment tracking and Scott-Knott ESD statistical comparison.

**Explicitly postponed from Phase 1:** all of the above Phase 2+ items. Reasoning: each introduces either unpredictable integration risk (unmaintained academic tools), unnecessary complexity relative to current scale (RAG, orchestration frameworks), or requires data that doesn't exist yet (multi-repo statistical comparison).

---

## 7. Success Metrics

**Phase 1 success criteria (qualitative, feasibility-focused):**
- End-to-end pipeline runs on at least one repository without manual intervention beyond the patch-application step.
- LLM detection output is structured, parseable JSON in the large majority of runs.
- At least one refactoring proposal is generated, manually applied, and verified to remove the detected cycle without breaking existing tests.

**Later-phase success metrics (quantitative, per thesis methodology):**
- Precision / Recall of smell detection against labeled ground truth.
- Pass@k for refactoring correctness across repeated LLM samples.
- Smell Reduction Rate (SRR) pre/post refactoring.
- Cost and latency per detection/refactoring cycle.
- Statistical significance of improvement over baselines (Scott-Knott ESD).
- Inter-rater reliability of ground-truth labeling (Cohen's κ / Fleiss' κ).

---

## 8. Technical Constraints & Assumptions

- Target codebases: Java, Spring Boot, REST-based inter-service communication (Feign/RestTemplate/WebClient). This is a stated scope boundary, not a temporary limitation — non-REST (gRPC, message-queue-based) architectures are out of scope for the dependency extractor as designed.
- LLM: Claude, called directly via API for Phase 1 (no agent framework yet).
- Refactoring is strictly file/service-scoped; any LLM-proposed change outside the detected smell's affected services is rejected or triggers a bounded retry.
- No refactoring is ever accepted without passing compile, test, and re-detection gates.

---

## 9. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Chosen repo doesn't build/test cleanly out of the box | High — blocks everything downstream | Verify build/tests before any other work begins (Phase 1, Step 1) |
| LLM output non-determinism / malformed JSON | Medium — slows iteration | JSON-repair/retry logic; treat variance as a measured research variable (pass@k), not purely a bug |
| Team has no prior experience with core tools (LLM structured prompting, graph libraries) | Medium — extends timelines | Time-box learning per tool; simplify implementation (e.g., regex over AST) where possible |
| External static-analysis tools (Arcan, MSANose, Code2DFD) are unmaintained/fragile | High if integrated early | Postponed to Phase 2+, used only for cross-validation, not as a Phase 1 dependency |
| Refactoring plan scope creep (LLM proposes changes beyond intended boundary) | Medium — safety concern | Explicit scope-enforcement check before any change is applied |

---

## 10. Open Questions

- Which specific repository(ies) will serve as the primary Phase 1 and later multi-repo evaluation corpus?
- What precise, defensible definition of "smell reduced/eliminated" will be used for the automated re-detection gate (exact match vs. threshold-based)?
- What baseline(s) will the system be compared against for the thesis's comparative evaluation (static-analysis-only? unconstrained LLM refactoring?)?
- At what point will Hub-like Dependency detection be introduced relative to Cyclic Dependency's maturity?

---

## 11. References to Source Material

This PRD is derived from the project's original scoping document (system goals, architecture, tool direction, MVP definition) and subsequent scoping discussion establishing the Phase 1/Phase 2+ split. Any recommendation beyond what was established in the original scoping document is noted as an implementation recommendation, not an established requirement.
