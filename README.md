# LLM-Guided Microservice Code Smell Detection & Autonomous Refactoring

Phase 1 MVP: detect the **Cyclic Dependency** architectural smell in a Java Spring Boot
microservices repository using static analysis (regex extraction + graph cycle detection),
confirm it with an LLM, propose a scope-limited fix with a second LLM call, and log every
stage for reproducibility. Full spec and rationale: [CLAUDE.md](CLAUDE.md).

## Status at a glance

| Step | What | Status |
|---|---|---|
| 1 | Select & verify target repo | ✅ Done |
| 2 | Dependency extractor | ✅ Done |
| 3 | Graph + cycle detection | ✅ Done |
| 4 | LLM Detection Agent | ✅ Done — run once against real evidence |
| 5 | LLM Refactoring Agent | ✅ Done — run once against real evidence |
| 6 | Manual apply & verify | ❌ **Not started — next task** |
| 7 | Per-run logging | ✅ Done |

---

## What has been done

### Step 1 — Target repository
[`spring-petclinic-microservices`](https://github.com/spring-petclinic/spring-petclinic-microservices)
(8 Spring Boot services, Feign + `RestTemplate`/`WebClient`, `docker-compose.yml`) was cloned
into `target-repo/` and verified to build and test clean in its original, unmodified form.

In its original form the repo has **no cyclic dependency** — only a clean fan-out from
`api-gateway`/`genai-service` down to the other services. Since Phase 1's target smell needs
something to detect, a **minimal, clearly-labeled synthetic cyclic dependency** was
deliberately introduced between `customers-service` and `visits-service` (two new,
additive, self-contained endpoints on each side — no existing endpoint, class, or test was
touched). Full details, rationale, and the resulting dependency graph are documented in
[`target-repo/FIXTURE_NOTES.md`](target-repo/FIXTURE_NOTES.md). `mvn clean install` and
`mvn test` were re-verified clean after introducing it. This lives on the
`thesis/phase1-cyclic-dependency-fixture` branch inside `target-repo`'s own git history.

**`target-repo/` is intentionally excluded from this repo's git history** (it's a clone of
an external project with its own `.git`) — see [Setup](#setup) and
[Current Issues](#current-issues) below for what that means for the team.

### Step 2 — Dependency extractor ([`pipeline/extractor.py`](pipeline/extractor.py))
Regex/string-matching extractor (no AST parsing, per scope) that scans each Maven module's
Java source for `@FeignClient(...)`, `DiscoveryClient.getInstances("...")`, and any
`"http(s)://<service-name>/..."` string literal (covers `RestTemplate` and `WebClient`
calls, including the common pattern where the host is assigned to a field first), plus
`docker-compose.yml` `depends_on` blocks as a secondary edge source. Known limitations
(dynamic string assembly, gRPC/message queues) are documented in the module docstring.
Tested in [`tests/test_extractor.py`](tests/test_extractor.py). A sample extraction run
against `target-repo` is checked in at [`logs/edges_current.json`](logs/edges_current.json).

### Step 3 — Graph & cycle detection ([`pipeline/graph_analysis.py`](pipeline/graph_analysis.py))
Loads Step 2's edges into a `networkx.DiGraph` (collapsing duplicate edges, preserving all
underlying evidence) and runs `nx.simple_cycles`. `docker-compose` edges are excluded from
cycle detection by default (they encode startup order, not request-flow coupling).
`cycle_evidence()` maps a detected cycle back to the specific file/line evidence for every
hop, feeding Step 4. Tested in
[`tests/test_graph_analysis.py`](tests/test_graph_analysis.py).

### Step 4 — LLM Detection Agent ([`pipeline/llm_detection.py`](pipeline/llm_detection.py))
One LLM call per candidate cycle, grounded in the actual code evidence, requesting
structured JSON (`detected`, `confidence`, `severity`, `rationale`,
`refactoring_recommended`). Includes a JSON-repair/retry loop
([`pipeline/json_utils.py`](pipeline/json_utils.py)) for malformed responses, per CLAUDE.md's
stated failure risk. **Provider note:** this uses NVIDIA's OpenAI-compatible endpoint
([`pipeline/llm_client.py`](pipeline/llm_client.py)), not the Claude API CLAUDE.md
originally specified — a project decision made when API access was easier to get through
NVIDIA; nothing downstream depends on which provider is behind `call_llm()`.

**Already run successfully against the real fixture**: confirmed the
`customers-service <-> visits-service` cycle with `detected: true`, `confidence: 0.95`,
`severity: HIGH` — see
[`logs/runs/20260914T180118Z_step5-refactoring/step4_detection_visits-service_customers-service.json`](logs/runs/20260914T180118Z_step5-refactoring/step4_detection_visits-service_customers-service.json).
Tested in [`tests/test_llm_detection.py`](tests/test_llm_detection.py).

### Step 5 — LLM Refactoring Agent ([`pipeline/llm_refactoring.py`](pipeline/llm_refactoring.py))
Second LLM call proposing a minimal fix, with a **scope-enforcement check**: every file the
plan names must live inside a module belonging to a service in the cycle, or the plan is
rejected and re-prompted with the specific offending paths (not silently dropped or
accepted), bounded by `max_retries`.

**Already run successfully**: proposed removing `customers-service`'s call into
`visits-service` (`VisitsServiceClient.getVisitCount`) as the minimal cycle-breaking change
— see
[`logs/runs/20260914T180118Z_step5-refactoring/step5_refactoring_customers-service_visits-service.json`](logs/runs/20260914T180118Z_step5-refactoring/step5_refactoring_customers-service_visits-service.json).
Tested in [`tests/test_llm_refactoring.py`](tests/test_llm_refactoring.py).

### Step 6 — Manual apply & verify — **not started**
The Step 5 plan above has not yet been applied to `target-repo`, `mvn test` has not been
re-run against the change, and the extractor/cycle-detector have not been re-run to confirm
the cycle disappears. This is the immediate next task (see Developer A below).

### Step 7 — Logging ([`pipeline/run_logger.py`](pipeline/run_logger.py))
One timestamped directory per run under `logs/runs/`, one JSON file per stage, including
full prompts, raw responses, and reasoning traces. Already exercised by the Step 4/5 run
referenced above. Tested in [`tests/test_run_logger.py`](tests/test_run_logger.py).

---

## Repository layout

```
pipeline/         Steps 2-5, 7 (extractor, graph analysis, LLM agents, logging)
api/              Read-only FastAPI layer over logs/ for the frontend (see Dashboard)
frontend/         Vite + React + TypeScript dashboard (see Dashboard)
tests/            pytest suite, one file per pipeline module
target-repo/      cloned target repo (gitignored — see Setup)
logs/             logs/edges_current.json (sample) + logs/runs/ (per-run stage logs)
IGNORE/           reference docs only (PRD) — never commit generated output here
CLAUDE.md         full phased implementation guide
```

## Dashboard

A dashboard visualizes what the pipeline already produces on disk: an interactive Service
Dependency Graph (`/graph`, hand-laid-out SVG — services that only appear in docker-compose
`depends_on` are drawn as muted infra nodes, real call edges are solid, cycle edges are
highlighted), the Step 4 detection verdict and Step 5 refactoring proposal with their full
LLM reasoning traces (`/detection`, `/refactoring`), and the run log history (`/runs`). It's
read-only for now: it renders `logs/edges_current.json` and `logs/runs/*/*.json`, it doesn't
trigger pipeline runs (there's no orchestrator to call yet — see Issue #3 below).

- `api/main.py` is a thin FastAPI layer that reuses `pipeline.graph_analysis` directly rather
  than re-deriving cycle detection in JS. Run from the repo root: `python -m uvicorn api.main:app
  --reload --port 8000`.
- `frontend/` is a Vite + React + TypeScript SPA (Tailwind v4, hand-built SVG graph, no chart/graph
  library). Vite's dev server proxies `/api` to `127.0.0.1:8000` (not `localhost` — on hosts
  where Node resolves `localhost` to `::1` only, that mismatches uvicorn's IPv4-only bind and
  every request 502s). `cd frontend && npm install && npm run dev`, then open the printed
  `localhost:5173` URL.
- `npm test` in `frontend/` runs a small Vitest suite covering the graph layout's node
  classification and cycle-edge separation logic.

## Setup

1. `python -m venv .venv` and activate it, then `pip install -r requirements.txt` (includes
   FastAPI/uvicorn for the dashboard's API).
2. Copy `.env.example` to `.env` and fill in your own `NVIDIA_API_KEY` (free tier at
   https://build.nvidia.com) — required for Steps 4-5. `.env` is gitignored; never commit it.
3. Clone the target repo yourself — it is **not** part of this git history:
   ```
   git clone https://github.com/spring-petclinic/spring-petclinic-microservices.git target-repo
   ```
   This gives you the *unmodified* upstream repo. To get the same synthetic cyclic-dependency
   fixture the existing run logs were produced against, see **Current Issue #2** below — until
   Developer A's task lands, you'll need to recreate `target-repo/FIXTURE_NOTES.md`'s changes
   by hand.
4. Run tests: `python -m pytest tests/ -q`.
5. For the dashboard: see **Dashboard** above.

---

## Current issues

1. **Flaky test** — `tests/test_llm_refactoring.py::test_run_proposes_refactoring_when_recommended`
   fails intermittently (reproduced locally: `1 failed, 33 passed`). `networkx.simple_cycles()`
   returns a 2-node cycle starting at whichever node its traversal hits first — e.g.
   `['a', 'b', 'a']` on one run, `['b', 'a', 'b']` on another — and the test hardcodes one
   specific rotation. This isn't a bug in the code path under test, but the underlying output
   genuinely isn't deterministic today, which also undermines Step 7's reproducibility goal for
   real runs. **Fix needed in `detect_cycles()` itself** (canonicalize each cycle to a stable
   rotation), not just in the test. Assigned to Developer B below.

2. **Fixture is not reproducible by teammates.** `target-repo/` is correctly excluded from this
   repo's git history (it's a full external clone with its own `.git`), but that also means the
   synthetic cyclic dependency the Step 4/5 logs above were produced against — currently
   uncommitted, local-only changes on `target-repo`'s `thesis/phase1-cyclic-dependency-fixture`
   branch — exists on one machine only. No one else can currently reproduce Steps 2-6 against
   the same evidence. Needs to be exported as a patch file committed into this repo. Assigned to
   Developer A below.

3. **No single command runs the full pipeline.** Steps 2, 4, and 5 each have their own
   `argparse` `main()` and must be invoked separately, passing the edges JSON between them by
   hand (`python -m pipeline.extractor target-repo -o edges.json`, then
   `python -m pipeline.llm_refactoring edges.json --repo-root target-repo`, ...). Assigned to
   Developer B below.

4. **Per-teammate API keys.** Steps 4-5 require a live `NVIDIA_API_KEY`; each developer needs
   their own in their own local `.env` (never shared via git) before those steps will run.

---

## Next steps — split for 3 people working in parallel

The three workstreams below touch almost entirely disjoint files, so all three can be branched
from `main` and developed simultaneously with minimal merge conflicts. The only file two
workstreams both touch is `pipeline/graph_analysis.py` (Developer B only) — Developer C should
add a *new* file rather than editing it, to stay out of Developer B's way.

### Developer A — Close out Step 6 + fix Issue #2 (fixture reproducibility)
**Branch:** `feature/step6-verify-fixture`

1. On `target-repo`, branch off `thesis/phase1-cyclic-dependency-fixture` and manually apply
   the Step 5 plan (remove `VisitsServiceClient.getVisitCount` and its call sites from
   `customers-service`, per
   [`step5_refactoring_customers-service_visits-service.json`](logs/runs/20260914T180118Z_step5-refactoring/step5_refactoring_customers-service_visits-service.json)).
2. Run `mvn test`; confirm it's still green.
3. Re-run the extractor (`pipeline/extractor.py`) and cycle detector
   (`pipeline/graph_analysis.py`) against the modified code; confirm the
   `customers-service <-> visits-service` cycle no longer appears.
4. Record the before/after result (tests pass? cycle gone?) as a new entry under
   `logs/runs/` or a short report — this is Phase 1's final accept/reject decision.
5. **Fix Issue #2:** export the fixture as `git diff` against upstream `main`, committed into
   *this* repo as `fixtures/petclinic-cyclic-dependency.patch`, with a short
   `fixtures/README.md` giving the two-command apply recipe. This lets any teammate regenerate
   the exact same `target-repo` state from a clean clone.

**Files touched:** `target-repo/` (untracked by this repo — zero conflict risk with B/C), new
`fixtures/`, `logs/runs/`.

### Developer B — Fix Issue #1 (flaky test) + Issue #3 (single pipeline runner)
**Branch:** `feature/canonicalize-cycles-and-runner`

1. In `pipeline/graph_analysis.py`, canonicalize each cycle in `detect_cycles()` to a stable
   rotation (e.g. start at the lexicographically smallest node) before returning, so output is
   deterministic regardless of `networkx`'s internal traversal order.
2. Update the affected assertions in `tests/test_graph_analysis.py` and
   `tests/test_llm_refactoring.py`; add a regression test that feeds the same cycle in two
   different rotations and asserts identical canonicalized output.
3. Add a single orchestrator entry point (e.g. `pipeline/run_pipeline.py`) chaining
   extractor → graph/cycle detection → LLM detection → LLM refactoring → logging behind one
   command, taking just a repo path — replacing the current need to invoke four separate
   `python -m pipeline.X` commands and pass JSON between them by hand.
4. Add tests for the new entry point and a short usage note.

**Files touched:** `pipeline/graph_analysis.py`, `tests/test_graph_analysis.py`,
`tests/test_llm_refactoring.py`, new `pipeline/run_pipeline.py` + its test.

### Developer C — Phase 2 kickoff: Hub-like Dependency detector
**Branch:** `feature/hub-like-dependency-detector`

The first Phase 2+ item from CLAUDE.md ("Hub-like Dependency detector — degree/centrality
threshold on the same SDG"), additive and independent of the Cyclic Dependency code path.

1. Add a hub-like dependency detector as a **new module**, `pipeline/hub_detection.py`
   (reuses `build_graph()` from `pipeline/graph_analysis.py` but doesn't modify it): flag any
   service whose in-/out-degree (or centrality) exceeds a configurable threshold.
2. Add an LLM detection agent for this smell mirroring `pipeline/llm_detection.py`'s pattern
   (new smell definition + system prompt; reuse `code_context.py`, `llm_client.py`,
   `json_utils.py` as-is for evidence formatting, calling the LLM, and JSON parsing/retry).
3. Add tests mirroring `tests/test_graph_analysis.py` / `tests/test_llm_detection.py`'s
   structure.
4. Document known limitations of the degree/centrality approach in the module docstring,
   matching the style of `extractor.py`'s docstring.

**Files touched:** new `pipeline/hub_detection.py`, new `pipeline/llm_hub_detection.py`, new
`tests/test_hub_detection.py`, `tests/test_llm_hub_detection.py`.

### Merging back to `main`

- Run `python -m pytest tests/ -q` before opening each PR.
- Suggested merge order: **B first** (it fixes the flaky-test baseline everyone else's CI runs
  against), then **A** and **C** in either order — their file sets don't overlap with each
  other or with B's `graph_analysis.py` change beyond the shared `build_graph()`/`detect_cycles()`
  functions, which only B modifies.
