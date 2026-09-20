"""
API for the frontend dashboard.

Two halves:

* Read-only endpoints over the artifacts the pipeline writes to disk --
  ``logs/edges_current.json`` (Step 2) and ``logs/runs/*/*.json`` (Steps
  3-7, via ``pipeline.run_logger``). Graph construction reuses
  ``pipeline.graph_analysis`` rather than re-deriving it in the frontend.
* Analysis control (``/api/smells``, ``/api/analyze``, ``/api/jobs/...``),
  which starts a real ``pipeline.run_pipeline`` run on a background thread
  and reports its progress. See ``api/jobs.py``.

Run from the repo root so `pipeline` and `api` both resolve as top-level
packages, same convention as the test suite:

    python -m uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from api import jobs
from pipeline.config import ALL_DETECTORS, default_config
from pipeline.graph_analysis import build_graph, detect_cycles
from pipeline.run_logger import LOGS_RUNS_DIR
from pipeline.smells import get_spec

REPO_ROOT = Path(__file__).resolve().parent.parent
EDGES_FILE = REPO_ROOT / "logs" / "edges_current.json"

_RUN_DIRNAME_RE = re.compile(r"^(\d{8}T\d{6}Z)_(.+)$")

app = FastAPI(title="Microservice Smell Detection API")


def _load_edges() -> list[dict]:
    if not EDGES_FILE.exists():
        return []
    return json.loads(EDGES_FILE.read_text(encoding="utf-8"))


def _parse_run_dirname(name: str) -> tuple[str, str]:
    m = _RUN_DIRNAME_RE.match(name)
    return (m.group(1), m.group(2)) if m else (name, name)


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _run_dir(run_id: str) -> Path:
    """Resolve a run id to its directory, rejecting anything outside logs/runs."""
    candidate = (LOGS_RUNS_DIR / run_id).resolve()
    if not candidate.is_dir() or candidate.parent != LOGS_RUNS_DIR.resolve():
        raise HTTPException(status_code=404, detail="run not found")
    return candidate


def _flatten_findings(result: dict) -> list[dict]:
    """Project an orchestrator result into the flat shape the UI renders."""
    flat: list[dict] = []
    for entry in result.get("results") or []:
        finding = entry.get("finding") or {}
        flat.append(
            {
                **finding,
                "llmDetection": entry.get("llm_detection"),
                "refactoring": entry.get("refactoring"),
                "errors": entry.get("errors") or [],
            }
        )
    return flat


def _summarize_run(run_dir: Path) -> dict:
    """One row's worth of a run.

    Multi-smell runs written by ``pipeline.run_pipeline`` carry everything in
    ``99_result.json``; Phase 1 runs predate it, so the older per-stage fields
    (``cycle``/``detection``/``refactoring``) are still derived for those.
    """
    timestamp, label = _parse_run_dirname(run_dir.name)
    summary: dict[str, Any] = {
        "id": run_dir.name,
        "timestamp": timestamp,
        "label": label,
        "files": [],
        "cycle": None,
        "detection": None,
        "refactoring": None,
        "scopeOk": None,
        "kind": "legacy",
        "repository": None,
        "counts": None,
        "findings": [],
        "detectorsRun": [],
        "llmEnabled": None,
        "errors": [],
    }
    for f in sorted(run_dir.glob("*.json")):
        summary["files"].append(f.name)
        data = _read_json(f)
        if not isinstance(data, dict):
            continue
        if f.stem == "99_result":
            summary.update(
                {
                    "kind": "pipeline",
                    "repository": data.get("repository"),
                    "counts": data.get("counts"),
                    "findings": _flatten_findings(data),
                    "detectorsRun": data.get("detectors_run") or [],
                    "llmEnabled": data.get("llm_enabled"),
                    "errors": data.get("errors") or [],
                }
            )
            continue
        if summary["cycle"] is None and isinstance(data.get("cycle"), list):
            summary["cycle"] = data["cycle"]
        stem = f.stem.lower()
        if "detection" in stem and isinstance(data.get("result"), dict):
            summary["detection"] = data["result"]
        if "refactoring" in stem and isinstance(data.get("result"), dict):
            summary["refactoring"] = data["result"]
            attempts = data.get("attempts") or []
            if attempts:
                summary["scopeOk"] = attempts[-1].get("scope_ok")
    return summary


def _list_runs() -> list[dict]:
    if not LOGS_RUNS_DIR.is_dir():
        return []
    run_dirs = sorted((p for p in LOGS_RUNS_DIR.iterdir() if p.is_dir()), reverse=True)
    return [_summarize_run(d) for d in run_dirs]


def _graph_payload(edges: list[dict]) -> dict:
    graph = build_graph(edges)
    services = sorted({e["caller"] for e in edges} | {e["callee"] for e in edges})
    return {"services": services, "edges": edges, "cycles": detect_cycles(graph)}


def _run_edges(run_id: str) -> list[dict]:
    """Dependency edges as extracted by one run (its 01_evidence stage)."""
    evidence = _read_json(_run_dir(run_id) / "01_evidence.json")
    if not isinstance(evidence, dict):
        raise HTTPException(status_code=404, detail="run has no extracted evidence")
    return list(evidence.get("dependencies") or [])


# --- read-only endpoints -----------------------------------------------------


@app.get("/api/graph")
def get_graph(run: Optional[str] = None) -> dict:
    """The service dependency graph.

    Without `run`, the Phase 1 extractor snapshot in logs/edges_current.json.
    With `run`, the graph that specific run actually analyzed -- which is the
    only way to see the graph of a repository analyzed by URL, since those
    clones are deleted after the run.
    """
    return _graph_payload(_run_edges(run) if run else _load_edges())


@app.get("/api/overview")
def get_overview() -> dict:
    runs = _list_runs()
    latest = runs[0] if runs else None
    # Prefer the latest pipeline run's own graph over the standalone
    # extractor snapshot, so the dashboard reflects the last analysis.
    edges = _load_edges()
    if latest and latest["kind"] == "pipeline":
        try:
            edges = _run_edges(latest["id"]) or edges
        except HTTPException:
            pass
    graph = _graph_payload(edges)
    source_breakdown: dict[str, int] = {}
    for e in edges:
        source_breakdown[e["source"]] = source_breakdown.get(e["source"], 0) + 1
    return {
        "services": graph["services"],
        "edgeCount": len(edges),
        "sourceBreakdown": source_breakdown,
        "cycles": graph["cycles"],
        "runCount": len(runs),
        "latestRun": latest,
    }


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return _list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run_dir = _run_dir(run_id)
    stages = {f.stem: _read_json(f) for f in sorted(run_dir.glob("*.json"))}
    timestamp, label = _parse_run_dirname(run_dir.name)
    return {
        "id": run_dir.name,
        "timestamp": timestamp,
        "label": label,
        "stages": stages,
        "summary": _summarize_run(run_dir),
    }


# --- analysis control --------------------------------------------------------


@app.get("/api/smells")
def get_smells() -> dict:
    """Which detectors can be selected, and whether LLM validation is available."""
    import os

    return {
        "smells": [
            {
                "key": key,
                "name": get_spec(key).name,
                "definition": " ".join(get_spec(key).definition.split()),
                "evidence": get_spec(key).evidence_noun,
                "limits": get_spec(key).static_limits,
            }
            for key in ALL_DETECTORS
        ],
        "llmAvailable": bool(os.environ.get("NVIDIA_API_KEY")),
        "llmEnabledByDefault": default_config().llm.enabled,
        "defaultLocalRepo": "target-repo" if (REPO_ROOT / "target-repo").is_dir() else None,
        "phases": [{"key": k, "label": label} for k, label in jobs.PHASES],
    }


class AnalyzeRequest(BaseModel):
    source: str = Field(..., description="Git URL or a local path inside the project")
    smells: Optional[list[str]] = None
    llm: bool = True
    branch: Optional[str] = None
    runName: str = "dashboard"


@app.post("/api/analyze", status_code=202)
def start_analysis(req: AnalyzeRequest) -> dict:
    try:
        job = jobs.start_job(
            req.source,
            smells=req.smells,
            llm=req.llm,
            branch=req.branch,
            run_name=req.runName or "dashboard",
        )
    except jobs.JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:  # another run already in flight
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job.to_dict()


@app.get("/api/jobs")
def list_jobs() -> dict:
    active = jobs.active_job()
    return {
        "active": active.to_dict() if active and active.status == "running" else None,
        "recent": [j.to_dict() for j in jobs.recent_jobs()],
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job.to_dict()
