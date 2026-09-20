# LLM-Guided Microservice Code Smell Detection & Autonomous Refactoring

Detect architectural smells in a Java Spring Boot microservices repository using static
analysis, have an LLM confirm whether each finding is a genuine problem, propose a
scope-limited fix with a second LLM call, and log every stage for reproducibility. The
pipeline takes a **Git URL or a local path** and runs **multiple smell detectors** in one
pass. Full spec and rationale: [CLAUDE.md](CLAUDE.md).

## Quick start

```bash
# Analyze a repository by URL (clones it into workspace/, then deletes it)
python -m pipeline.run_pipeline https://github.com/owner/repo.git

# Analyze a local checkout, deterministic detection only (no API key needed)
python -m pipeline.run_pipeline target-repo --no-llm

# One smell only, writing the unified result somewhere
python -m pipeline.run_pipeline target-repo --smells cyclic_dependency --output result.json
```

Or drive it from the dashboard — pick the repository and the smells in the browser and watch
the run stage by stage (see [Dashboard](#dashboard)):

```bash
python -m uvicorn api.main:app --reload --port 8000   # terminal 1, from the repo root
cd frontend && npm install && npm run dev             # terminal 2
```

## Supported smells

| Smell | Detection | Evidence source | Status |
|---|---|---|---|
| **Cyclic Dependency** | NetworkX `simple_cycles` on the service dependency graph | Feign / RestTemplate / WebClient / DiscoveryClient call sites | ✅ Implemented + LLM validated |
| **Hub-like Dependency** | Degree vs. the graph's own degree distribution, plus a connectivity-share test | Same dependency graph | ✅ Implemented + LLM validated |
| **Shared Persistence** | Services resolving to one database identity, or declaring the same tables | Datasource config, JPA `@Table`, SQL schema/migrations, compose env | ✅ Implemented + LLM validated |
| Temporal coupling | — | — | ❌ Not implemented (see [Known limitations](#known-limitations)) |

Every smell is **statically detected first** and **LLM validated second**: the model is
never asked to find smells, only to judge a candidate that deterministic analysis already
produced, and to explain its reasoning. The LLM's verdict never overwrites the
deterministic severity/confidence; both are recorded.

**Scope provenance:** CLAUDE.md and the PRD specify two smells — Cyclic Dependency (Phase 1)
and Hub-like Dependency (Phase 2+). Shared Persistence is implemented in addition to those;
it is a standard microservice anti-pattern and is statically observable, but it is not part
of the original PRD scope.

## Status at a glance

| Step | What | Status |
|---|---|---|
| 1 | Select & verify target repo | ✅ Done |
| 2 | Dependency extractor | ✅ Done — generalized into `pipeline/extractors/` |
| 3 | Graph + cycle detection | ✅ Done — cycles now canonicalized |
| 4 | LLM Detection Agent | ✅ Done — generalized to any smell |
| 5 | LLM Refactoring Agent | ✅ Done — generalized to any smell |
| 6 | Manual apply & verify | ❌ **Not started** |
| 7 | Per-run logging | ✅ Done — generalized to multi-smell runs |
| — | Repository acquisition (Git URL / local path) | ✅ Done |
| — | Pipeline orchestrator (`run_pipeline.py`) | ✅ Done |
| — | Hub-like Dependency detector | ✅ Done |
| — | Shared Persistence detector | ✅ Done |
| — | Dashboard (API + frontend) | ✅ Done — starts runs (repo URL or local, smell selection, live progress) and renders multi-smell results |

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
`mvn test` were re-verified clean after introducing it.

**`target-repo/` is now committed into this repository** (its own nested `.git` was removed
so its files track as ordinary files here), so a fresh clone reproduces the exact tree the
run logs were produced against — no patch file or separate clone step needed.

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

## Pipeline architecture

```
repository acquisition   pipeline/repository.py    Git URL or local path -> working tree
         |                                          + branch/commit metadata
         v
evidence extraction      pipeline/extractors/      rest.py, docker.py, persistence.py
         |                                          -> normalized EvidenceBundle
         v
service dependency graph pipeline/graph_analysis.py NetworkX DiGraph (compose edges excluded)
         |
         v
smell detectors          pipeline/detectors/       registry: cyclic_dependency,
         |                                          hub_dependency, shared_persistence
         v                                          -> Finding objects
LLM validation           pipeline/llm_detection.py  per finding, prompt built from the
         |                                          smell's spec in pipeline/smells.py
         v
LLM refactoring          pipeline/llm_refactoring.py scope-checked proposal, no code edits
         |
         v
logging + result         pipeline/run_logger.py     one directory per run
                         pipeline/models.py         unified AnalysisResult
```

`pipeline/run_pipeline.py` coordinates these stages and contains **no smell-specific
logic**. Adding a smell means adding a detector in `pipeline/detectors/` and a spec in
`pipeline/smells.py` — the orchestrator, the LLM agents and the API need no changes.

Every stage isolates errors: a failing extractor, a failing detector or a failing LLM call
for one finding is recorded on the result and the run continues with the rest. Nothing is
swallowed silently.

## Repository layout

```
pipeline/            orchestrator, models, config, smell specs, repository acquisition
pipeline/extractors/ evidence collectors: rest, docker, persistence
pipeline/detectors/  one module per smell + the registry
api/                 FastAPI layer for the frontend: reads logs/, starts runs (see Dashboard)
frontend/            Vite + React + TypeScript dashboard (see Dashboard)
tests/               pytest suite
target-repo/         the Phase 1 target repository
workspace/           repositories cloned by URL (gitignored, cleaned up after each run)
logs/                logs/edges_current.json (sample) + logs/runs/ (per-run stage logs)
IGNORE/              reference docs only (PRD) — never commit generated output here
CLAUDE.md            full phased implementation guide
```

## Running the pipeline

```bash
# A local repository (no network, no API key with --no-llm)
python -m pipeline.run_pipeline target-repo --no-llm

# A Git repository by URL, on a specific branch
python -m pipeline.run_pipeline https://github.com/owner/repo.git --branch main

# Choose detectors, name the run, keep the clone for inspection
python -m pipeline.run_pipeline https://github.com/owner/repo \
    --smells cyclic_dependency shared_persistence \
    --run-name experiment-3 --keep-clone --output result.json
```

| Flag | Effect |
|---|---|
| `--branch` | Branch to check out (Git URLs only) |
| `--smells` | Subset of `cyclic_dependency`, `hub_dependency`, `shared_persistence` |
| `--no-llm` | Deterministic detection only; makes no API calls |
| `--run-name` | Label for the run log directory under `logs/runs/` |
| `--output` | Also write the unified result JSON to this path |
| `--keep-clone` | Keep a cloned repository instead of deleting it after the run |

The Phase 1 single-step commands still work unchanged:

```bash
python -m pipeline.extractor target-repo -o logs/edges_current.json
python -m pipeline.graph_analysis logs/edges_current.json
python -m pipeline.llm_detection logs/edges_current.json --repo-root target-repo
python -m pipeline.llm_refactoring logs/edges_current.json --repo-root target-repo
```

## Configuration

All thresholds live in [`pipeline/config.py`](pipeline/config.py) rather than inside
detectors, so a run can state exactly which parameters produced it (they are written to
`00_run_metadata.json` on every run). The main ones:

| Setting | Default | Meaning |
|---|---|---|
| `hub.min_degree` | 4 | Absolute floor before a service can be a hub at all |
| `hub.stdev_multiplier` | 1.5 | Outlier test: `degree >= mean + k*stdev` |
| `hub.degree_ratio` | 0.5 | Share test: connected to this fraction of other services |
| `hub.degree_mode` | `total` | `total`, `in`, or `out` degree |
| `shared_persistence.in_memory_drivers` | hsqldb, h2, derby, sqlite | Never reported as shared |
| `shared_persistence.report_table_overlap_without_datasource` | `True` | Enables the weak table-name rule |
| `excluded_edge_sources` | `docker-compose` | Edge sources kept out of the graph |
| `repository.clone_depth` / `max_repo_size_mb` | 1 / 1024 | Clone limits |
| `llm.min_finding_confidence` | 0.0 | Skip weak findings before spending tokens |

## Tests

```bash
python -m pytest tests/ -q          # whole suite, no network and no API key required
python -m pytest tests/test_run_pipeline.py -q   # end-to-end orchestrator
```

Every LLM call and every `git clone` is mocked in the suite, so it runs offline.

## Known limitations

These are real boundaries of the current implementation, not oversights:

- **REST only.** gRPC and message-queue (Kafka/RabbitMQ/JMS) communication are invisible to
  the extractor, so a system built on them will appear to have no dependencies at all. This
  is a stated scope boundary in the PRD, not a temporary gap.
- **Regex, not AST.** A call assembled dynamically so that no `"http://<service>"` literal
  ever appears in the source will be missed.
- **Java/Spring Boot/Maven layout.** Services are discovered from top-level Maven modules
  with `pom.xml` and `spring.application.name`. Gradle projects, nested module layouts and
  non-Spring stacks are not supported.
- **Externalized configuration is invisible.** If datasource config lives in Spring Cloud
  Config, environment variables or Kubernetes secrets (as it does in the Phase 1 target
  repo), Shared Persistence has nothing to analyze and correctly reports nothing. Absence of
  evidence is reported as absence, never as "no sharing".
- **Shared Persistence cannot prove physical sharing.** Identical database names may be
  different instances per environment; identical table names may be unrelated tables. The
  detector reports confidence accordingly and the LLM is told to treat it as a ceiling.
- **Temporal coupling is not implemented.** Neither the PRD nor CLAUDE.md defines it, and
  establishing it requires runtime call-ordering data (traces) that static analysis cannot
  produce. Implementing it as a static heuristic would mean claiming runtime behaviour was
  observed when it was not.
- **No private repositories.** Only public `https://` clones are supported; credential
  prompts are disabled so a private URL fails fast instead of hanging.
- **Refactoring is a proposal only.** The agent never edits code (CLAUDE.md Step 6 remains
  manual).

## Dashboard

The dashboard both **starts** analyses and visualizes their results.

**Running one (`/analyze`).** Pick a repository — the bundled `target-repo`, or any public
HTTPS Git URL with an optional branch — then either run a full analysis or search the detector
catalogue and select specific smells, and choose whether to include LLM validation. The page
then shows the run's real progress: a station-by-station view of the orchestrator's own stages
(acquire → extract → detect → validate → refactor), each one appearing as the pipeline actually
writes it to disk, alongside a live stage log. Nothing about the progress is simulated —
`api/jobs.py` subclasses the pipeline's `RunLogger`, so a station lights up only when that
stage has completed. When the run finishes it links straight to the full run log.

**Reading results.** `/detection` lists every finding of the latest run, of any smell, showing
the deterministic detector's severity/confidence and the LLM's verdict side by side (the model
never overwrites what static analysis measured), with the evidence and full reasoning trace
behind each. `/refactoring` shows the proposed plans, `/graph` renders the Service Dependency
Graph of any selected run (hand-laid-out SVG — docker-compose-only services are drawn as muted
infra nodes, real call edges solid, cycle edges highlighted), and `/runs` is the history.

- `api/main.py` is a thin FastAPI layer that reuses `pipeline.graph_analysis` directly rather
  than re-deriving cycle detection in JS. Run from the repo root: `python -m uvicorn api.main:app
  --reload --port 8000`.
- `api/jobs.py` runs one analysis at a time on a background thread. The repository source is
  validated before it reaches the pipeline: Git URLs go through `pipeline.repository`'s existing
  strict validator, and local paths are confined to the project directory, so the endpoint
  cannot be used to read arbitrary directories on the machine.
- `frontend/` is a Vite + React + TypeScript SPA (Tailwind v4, hand-built SVG graph, no chart/graph
  library). Vite's dev server proxies `/api` to `127.0.0.1:8000` (not `localhost` — on hosts
  where Node resolves `localhost` to `::1` only, that mismatches uvicorn's IPv4-only bind and
  every request 502s). `cd frontend && npm install && npm run dev`, then open the printed
  `localhost:5173` URL.
- `npm test` in `frontend/` runs a small Vitest suite covering the graph layout's node
  classification and cycle-edge separation logic, and the run-log stage lookup by finding key.

## Setup

1. `python -m venv .venv` and activate it, then `pip install -r requirements.txt` (includes
   FastAPI/uvicorn for the dashboard's API).
2. Copy `.env.example` to `.env` and fill in your own `NVIDIA_API_KEY` (free tier at
   https://build.nvidia.com) — required for Steps 4-5. `.env` is gitignored; never commit it.
3. `target-repo/` is already committed, including the synthetic cyclic-dependency fixture the
   run logs were produced against — nothing to clone. Analyzing any other repository needs no
   setup either: pass its URL to `pipeline.run_pipeline` and it is cloned into `workspace/`.
4. Run tests: `python -m pytest tests/ -q` (no network or API key needed).
5. Run the pipeline: see **Running the pipeline** above.
6. For the dashboard: see **Dashboard** above.

---

## Current issues

1. ~~**Flaky test / non-deterministic cycles.**~~ **Fixed.** `detect_cycles()` now
   canonicalizes every cycle to start at its lexicographically smallest node (rotation only,
   direction preserved) and sorts the result, so the same graph always produces byte-identical
   output regardless of NetworkX's traversal order. Covered by
   `tests/test_detectors_cyclic.py::test_equivalent_rotations_produce_identical_output`.

2. ~~**Fixture is not reproducible by teammates.**~~ **Resolved differently than planned.**
   `target-repo/` (including the synthetic cyclic-dependency fixture and
   `FIXTURE_NOTES.md`) is now committed directly into this repository rather than exported as
   a patch, so a fresh clone reproduces the exact tree the run logs were produced against.

3. ~~**No single command runs the full pipeline.**~~ **Fixed.**
   `python -m pipeline.run_pipeline <repo>` runs acquisition → extraction → all detectors →
   LLM validation → refactoring → logging in one command.

4. **Per-teammate API keys.** The LLM stages require a live `NVIDIA_API_KEY`; each developer
   needs their own in their own local `.env` (never shared via git). Everything except the LLM
   stages runs without one — use `--no-llm`.

5. **Analyses started from the dashboard live in the server process.** `api/jobs.py` keeps
   jobs in memory and runs one at a time, so a restart loses in-flight progress (the run log
   on disk survives) and a multi-worker deployment would not share job state. That is the
   right size for a single-user research dashboard; a real queue is only worth it if runs ever
   need to outlive the server.

---

## Next steps

The Phase 1 three-way split (Developers A/B/C) is complete: cycle canonicalization, the
single-command orchestrator and the Hub-like Dependency detector all landed, and the target
repository is now committed rather than needing a patch file.

Remaining, in rough priority order:

1. **Step 6 — apply & verify.** Apply a proposed refactoring to `target-repo`, run `mvn test`,
   and re-run the pipeline to confirm the finding disappears. This is Phase 1's final
   accept/reject gate and is still manual. Automating it (branch → patch → compile → test →
   re-detect) is the natural follow-up.
2. **Evaluation corpus.** Run the pipeline across several repositories and label the findings
   to get precision/recall per smell, which is what the thesis's measurement chapter needs.
3. **Phase 2+ items from CLAUDE.md** not yet started: RAG over large services, LangGraph
   orchestration with bounded retries, cross-validation against Arcan/MSANose/DesigniteJava,
   EvoSuite regression tests, MLflow tracking.
