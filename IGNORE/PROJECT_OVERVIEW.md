# Project Overview

## LLM-Guided Microservice Code Smell Detection & Autonomous Refactoring

This is a thesis research prototype for detecting architectural smells in Java Spring Boot
microservice systems and proposing fixes for them, combining deterministic static analysis
with LLM reasoning. It started (per `CLAUDE.md`) as a one-smell, one-repository Phase 1 MVP
and has since generalized well past that scope. This document describes **what it actually
does today**. For the phased build plan see `CLAUDE.md`; for setup, the full status table,
and known limitations see `README.md`.

---

## What it does today

**Detects three architectural smells**, not just one:

| Smell | How it's detected |
|---|---|
| **Cyclic Dependency** | The service dependency graph is built with NetworkX and run through `simple_cycles` |
| **Hub-like Dependency** | A service's degree is tested against the graph's own degree distribution (absolute floor + statistical-outlier test) plus a connectivity-share test |
| **Shared Persistence** | Services are checked for resolving to the same database identity or declaring overlapping tables, from datasource config, JPA `@Table` declarations, and SQL schema/migration files |

Every smell is detected statically first and validated by an LLM second: the model never
invents findings, it only judges and explains a candidate that deterministic analysis
already produced. Its verdict is recorded alongside — never in place of — the deterministic
severity/confidence.

**Analyzes any repository, not just the bundled fixture.** The pipeline takes either a
**GitHub (or any Git) URL** or a **local path**. A URL is cloned into a scratch `workspace/`
directory and cleaned up after the run (unless told to keep it); a local checkout is used in
place. The bundled `target-repo/` (`spring-petclinic-microservices`, with a deliberately
introduced, documented cyclic dependency — see `target-repo/FIXTURE_NOTES.md`) remains the
default fixture so a run works out of the box, but it is no longer the only thing the
pipeline can point at. Recent runs under `logs/runs/` include analyses of several different
teammates' repositories and branches, run both from the CLI and from the dashboard.

**Supports Maven and Gradle**, not just Maven. Module (service) discovery recognizes a
top-level directory as a service by either a `pom.xml` **or** a `build.gradle`/
`build.gradle.kts` at its root. The service's name is resolved from the Maven `artifactId`
when one is declared, or falls back to the Gradle module directory's own name for a
standalone Gradle module (matching Gradle's own default artifact-naming convention, rather
than inventing one). Both paths are covered by the test suite, not just documented.

**Runs end to end with one command.** `pipeline/run_pipeline.py` chains acquisition →
evidence extraction → all three detectors → LLM validation → LLM refactoring proposals →
logging, with no smell-specific logic of its own and per-stage error isolation — a failing
detector or a failing LLM call for one finding doesn't take down the rest of the run.

```bash
python -m pipeline.run_pipeline https://github.com/owner/repo.git
python -m pipeline.run_pipeline target-repo --no-llm
python -m pipeline.run_pipeline target-repo --smells cyclic_dependency hub_dependency
```

**Proposes fixes, scope-checked.** For every finding the LLM confirms and recommends a fix
for, a second LLM call proposes the minimal change that resolves it — affected files,
per-file changes, rationale, expected impact — and a scope-enforcement check rejects and
re-prompts if the plan names a file outside the services actually implicated in the finding.

**Logs everything.** Every run writes one timestamped directory under `logs/runs/` with one
JSON file per stage — full prompts, raw LLM responses, reasoning traces, parse/scope-check
status — across every smell detected in that run, so any run is reproducible and auditable
after the fact.

**Has a dashboard that starts runs, not just displays them.** From `/analyze` you pick a
repository (the bundled fixture or any public HTTPS Git URL), choose which smells to check
and whether to include LLM validation, and watch real per-stage progress as the pipeline
writes each stage to disk — nothing in the progress view is simulated. `/detection`,
`/refactoring`, `/graph`, and `/runs` read the results of any completed run: findings with
both deterministic and LLM verdicts side by side, proposed fixes, a hand-built SVG of the
service dependency graph, and run history.

**Still manual: applying a fix.** A proposed refactoring plan is not applied to code, `mvn
test` is not re-run, and the pipeline is not re-run to confirm a finding disappeared —
that accept/reject step is still a human doing it by hand. This is Phase 1's one remaining
open loop.

---

## How a run actually flows

1. **Acquire** (`pipeline/repository.py`) — clone the Git URL or use the local path.
2. **Extract evidence** (`pipeline/extractors/`) — `rest.py` for Feign/RestTemplate/WebClient
   call sites (recognizing Maven or Gradle modules), `docker.py` for `docker-compose.yml`
   `depends_on` edges, `persistence.py` for datasource/JPA/SQL schema evidence.
3. **Build the graph** (`pipeline/graph_analysis.py`) — a `networkx.DiGraph` of service call
   edges, with cycles canonicalized so the same graph always yields identical output.
4. **Detect** (`pipeline/detectors/`) — the registry runs every requested smell detector
   against that graph/evidence and produces `Finding` objects.
5. **Validate** (`pipeline/llm_detection.py`) — one LLM call per finding, grounded in the real
   code evidence, prompted from that smell's spec in `pipeline/smells.py`.
6. **Propose a fix** (`pipeline/llm_refactoring.py`) — for confirmed, fix-recommended
   findings, a scope-checked minimal refactoring plan.
7. **Log** (`pipeline/run_logger.py`) — every stage's input/output written to
   `logs/runs/<timestamp>_<label>/`.
8. **(Manual)** — apply the plan, re-test, re-run the pipeline to confirm the finding is gone.

Adding a new smell touches only a new detector module and a new spec entry — the
orchestrator, the LLM agents, and the API need no changes.

---

## Repository map

```
pipeline/            orchestrator, models, config, smell specs, repository acquisition
pipeline/extractors/ evidence collectors: rest (Maven + Gradle), docker, persistence
pipeline/detectors/  cyclic_dependency, hub_dependency, shared_persistence + the registry
api/                 FastAPI layer: reads logs/, starts runs (api/jobs.py)
frontend/            Vite + React + TypeScript dashboard
tests/               pytest suite, offline (LLM calls and git clones mocked)
target-repo/         bundled default target repository (committed, incl. the fixture)
workspace/           repositories cloned by URL (gitignored, cleaned up after each run)
logs/                edges_current.json (sample) + runs/ (per-run stage logs across repos)
IGNORE/              reference docs only (the PRD) -- never commit generated output here
CLAUDE.md            phased implementation guide (Phase 1 steps, Phase 2+ roadmap)
README.md            status table, setup instructions, current known issues
```

## Known limitations (see `README.md` for full detail)

- REST only — gRPC and message-queue communication are invisible to the extractor (a stated
  scope boundary, not a gap).
- Regex-based, not AST — a call assembled dynamically with no literal service-name string is
  missed.
- Java/Spring Boot only, on Maven **or** Gradle — other build tools and non-Spring stacks
  aren't supported.
- Shared Persistence can't prove physical sharing (identical names may be different
  instances/tables); confidence is reported accordingly.
- Temporal coupling isn't implemented — no static heuristic for it is claimed.
- Step 6 (apply & verify) is still a manual step.
