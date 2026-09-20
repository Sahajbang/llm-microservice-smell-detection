"""Detector architecture tests.

These cover the properties the pluggable design promises, independently of
any one smell: detectors run independently, one failing detector cannot
corrupt another's results, missing evidence is a recorded skip rather than
a silent empty result, and unknown smell keys fail loudly.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from pipeline.config import ALL_DETECTORS, default_config
from pipeline.detectors import DETECTOR_REGISTRY, get_detector, run_detectors
from pipeline.detectors.base import SmellDetector
from pipeline.graph_analysis import build_graph
from pipeline.models import AnalysisContext, EvidenceBundle, PersistenceEvidence, RepositoryInfo
from pipeline.smells import SPECS


def edge(caller: str, callee: str) -> dict:
    return {
        "caller": caller,
        "callee": callee,
        "source": "feign",
        "file": f"{caller}.java",
        "line": 1,
        "evidence": f"{caller}->{callee}",
        "confidence": 0.95,
        "metadata": {},
    }


def make_ctx(tmp_path: Path, *, dependencies=None, persistence=None, config=None) -> AnalysisContext:
    config = config or default_config()
    dependencies = dependencies if dependencies is not None else [edge("a", "b"), edge("b", "a")]
    bundle = EvidenceBundle(dependencies=dependencies, persistence=persistence or [])
    return AnalysisContext(
        repo_root=tmp_path,
        repository=RepositoryInfo(source=str(tmp_path), kind="local", root=str(tmp_path), name="t"),
        evidence=bundle,
        config=config,
        graph=build_graph(dependencies, exclude_sources=frozenset(config.excluded_edge_sources)),
    )


# -- Registry integrity -------------------------------------------------------


def test_every_configured_detector_is_registered():
    assert set(ALL_DETECTORS) == set(DETECTOR_REGISTRY)


def test_every_registered_detector_has_a_smell_spec():
    """A detector without a spec would crash the LLM stage at prompt time."""
    for key, detector_cls in DETECTOR_REGISTRY.items():
        assert key in SPECS, f"{key} has no smell spec"
        assert detector_cls.smell == key


def test_every_detector_declares_its_required_evidence():
    for detector_cls in DETECTOR_REGISTRY.values():
        assert detector_cls.required_evidence, f"{detector_cls.__name__} declares no required evidence"


def test_get_detector_returns_an_instance():
    detector = get_detector("cyclic_dependency")
    assert isinstance(detector, SmellDetector)


def test_unknown_smell_key_fails_loudly():
    with pytest.raises(KeyError, match="Unknown detector"):
        get_detector("not_a_real_smell")


def test_unknown_smell_in_config_is_rejected_early():
    with pytest.raises(ValueError, match="Unknown detector"):
        default_config().with_detectors(["cyclic_dependency", "made_up_smell"])


# -- Execution ----------------------------------------------------------------


def test_detectors_run_independently(tmp_path: Path):
    ctx = make_ctx(tmp_path)
    findings, errors, statuses = run_detectors(ctx)

    assert errors == []
    assert {s["detector"] for s in statuses} == set(ALL_DETECTORS)
    assert any(f.smell == "cyclic_dependency" for f in findings)


def test_selecting_a_subset_runs_only_those_detectors(tmp_path: Path):
    config = default_config().with_detectors(["cyclic_dependency"])
    findings, _, statuses = run_detectors(make_ctx(tmp_path, config=config), config)

    assert [s["detector"] for s in statuses] == ["cyclic_dependency"]
    assert {f.smell for f in findings} == {"cyclic_dependency"}


def test_missing_evidence_is_a_recorded_skip_not_a_silent_empty(tmp_path: Path):
    """"No persistence evidence extracted" must be distinguishable from "no sharing"."""
    ctx = make_ctx(tmp_path, persistence=[])
    _, _, statuses = run_detectors(ctx)

    skipped = [s for s in statuses if s["detector"] == "shared_persistence"][0]
    assert skipped["status"] == "skipped"
    assert "persistence" in skipped["reason"]


def test_detector_with_evidence_reports_ok_even_with_no_findings(tmp_path: Path):
    record = PersistenceEvidence(
        service="a", kind="datasource", value="mysql://db/one", file="a.yml", line=1, evidence="url"
    )
    ctx = make_ctx(tmp_path, persistence=[record])
    _, _, statuses = run_detectors(ctx)

    status = [s for s in statuses if s["detector"] == "shared_persistence"][0]
    assert status["status"] == "ok"
    assert status["findings"] == 0


def test_one_failing_detector_does_not_corrupt_the_others(tmp_path: Path, monkeypatch):
    """A crash in Hub-like Dependency must not cost the run its cycle findings."""

    def boom(self, ctx):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(
        "pipeline.detectors.hub_dependency.HubDependencyDetector.detect", boom
    )

    findings, errors, statuses = run_detectors(make_ctx(tmp_path))

    assert any(f.smell == "cyclic_dependency" for f in findings), "unrelated results must survive"
    assert [e.component for e in errors] == ["hub_dependency"]
    assert "detector exploded" in errors[0].message
    assert [s["status"] for s in statuses if s["detector"] == "hub_dependency"] == ["error"]


def test_detector_failure_is_recorded_not_swallowed(tmp_path: Path, monkeypatch):
    def boom(self, ctx):
        raise ValueError("bad evidence")

    monkeypatch.setattr(
        "pipeline.detectors.cyclic_dependency.CyclicDependencyDetector.detect", boom
    )
    _, errors, _ = run_detectors(make_ctx(tmp_path))

    assert len(errors) == 1
    assert errors[0].stage == "detection"
    assert "ValueError" in errors[0].message


def test_unknown_detector_configured_at_runtime_is_isolated(tmp_path: Path):
    """Bypassing with_detectors() validation must still not crash a run."""
    config = replace(default_config(), enabled_detectors=("cyclic_dependency", "ghost_smell"))
    findings, errors, statuses = run_detectors(make_ctx(tmp_path, config=config), config)

    assert any(f.smell == "cyclic_dependency" for f in findings)
    assert [e.component for e in errors] == ["ghost_smell"]
    assert [s["status"] for s in statuses if s["detector"] == "ghost_smell"] == ["error"]
