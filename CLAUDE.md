# Implementation Guide
## LLM-Guided Microservice Code Smell Detection & Autonomous Refactoring

This guide sequences the project from a zero-code state to the full research vision. **Phase 1 (Steps 1–7)** is the achievable MVP for a first working end-to-end loop. **Phase 2+ (Step 8 onward)** lists the remaining components from the full architecture, to be added incrementally once Phase 1 works.

Scope for Phase 1: **one smell (Cyclic Dependency)**, **one repository**, grep-based extraction, no orchestration framework, manual patch application.

---

## Phase 1 — MVP (Build This First)

### Step 1: Select & Verify a Repository

**Goal:** Have a known-good Java Spring Boot microservices repo to work against.

**Why it's necessary:** The single most common time-sink in projects like this is discovering, mid-build, that the repo itself doesn't compile or has pre-existing flaky tests — wasting time on something unrelated to your actual system.

**What to do:**
- Choose a small (4–8 service) Spring Boot microservices sample repository that uses Feign clients or RestTemplate for inter-service calls, and has a `docker-compose.yml`.
- Clone it locally.
- Run `mvn clean install` and `mvn test` on the **untouched** repo.

**Input/Output:** Input: repo URL. Output: confirmation that build + tests are green.

**How to test independently:** The build/test commands either pass or they don't — no ambiguity.

**Failure risks:** Stale dependencies, wrong JDK version, tests already broken before you touch anything. If this repo fails, pick a different one rather than trying to fix an unrelated codebase's pre-existing issues.

---

### Step 2: Build the Dependency Extractor

**Goal:** Automatically produce a list of service-to-service dependency edges from source code and config.

**Why it's necessary:** This is the raw evidence the Service Dependency Graph is built from. Without it, there's no graph and no smell detection.

**Simplest reliable implementation:** Regex/grep-based scanning — not AST parsing — for:
- `@FeignClient(name = "...")` annotations
- `RestTemplate.exchange(...)` / `.getForObject(...)` calls where the URL references another service's name
- `WebClient` calls with similar patterns
- `docker-compose.yml` `depends_on` blocks as a secondary edge source

**Tool:** Python, `re` module, `pyyaml` for parsing `docker-compose.yml`.

**Connects to:** Feeds Step 3 (Graph Construction).

**Input/Output:**
- Input: path to repo root
- Output: JSON list of edges, e.g. `[{"caller": "order-service", "callee": "payment-service", "source": "feign"}]`

**How to test independently:** Run it against the repo, manually inspect the output edge list against what you know the repo's actual service calls are (you can verify by reading a few service files directly).

**Failure risks:** Missing non-standard HTTP client wrappers; false positives from string matches that aren't actually service calls; repo using gRPC or message queues instead of REST (out of scope for this extractor — document this limitation explicitly rather than trying to handle it).

---

### Step 3: Build the Graph & Detect Cyclic Dependency

**Goal:** Represent the extracted edges as a graph and detect cycles.

**Why it's necessary:** This is the core static-analysis signal — the "something suspicious exists here" evidence that grounds the LLM's reasoning.

**Simplest reliable implementation:** Load the edge list into a `networkx.DiGraph`, run `list(nx.simple_cycles(graph))`.

**Tool:** NetworkX.

**Connects to:** Takes Step 2's output; feeds Step 4 (LLM Detection).

**Input/Output:**
- Input: edge list JSON
- Output: `{"smell": "Cyclic Dependency", "cycle": ["order-service", "payment-service", "order-service"]}` if a cycle exists, else empty.

**How to test independently:** Construct a small synthetic edge list with a known cycle and confirm the function finds it; then run on your real repo's extracted edges.

**Failure risks:** None significant — this is deterministic graph theory, low risk.

---

### Step 4: LLM Detection Agent

**Goal:** Have Claude confirm the cycle is a real architectural problem, and explain why, in structured JSON.

**Why it's necessary:** Static detection alone doesn't tell you severity, context-specific impact, or whether the cycle is actually a problem in this specific case — that's the reasoning layer the thesis is centered on.

