"""
Step 7: Per-run logging.

Writes each stage's full input/output to a timestamped JSON file per run,
per CLAUDE.md Step 7 ("write each stage's input/output to a JSON file per
run... including full LLM prompts and responses"). Deliberately plain
json/file I/O, no MLflow (that's Phase 2+, once there are multiple
runs/repos/baselines worth comparing).
"""

from __future__ import annotations

import dataclasses
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOGS_RUNS_DIR = Path(__file__).resolve().parent.parent / "logs" / "runs"


def _slugify(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-").lower() or "run"


def _json_default(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return str(obj)


class RunLogger:
    """One RunLogger instance == one timestamped run directory, holding one
    JSON file per pipeline stage (e.g. "step4_detection.json")."""

    def __init__(self, run_name: str = "run", base_dir: Path = LOGS_RUNS_DIR):
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.dir = base_dir / f"{timestamp}_{_slugify(run_name)}"
        self.dir.mkdir(parents=True, exist_ok=True)

    def log_stage(self, stage_name: str, data: Any) -> Path:
        """Serialize `data` (dicts, lists, dataclasses, Path objects, etc.)
        to <run_dir>/<stage_name>.json and return the path written."""
        path = self.dir / f"{_slugify(stage_name)}.json"
        path.write_text(json.dumps(data, indent=2, default=_json_default), encoding="utf-8")
        return path
