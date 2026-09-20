"""Dashboard analysis-control tests.

Covers the trust boundary the CLI does not have -- an HTTP body deciding
which repository gets analyzed -- plus the job lifecycle and the mapping
from logged pipeline stages onto the progress the UI renders.

No real Git and no real LLM: the pipeline entry point is replaced with a
fake, so these tests run offline.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from api import jobs
from pipeline.models import AnalysisResult, RepositoryInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def clean_registry():
    """Each test starts with an empty job registry."""
    jobs._jobs.clear()
    jobs._active = None
    yield
    jobs._jobs.clear()
    jobs._active = None


def wait_for(job: jobs.Job, timeout: float = 5.0) -> jobs.Job:
    deadline = time.time() + timeout
    while job.status == "running" and time.time() < deadline:
        time.sleep(0.02)
    assert job.status != "running", "job did not finish in time"
    return job


# -- source validation (trust boundary) ---------------------------------------


def test_accepts_a_local_path_inside_the_project():
    assert jobs.resolve_source("target-repo") == str(PROJECT_ROOT / "target-repo")


@pytest.mark.parametrize(
    "source",
    [
        "../..",
        "../../Windows",
        "target-repo/../../..",
        "C:/Windows",
        "/etc",
    ],
)
def test_rejects_paths_outside_the_project(source: str):
    """The endpoint must not become a way to read arbitrary directories."""
    with pytest.raises(jobs.JobError):
        jobs.resolve_source(source)


def test_rejects_empty_source():
    with pytest.raises(jobs.JobError):
        jobs.resolve_source("   ")


def test_rejects_a_local_path_that_does_not_exist():
    with pytest.raises(jobs.JobError, match="No such directory"):
        jobs.resolve_source("no-such-repo-here")


def test_accepts_an_https_git_url():
    assert jobs.resolve_source("https://github.com/owner/repo.git") == "https://github.com/owner/repo.git"


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:owner/repo.git",
        "ssh://git@github.com/owner/repo.git",
        "git://github.com/owner/repo.git",
    ],
)
def test_rejects_non_https_git_urls(url: str):
    with pytest.raises(jobs.JobError):
        jobs.resolve_source(url)


def test_rejects_unknown_smells():
    with pytest.raises(jobs.JobError, match="Unknown smell"):
        jobs.resolve_smells(["cyclic_dependency", "made_up_smell"])


def test_no_smells_means_all_detectors():
    assert jobs.resolve_smells([]) is None
    assert jobs.resolve_smells(None) is None


# -- stage -> progress mapping ------------------------------------------------


def test_metadata_stage_reports_the_repository():
    phase, label, detail = jobs.describe_stage(
        "00_run_metadata", {"repository": {"name": "shop", "branch": "main", "commit": "abc123def456789"}}
    )
    assert phase == "acquire"
    assert "shop" in label
    assert detail == "abc123def456"


def test_evidence_stage_reports_real_counts():
    phase, label, detail = jobs.describe_stage(
        "01_evidence", {"dependencies": [{}, {}, {}], "persistence": [{}], "services": ["a", "b"]}
    )
    assert phase == "extract"
    assert "3 dependency edges" in label and "1 persistence" in label
    assert "2 services" in detail


def test_detection_stage_names_the_detectors_that_ran():
    phase, label, detail = jobs.describe_stage(
        "02_detection",
        {
            "findings": [{}, {}],
            "detector_statuses": [
                {"detector": "cyclic_dependency", "status": "ok"},
                {"detector": "shared_persistence", "status": "skipped"},
            ],
        },
    )
    assert phase == "detect"
    assert "2 candidate" in label
    assert "cyclic_dependency" in detail and "shared_persistence" not in detail


def test_llm_verdict_distinguishes_confirmed_from_dismissed():
    confirmed = jobs.describe_stage(
        "detection_x", {"result": {"detected": True, "severity": "HIGH", "confidence": 0.9, "smell": "Cyclic"}}
    )
    dismissed = jobs.describe_stage("detection_x", {"result": {"detected": False, "smell": "Cyclic"}})
    assert confirmed[0] == "validate" and "confirmed" in confirmed[1]
    assert "dismissed" in dismissed[1]
    assert dismissed[2] is None


def test_refactoring_stage_counts_affected_files():
    phase, label, _ = jobs.describe_stage("refactoring_x", {"result": {"affected_files": ["a.java", "b.java"]}})
    assert phase == "refactor" and "2 file" in label


def test_result_stage_completes_the_run():
    phase, label, detail = jobs.describe_stage(
        "99_result", {"counts": {"findings": 3, "confirmed_by_llm": 2, "refactoring_proposals": 1}}
    )
    assert phase == "complete"
    assert "3 finding" in label and "2 confirmed" in label
    assert "1 refactoring" in detail


def test_unknown_stage_does_not_raise():
    assert jobs.describe_stage("something_new", {"unexpected": True})[0] in dict(jobs.PHASES)


def test_every_described_phase_is_a_real_station():
    """A label mapped to a phase the UI does not render would be invisible."""
    known = set(dict(jobs.PHASES))
    for stage in ("00_run_metadata", "01_evidence", "02_detection", "finding_x", "detection_x", "refactoring_x", "99_result"):
        assert jobs.describe_stage(stage, {})[0] in known


# -- job lifecycle ------------------------------------------------------------


def fake_result(run_id: str, *, findings: int = 0, errors: list | None = None) -> AnalysisResult:
    result = AnalysisResult(run_id=run_id, started_at="2026-01-01T00:00:00Z")
    result.repository = RepositoryInfo(source="x", kind="local", root="x", name="x")
    result.errors = errors or []
    return result


def test_job_runs_the_pipeline_and_records_progress(monkeypatch, tmp_path):
    import pipeline.run_pipeline as rp

    def fake_analyze(source, *, branch=None, config=None, logger=None, run_name="", keep_clone=False):
        logger.log_stage("01_evidence", {"dependencies": [{}], "persistence": [], "services": ["a"]})
        logger.log_stage("99_result", {"counts": {"findings": 0, "confirmed_by_llm": 0, "refactoring_proposals": 0}})
        return fake_result(logger.dir.name)

    monkeypatch.setattr(rp, "analyze_repository", fake_analyze)

    job = wait_for(jobs.start_job("target-repo", llm=False, log_dir=tmp_path))

    assert job.status == "done"
    assert job.phase == "complete"
    assert job.run_id and (tmp_path / job.run_id).is_dir()
    assert any("1 dependency edges" in e["label"] for e in job.events)
    assert job.counts == {
        "findings": 0,
        "findings_by_smell": {},
        "confirmed_by_llm": 0,
        "refactoring_proposals": 0,
        "errors": 0,
    }


def test_job_records_a_pipeline_crash_instead_of_dying(monkeypatch, tmp_path):
    import pipeline.run_pipeline as rp

    def boom(*args, **kwargs):
        raise RuntimeError("extractor exploded")

    monkeypatch.setattr(rp, "analyze_repository", boom)

    job = wait_for(jobs.start_job("target-repo", llm=False, log_dir=tmp_path))

    assert job.status == "failed"
    assert "extractor exploded" in job.error
    assert job.finished_at is not None


def test_only_one_analysis_runs_at_a_time(monkeypatch, tmp_path):
    import pipeline.run_pipeline as rp

    release = __import__("threading").Event()

    def slow_analyze(source, *, branch=None, config=None, logger=None, run_name="", keep_clone=False):
        release.wait(timeout=5)
        return fake_result(logger.dir.name)

    monkeypatch.setattr(rp, "analyze_repository", slow_analyze)

    first = jobs.start_job("target-repo", llm=False, log_dir=tmp_path)
    try:
        with pytest.raises(RuntimeError, match="already running"):
            jobs.start_job("target-repo", llm=False, log_dir=tmp_path)
    finally:
        release.set()
    wait_for(first)

    # Once it finishes, the next request is accepted again.
    second = wait_for(jobs.start_job("target-repo", llm=False, log_dir=tmp_path))
    assert second.id != first.id


def test_selected_smells_reach_the_pipeline_config(monkeypatch, tmp_path):
    import pipeline.run_pipeline as rp

    seen = {}

    def capture(source, *, branch=None, config=None, logger=None, run_name="", keep_clone=False):
        seen["detectors"] = config.enabled_detectors
        seen["llm"] = config.llm.enabled
        return fake_result(logger.dir.name)

    monkeypatch.setattr(rp, "analyze_repository", capture)

    wait_for(jobs.start_job("target-repo", smells=["hub_dependency"], llm=False, log_dir=tmp_path))
    assert seen == {"detectors": ("hub_dependency",), "llm": False}


def test_job_payload_carries_no_absolute_local_path(monkeypatch, tmp_path):
    """The browser sees what it asked for, not the resolved filesystem path."""
    import pipeline.run_pipeline as rp

    monkeypatch.setattr(rp, "analyze_repository", lambda source, **kw: fake_result(kw["logger"].dir.name))
    job = wait_for(jobs.start_job("target-repo", llm=False, log_dir=tmp_path))
    assert job.to_dict()["source"] == "target-repo"
