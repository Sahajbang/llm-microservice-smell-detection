"""End-to-end orchestrator tests.

The whole pipeline runs against a synthetic repository built on disk, with
the LLM mocked and no network access: repository acquisition, extraction,
graph construction, all three detectors, LLM validation, refactoring
proposal, logging and the unified result.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.config import default_config
from pipeline.llm_client import LLMResult
from pipeline.models import STAGE_LLM_DETECTION
from pipeline.run_logger import RunLogger
from pipeline.run_pipeline import analyze_repository, main

DETECTION_JSON = json.dumps(
    {
        "smell": "Some Smell",
        "detected": True,
        "confidence": 0.88,
        "severity": "HIGH",
        "rationale": "Confirmed from the evidence.",
        "refactoring_recommended": True,
    }
)
REFACTORING_JSON = json.dumps(
    {
        "smell": "Some Smell",
        "affected_files": [],
        "changes": [],
        "rationale": "Minimal fix.",
        "expected_impact": "Looser coupling.",
    }
)


def fake_result(content: str) -> LLMResult:
    return LLMResult(
        content=content,
        reasoning="thinking",
        model="test-model",
        system_prompt="sys",
        user_prompt="user",
        temperature=0.2,
        max_tokens=4096,
    )


@pytest.fixture
def logger(tmp_path: Path) -> RunLogger:
    return RunLogger("test-run", base_dir=tmp_path / "runs")


def no_llm_config():
    config = default_config()
    return replace(config, llm=replace(config.llm, enabled=False))


# -- Deterministic-only runs --------------------------------------------------


def test_finds_all_three_smells_in_one_run(multi_smell_repo: Path, logger: RunLogger):
    result = analyze_repository(str(multi_smell_repo), config=no_llm_config(), logger=logger)

    by_smell = result.counts()["findings_by_smell"]
    assert by_smell["cyclic_dependency"] == 1
    assert by_smell["hub_dependency"] == 1
    assert by_smell["shared_persistence"] == 1
    assert result.errors == []


def test_run_result_identifies_each_finding_fully(multi_smell_repo: Path, logger: RunLogger):
    result = analyze_repository(str(multi_smell_repo), config=no_llm_config(), logger=logger)

    for entry in result.results:
        finding = entry.finding
        assert finding.smell and finding.smell_name and finding.key
        assert finding.services
        assert finding.severity in {"LOW", "MEDIUM", "HIGH"}
        assert 0.0 <= finding.confidence <= 1.0
        assert finding.description


def test_cyclic_dependency_still_works_exactly_as_before(multi_smell_repo: Path, logger: RunLogger):
    result = analyze_repository(str(multi_smell_repo), config=no_llm_config(), logger=logger)
    cyclic = [r.finding for r in result.results if r.finding.smell == "cyclic_dependency"][0]

    assert cyclic.metrics["cycle"] == ["order-service", "payment-service", "order-service"]
    assert cyclic.severity == "HIGH"
    assert len(cyclic.evidence) == 2


def test_shared_persistence_finding_names_the_shared_database(multi_smell_repo: Path, logger: RunLogger):
    result = analyze_repository(str(multi_smell_repo), config=no_llm_config(), logger=logger)
    finding = [r.finding for r in result.results if r.finding.smell == "shared_persistence"][0]

    assert finding.services == ["billing-service", "order-service"]
    assert finding.metrics["identity"] == "mysql://db:3306/shopdb"
    assert finding.metrics["shared_tables"] == ["orders"]
    assert finding.severity == "HIGH"


def test_detector_subset_runs_only_requested_smells(multi_smell_repo: Path, logger: RunLogger):
    config = no_llm_config().with_detectors(["cyclic_dependency"])
    result = analyze_repository(str(multi_smell_repo), config=config, logger=logger)

    assert set(result.counts()["findings_by_smell"]) == {"cyclic_dependency"}
    assert result.detectors_run == ["cyclic_dependency"]


def test_no_llm_means_no_llm_calls(multi_smell_repo: Path, logger: RunLogger):
    with patch("pipeline.llm_detection.call_llm") as detect_call:
        result = analyze_repository(str(multi_smell_repo), config=no_llm_config(), logger=logger)

    detect_call.assert_not_called()
    assert result.llm_enabled is False
    assert all(r.llm_detection is None for r in result.results)


def test_clean_repository_produces_no_findings(tmp_path: Path, logger: RunLogger):
    from tests.conftest import write_module

    write_module(tmp_path, "solo-mod", "solo-service")
    result = analyze_repository(str(tmp_path), config=no_llm_config(), logger=logger)

    assert result.results == []
    assert result.counts()["findings"] == 0


# -- Full runs with a mocked LLM ---------------------------------------------


def test_confirmed_findings_get_a_refactoring_proposal(multi_smell_repo: Path, logger: RunLogger):
    with (
        patch("pipeline.llm_detection.call_llm", return_value=fake_result(DETECTION_JSON)),
        patch("pipeline.llm_refactoring.call_llm", return_value=fake_result(REFACTORING_JSON)),
    ):
        result = analyze_repository(str(multi_smell_repo), logger=logger)

    counts = result.counts()
    assert counts["confirmed_by_llm"] == counts["findings"]
    assert counts["refactoring_proposals"] == counts["findings"]
    assert all(r.refactoring is not None for r in result.results)


def test_rejected_finding_gets_no_refactoring(multi_smell_repo: Path, logger: RunLogger):
    rejected = json.dumps(
        {
            "smell": "Some Smell",
            "detected": False,
            "confidence": 0.2,
            "severity": "LOW",
            "rationale": "Expected for a gateway.",
            "refactoring_recommended": False,
        }
    )
    with (
        patch("pipeline.llm_detection.call_llm", return_value=fake_result(rejected)),
        patch("pipeline.llm_refactoring.call_llm") as refactor_call,
    ):
        result = analyze_repository(str(multi_smell_repo), logger=logger)

    refactor_call.assert_not_called()
    assert result.counts()["confirmed_by_llm"] == 0
    assert all(r.refactoring is None for r in result.results)


def test_confirmed_but_not_recommended_skips_refactoring(multi_smell_repo: Path, logger: RunLogger):
    confirmed_only = json.dumps(
        {
            "smell": "Some Smell",
            "detected": True,
            "confidence": 0.7,
            "severity": "LOW",
            "rationale": "Real but minor.",
            "refactoring_recommended": False,
        }
    )
    with (
        patch("pipeline.llm_detection.call_llm", return_value=fake_result(confirmed_only)),
        patch("pipeline.llm_refactoring.call_llm") as refactor_call,
    ):
        result = analyze_repository(str(multi_smell_repo), logger=logger)

    refactor_call.assert_not_called()
    assert result.counts()["confirmed_by_llm"] > 0


# -- Error isolation ----------------------------------------------------------


def test_one_failing_llm_call_does_not_stop_other_findings(multi_smell_repo: Path, logger: RunLogger):
    calls = {"n": 0}

    def flaky(system_prompt, user_prompt, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("model unavailable")
        return fake_result(DETECTION_JSON)

    with (
        patch("pipeline.llm_detection.call_llm", side_effect=flaky),
        patch("pipeline.llm_refactoring.call_llm", return_value=fake_result(REFACTORING_JSON)),
    ):
        result = analyze_repository(str(multi_smell_repo), logger=logger)

    failed = [r for r in result.results if r.errors]
    assert len(failed) == 1
    assert failed[0].errors[0].stage == STAGE_LLM_DETECTION
    assert "model unavailable" in failed[0].errors[0].message
    assert any(r.llm_detection for r in result.results), "other findings must still be processed"


def test_failing_refactoring_keeps_the_detection_verdict(multi_smell_repo: Path, logger: RunLogger):
    with (
        patch("pipeline.llm_detection.call_llm", return_value=fake_result(DETECTION_JSON)),
        patch("pipeline.llm_refactoring.call_llm", side_effect=RuntimeError("refactor boom")),
    ):
        result = analyze_repository(str(multi_smell_repo), logger=logger)

    assert all(r.llm_detection is not None for r in result.results)
    assert all(r.refactoring is None for r in result.results)
    assert all("refactor boom" in r.errors[0].message for r in result.results)


def test_invalid_repository_is_recorded_not_raised(tmp_path: Path, logger: RunLogger):
    result = analyze_repository(str(tmp_path / "missing"), config=no_llm_config(), logger=logger)

    assert result.results == []
    assert result.errors[0].stage == "repository"
    assert (logger.dir / "99_result.json").exists(), "a failed run must still be logged"


def test_low_confidence_findings_can_be_filtered_out_of_llm_stage(multi_smell_repo: Path, logger: RunLogger):
    config = default_config()
    config = replace(config, llm=replace(config.llm, min_finding_confidence=0.99))

    with patch("pipeline.llm_detection.call_llm") as call:
        result = analyze_repository(str(multi_smell_repo), config=config, logger=logger)

    call.assert_not_called()
    assert all(r.errors and "below" in r.errors[0].message for r in result.results)


# -- Logging ------------------------------------------------------------------


def test_run_log_answers_the_reproducibility_questions(multi_smell_repo: Path, logger: RunLogger):
    with (
        patch("pipeline.llm_detection.call_llm", return_value=fake_result(DETECTION_JSON)),
        patch("pipeline.llm_refactoring.call_llm", return_value=fake_result(REFACTORING_JSON)),
    ):
        analyze_repository(str(multi_smell_repo), logger=logger)

    metadata = json.loads((logger.dir / "00_run_metadata.json").read_text(encoding="utf-8"))
    evidence = json.loads((logger.dir / "01_evidence.json").read_text(encoding="utf-8"))
    detection = json.loads((logger.dir / "02_detection.json").read_text(encoding="utf-8"))
    result = json.loads((logger.dir / "99_result.json").read_text(encoding="utf-8"))

    # Which repository, when, with which settings?
    assert metadata["repository"]["root"]
    assert metadata["started_at"]
    assert metadata["config"]["hub"]["min_degree"] == 4
    # What evidence, which detectors, which candidates?
    assert evidence["dependencies"] and evidence["persistence"]
    assert {s["detector"] for s in detection["detector_statuses"]}
    assert detection["findings"]
    # What did the LLM decide, and what was proposed?
    assert result["counts"]["confirmed_by_llm"] >= 1
    assert result["results"][0]["refactoring"]["rationale"] == "Minimal fix."


def test_run_log_never_contains_api_keys(multi_smell_repo: Path, logger: RunLogger, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "super-secret-key-value")
    with (
        patch("pipeline.llm_detection.call_llm", return_value=fake_result(DETECTION_JSON)),
        patch("pipeline.llm_refactoring.call_llm", return_value=fake_result(REFACTORING_JSON)),
    ):
        analyze_repository(str(multi_smell_repo), logger=logger)

    for path in logger.dir.glob("*.json"):
        assert "super-secret-key-value" not in path.read_text(encoding="utf-8")


def test_historical_runs_are_isolated(multi_smell_repo: Path, tmp_path: Path):
    base = tmp_path / "runs"
    first = RunLogger("run-one", base_dir=base)
    second = RunLogger("run-two", base_dir=base)

    analyze_repository(str(multi_smell_repo), config=no_llm_config(), logger=first)
    analyze_repository(str(multi_smell_repo), config=no_llm_config(), logger=second)

    assert first.dir != second.dir
    assert (first.dir / "99_result.json").exists()
    assert (second.dir / "99_result.json").exists()


def test_per_finding_logs_stay_dashboard_compatible(multi_smell_repo: Path, logger: RunLogger):
    """The existing API/frontend expect caller/callee/source on evidence."""
    analyze_repository(str(multi_smell_repo), config=no_llm_config(), logger=logger)

    persistence_log = json.loads(
        next(logger.dir.glob("finding_shared_persistence*.json")).read_text(encoding="utf-8")
    )
    assert persistence_log["cycle"], "dashboard reads a 'cycle' list"
    for record in persistence_log["evidence"]:
        assert "caller" in record and "callee" in record and "source" in record


# -- CLI ----------------------------------------------------------------------


def test_cli_runs_end_to_end_and_writes_result(multi_smell_repo: Path, tmp_path: Path, capsys, monkeypatch):
    # Keep the test out of the project's real logs/runs/ directory.
    monkeypatch.setattr(
        "pipeline.run_pipeline.RunLogger",
        lambda name, base_dir=tmp_path / "runs": RunLogger(name, base_dir=base_dir),
    )
    output = tmp_path / "result.json"
    exit_code = main([str(multi_smell_repo), "--no-llm", "--output", str(output), "--run-name", "cli-test"])

    assert exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["counts"]["findings"] == 3
    assert payload["repository"]["kind"] == "local"
    assert "run_id" in capsys.readouterr().out


def test_cli_rejects_unknown_smell(multi_smell_repo: Path):
    with pytest.raises(SystemExit):
        main([str(multi_smell_repo), "--no-llm", "--smells", "not_a_smell"])
