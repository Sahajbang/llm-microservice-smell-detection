"""Cyclic Dependency detector tests.

Covers the cases CLAUDE.md's Step 3 guidance calls for (known cycle, no
cycle) plus the multi-smell refactor's additions: canonical representation,
multi-node and multiple cycles, duplicate edges, and the finding shape the
LLM stage consumes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.config import default_config
from pipeline.detectors.cyclic_dependency import CyclicDependencyDetector
from pipeline.graph_analysis import build_graph, canonicalize_cycle, detect_cycles
from pipeline.models import AnalysisContext, EvidenceBundle, RepositoryInfo


def edge(caller: str, callee: str, source: str = "feign", confidence: float = 0.95) -> dict:
    return {
        "caller": caller,
        "callee": callee,
        "source": source,
        "file": f"{caller}.java",
        "line": 1,
        "evidence": f"{caller}->{callee}",
        "confidence": confidence,
        "metadata": {},
    }


def make_ctx(edges: list[dict], tmp_path: Path) -> AnalysisContext:
    config = default_config()
    bundle = EvidenceBundle(dependencies=edges)
    graph = build_graph(edges, exclude_sources=frozenset(config.excluded_edge_sources))
    return AnalysisContext(
        repo_root=tmp_path,
        repository=RepositoryInfo(source=str(tmp_path), kind="local", root=str(tmp_path), name="t"),
        evidence=bundle,
        config=config,
        graph=graph,
    )


# -- canonicalization ---------------------------------------------------------


def test_canonicalize_rotates_to_smallest_node():
    assert canonicalize_cycle(["b", "a"]) == ["a", "b"]
    assert canonicalize_cycle(["b", "a", "b"]) == ["a", "b"]
    assert canonicalize_cycle(["c", "a", "b"]) == ["a", "b", "c"]


def test_canonicalize_preserves_direction():
    """a -> c -> b must not be silently reordered into a -> b -> c."""
    assert canonicalize_cycle(["a", "c", "b"]) == ["a", "c", "b"]


def test_canonicalize_handles_empty_and_single():
    assert canonicalize_cycle([]) == []
    assert canonicalize_cycle(["a"]) == ["a"]


def test_equivalent_rotations_produce_identical_output():
    """The same cycle fed in two rotations must detect identically."""
    forward = build_graph([edge("a", "b"), edge("b", "a")])
    reversed_order = build_graph([edge("b", "a"), edge("a", "b")])
    assert detect_cycles(forward) == detect_cycles(reversed_order)
    assert detect_cycles(forward)[0]["cycle"] == ["a", "b", "a"]


# -- detection ----------------------------------------------------------------


def test_detects_simple_two_node_cycle(tmp_path: Path):
    ctx = make_ctx([edge("a", "b"), edge("b", "a")], tmp_path)
    findings = CyclicDependencyDetector().detect(ctx)

    assert len(findings) == 1
    assert findings[0].services == ["a", "b"]
    assert findings[0].severity == "HIGH"
    assert findings[0].metrics["cycle_length"] == 2
    assert findings[0].smell == "cyclic_dependency"


def test_detects_three_node_cycle_with_medium_severity(tmp_path: Path):
    ctx = make_ctx([edge("a", "b"), edge("b", "c"), edge("c", "a")], tmp_path)
    findings = CyclicDependencyDetector().detect(ctx)

    assert len(findings) == 1
    assert findings[0].metrics["cycle"] == ["a", "b", "c", "a"]
    assert findings[0].severity == "MEDIUM"


def test_detects_multiple_independent_cycles(tmp_path: Path):
    edges = [edge("a", "b"), edge("b", "a"), edge("x", "y"), edge("y", "x")]
    findings = CyclicDependencyDetector().detect(make_ctx(edges, tmp_path))

    assert len(findings) == 2
    assert {tuple(f.services) for f in findings} == {("a", "b"), ("x", "y")}


def test_no_cycle_produces_no_findings(tmp_path: Path):
    edges = [edge("gateway", "a"), edge("gateway", "b"), edge("a", "c")]
    assert CyclicDependencyDetector().detect(make_ctx(edges, tmp_path)) == []


def test_duplicate_edges_collapse_but_keep_all_evidence(tmp_path: Path):
    edges = [
        edge("a", "b", source="feign"),
        {**edge("a", "b", source="resttemplate"), "file": "Other.java", "line": 9},
        edge("b", "a"),
    ]
    findings = CyclicDependencyDetector().detect(make_ctx(edges, tmp_path))

    assert len(findings) == 1, "duplicate edges must not create duplicate cycles"
    assert findings[0].metrics["call_sites"] == 3
    assert set(findings[0].metrics["edge_sources"]) == {"feign", "resttemplate"}


def test_docker_compose_edges_do_not_create_cycles(tmp_path: Path):
    """Startup ordering is not request-flow coupling; excluded by default."""
    edges = [
        edge("a", "b", source="docker-compose"),
        edge("b", "a", source="docker-compose"),
    ]
    assert CyclicDependencyDetector().detect(make_ctx(edges, tmp_path)) == []


def test_confidence_is_the_weakest_link_in_the_cycle(tmp_path: Path):
    """A cycle is only as real as its least certain edge."""
    edges = [
        edge("a", "b", source="feign", confidence=0.95),
        edge("b", "a", source="http-literal", confidence=0.6),
    ]
    findings = CyclicDependencyDetector().detect(make_ctx(edges, tmp_path))
    assert findings[0].confidence == pytest.approx(0.6)


def test_finding_carries_files_and_evidence_for_the_llm_stage(tmp_path: Path):
    ctx = make_ctx([edge("a", "b"), edge("b", "a")], tmp_path)
    finding = CyclicDependencyDetector().detect(ctx)[0]

    assert finding.files == ["a.java", "b.java"]
    assert len(finding.evidence) == 2
    assert finding.key == "cyclic_dependency:a-b"
    assert "closed dependency loop" in finding.description


def test_detector_returns_empty_without_a_graph(tmp_path: Path):
    ctx = make_ctx([edge("a", "b")], tmp_path)
    ctx.graph = None
    assert CyclicDependencyDetector().detect(ctx) == []
