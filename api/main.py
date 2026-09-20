"""
Read-only API for the frontend dashboard.

Serves the artifacts the Phase 1 pipeline already produces on disk --
`logs/edges_current.json` (Step 2) and `logs/runs/*/*.json` (Steps 3-5, 7,
via pipeline.run_logger) -- as JSON. Reuses pipeline.graph_analysis for
graph construction / cycle detection rather than re-deriving that logic in
the frontend. Does not trigger pipeline runs; no orchestrator (Step 6/
run_pipeline.py) exists yet to call.

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

from pipeline.graph_analysis import build_graph, detect_cycles
from pipeline.run_logger import LOGS_RUNS_DIR

REPO_ROOT = Path(__file__).resolve().parent.parent
EDGES_FILE = REPO_ROOT / "logs" / "edges_current.json"

_RUN_DIRNAME_RE = re.compile(r"^(\d{8}T\d{6}Z)_(.+)$")

app = FastAPI(title="Cyclic Dependency Pipeline API")


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


def _summarize_run(run_dir: Path) -> dict:
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
    }
    for f in sorted(run_dir.glob("*.json")):
        summary["files"].append(f.name)
        data = _read_json(f)
        if not isinstance(data, dict):
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


@app.get("/api/graph")
def get_graph() -> dict:
    edges = _load_edges()
    graph = build_graph(edges)
    cycles = detect_cycles(graph)
    services = sorted({e["caller"] for e in edges} | {e["callee"] for e in edges})
    return {"services": services, "edges": edges, "cycles": cycles}


@app.get("/api/overview")
def get_overview() -> dict:
    edges = _load_edges()
    graph = build_graph(edges)
    cycles = detect_cycles(graph)
    services = sorted({e["caller"] for e in edges} | {e["callee"] for e in edges})
    source_breakdown: dict[str, int] = {}
    for e in edges:
        source_breakdown[e["source"]] = source_breakdown.get(e["source"], 0) + 1
    runs = _list_runs()
    return {
        "services": services,
        "edgeCount": len(edges),
        "sourceBreakdown": source_breakdown,
        "cycles": cycles,
        "runCount": len(runs),
        "latestRun": runs[0] if runs else None,
    }


@app.get("/api/runs")
def list_runs() -> list[dict]:
    return _list_runs()


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict:
    run_dir = LOGS_RUNS_DIR / run_id
    if not run_dir.is_dir() or run_dir.parent != LOGS_RUNS_DIR:
        raise HTTPException(status_code=404, detail="run not found")
    stages = {f.stem: _read_json(f) for f in sorted(run_dir.glob("*.json"))}
    timestamp, label = _parse_run_dirname(run_dir.name)
    return {"id": run_dir.name, "timestamp": timestamp, "label": label, "stages": stages}
