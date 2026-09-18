"""Step 7 tests: per-run logging writes one JSON file per stage."""

import dataclasses
import json

from pipeline.run_logger import RunLogger


@dataclasses.dataclass
class _FakeLLMResult:
    content: str
    reasoning: str


def test_log_stage_writes_readable_json(tmp_path):
    logger = RunLogger("unit-test run!!", base_dir=tmp_path)

    path = logger.log_stage("Step 4: Detection", {"cycle": ["a", "b", "a"], "result": _FakeLLMResult("x", "y")})

    assert path.exists()
    assert path.parent == logger.dir
    assert path.name == "step-4-detection.json"

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["cycle"] == ["a", "b", "a"]
    assert loaded["result"] == {"content": "x", "reasoning": "y"}


def test_run_directory_is_timestamped_and_slugified(tmp_path):
    logger = RunLogger("Step4 Detection", base_dir=tmp_path)

    assert logger.dir.parent == tmp_path
    assert logger.dir.name.endswith("_step4-detection")
    assert logger.dir.exists()