**Simplest reliable implementation:** One direct API call (no agent framework). Prompt includes: the smell definition, the cycle path, and the relevant code snippets (the specific calling methods, pulled by file path from Step 2's evidence — no retrieval system needed since the file set is already small and known).

**Tool:** Claude API, direct SDK call, structured JSON output requested in the prompt.

**Connects to:** Takes Step 3's output + code evidence; feeds Step 5 if confirmed.

**Input/Output:**
- Input: cycle path, code snippets
- Output: `{"smell": "Cyclic Dependency", "detected": true, "confidence": 0.91, "severity": "HIGH", "rationale": "...", "refactoring_recommended": true}`

**How to test independently:** Run the same input multiple times and check output is broadly consistent; validate the JSON parses cleanly every time.

**Failure risks:** Malformed JSON output; inconsistent verdicts across runs (expected LLM non-determinism — plan for a JSON-repair/retry step rather than treating one failure as a system failure).

---

### Step 5: LLM Refactoring Agent

**Goal:** Have Claude propose a constrained fix that breaks the cycle.

**Why it's necessary:** This is the "autonomous refactoring" half of the thesis contribution — detection alone isn't enough.

**Simplest reliable implementation:** Second API call. Prompt: given the confirmed cycle and code, propose a minimal fix; require the response to explicitly list affected files/methods, the change description, rationale, and expected impact.

**Tool:** Claude API, structured JSON output.

**Connects to:** Takes Step 4's confirmed output; feeds Step 6.

**Input/Output:**
- Input: confirmed smell + code evidence
- Output: JSON refactoring plan (`affected_files`, `changes`, `rationale`, `expected_impact`)

**How to test independently:** Manually review whether the proposed plan is scoped only to the implicated services/files (a scope-enforcement check — reject/re-prompt if the plan names files outside the expected set).

**Failure risks:** LLM proposing changes outside the intended scope; vague or non-actionable change descriptions.

---

### Step 6: Manual Apply & Verify

**Goal:** Apply the proposed fix and confirm it actually resolves the smell without breaking the build.

**Why it's necessary:** No LLM-generated change should be trusted without verification — this is the safety gate.

**What to do (manual this week):**
1. Create a new git branch.
2. Manually make the change described in the refactoring plan.
3. Run `mvn test`.
4. Re-run Steps 2–3 (extractor + cycle detector) on the modified code.
5. Record: did tests pass? Is the cycle gone?

**Connects to:** Takes Step 5's plan; produces the final accept/reject decision.

**How to test independently:** Pass/fail is directly observable from test output and cycle-detector output.

**Failure risks:** None beyond normal debugging — this step is intentionally manual and low-risk for week 1. Automating git-branch-apply-compile-test is a legitimate Phase 2 task once the core loop is proven.

---

### Step 7: Logging

**Goal:** Record every stage's input/output for reproducibility.

**Why it's necessary:** Required for later evaluation and for the thesis's reproducibility claims.

**Simplest reliable implementation:** Write each stage's input/output to a JSON file per run (timestamped), including full LLM prompts and responses.

**Tool:** Plain Python `json`/file I/O — no MLflow yet.

**Failure risks:** None significant; just don't skip it, since retrofitting logs after the fact loses information.

---

## Phase 2+ — Add Incrementally After Phase 1 Works

Only proceed here once Steps 1–7 run end-to-end successfully on at least one repo.

**Already landed** (see README.md for usage and limitations):

- ✅ **Hub-like Dependency detector** — implemented in `pipeline/detectors/hub_dependency.py`
  using an absolute degree floor combined with a statistical-outlier test and a
  connectivity-share test on the same SDG, all thresholds configurable in `pipeline/config.py`.
- ✅ **Repository-agnostic input** — `pipeline/repository.py` accepts a Git URL or a local
  path; Steps 2–7 no longer assume `target-repo/`.
- ✅ **Pipeline orchestrator** — `pipeline/run_pipeline.py` runs acquisition → extraction →
  all detectors → LLM validation → refactoring → logging in one command, with per-stage error
  isolation.
- ✅ **Pluggable multi-smell architecture** — detector registry (`pipeline/detectors/`) plus a
  smell-spec registry (`pipeline/smells.py`) that drives per-smell prompts, so adding a smell
  requires no change to the orchestrator or the LLM agents.
- ✅ **Shared Persistence detector** — beyond the two smells this document scopes, added on
  request. Detects services sharing a database/schema/tables from datasource config, JPA
  `@Table` declarations and SQL schema/migration files. Its provenance (not PRD scope) is
  recorded in `pipeline/smells.py` and README.md.

**Still outstanding:**
- **Automated patch application** — replace manual git apply in Step 6 with a programmatic patch applier + compiler/test runner wrapper.
- **RAG layer (chunking → CodeBERT embeddings → FAISS)** — only once a single implicated service's relevant code no longer fits comfortably in one prompt, or once testing spans multiple repos where relevance can't be hand-verified. The SDG continues to do coarse filtering first; FAISS ranks within that already-narrowed scope.
- **LangGraph orchestration** — wrap the Detection → Refactoring → Verification sequence with retry logic, once the underlying logic is stable and doesn't need debugging at the same time as the framework.
- **External tool cross-validation** — run Arcan/MSANose/DesigniteJava on the same repo and compare their extracted graph against your own extractor's output, as a validation step for the methods section.
- **EvoSuite regression test generation** — for validation beyond existing tests.
- **Multi-repo evaluation corpus + ground-truth labeling** — with inter-rater reliability (Cohen's κ) across team members.
- **MLflow experiment tracking + Scott-Knott ESD statistical comparison** — once multiple runs across multiple repos/baselines exist to compare.

---

## Guiding Principle Throughout

Priority order, per the original project brief:

**Working end-to-end prototype → Reproducible experiment → Measurable results → Then add complexity.**

Do not add a Phase 2+ component until the corresponding Phase 1 loop is demonstrably working.
