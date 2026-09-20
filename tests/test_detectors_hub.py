"""Hub-like Dependency detector tests.

Exercises the documented threshold strategy: the absolute floor, the
statistical-outlier criterion, the connectivity-share criterion, and the
cases each one exists to handle (balanced graphs, isolated nodes, small
graphs where a standard deviation would otherwise fire spuriously).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from pipeline.config import HubConfig, default_config
from pipeline.detectors.hub_dependency import HubDependencyDetector
from pipeline.graph_analysis import build_graph
from pipeline.models import AnalysisContext, EvidenceBundle, RepositoryInfo


def edge(caller: str, callee: str, source: str = "feign", confidence: float = 0.95) -> dict:
    return {
        "caller": caller,
        "callee": callee,
        "source": source,
        "file": f"{caller}-{callee}.java",
        "line": 1,
        "evidence": f"{caller}->{callee}",
        "confidence": confidence,
        "metadata": {},
    }


def make_ctx(edges: list[dict], tmp_path: Path, hub: HubConfig | None = None) -> AnalysisContext:
    config = default_config()
    if hub is not None:
        config = replace(config, hub=hub)
    graph = build_graph(edges, exclude_sources=frozenset(config.excluded_edge_sources))
    return AnalysisContext(
        repo_root=tmp_path,
        repository=RepositoryInfo(source=str(tmp_path), kind="local", root=str(tmp_path), name="t"),
        evidence=EvidenceBundle(dependencies=edges),
        config=config,
        graph=graph,
    )


def star(center: str, spokes: int, inbound: bool = True) -> list[dict]:
    """A star graph: `spokes` services all calling `center` (or called by it)."""
    return [
        edge(f"svc{i}", center) if inbound else edge(center, f"svc{i}")
        for i in range(spokes)
    ]


# -- obvious hubs -------------------------------------------------------------


def test_detects_obvious_inbound_hub(tmp_path: Path):
    findings = HubDependencyDetector().detect(make_ctx(star("core", 5), tmp_path))

    assert len(findings) == 1
    assert findings[0].services[0] == "core"
    assert findings[0].metrics["in_degree"] == 5
    assert findings[0].metrics["neighbour_count"] == 5
    assert findings[0].severity == "HIGH"


def test_detects_obvious_outbound_hub(tmp_path: Path):
    findings = HubDependencyDetector().detect(make_ctx(star("orchestrator", 5, inbound=False), tmp_path))
    assert [f.services[0] for f in findings] == ["orchestrator"]


def test_hub_finding_includes_neighbours_and_call_site_evidence(tmp_path: Path):
    finding = HubDependencyDetector().detect(make_ctx(star("core", 4), tmp_path))[0]

    assert set(finding.services[1:]) == {"svc0", "svc1", "svc2", "svc3"}
    assert len(finding.evidence) == 4
    assert finding.key == "hub_dependency:core"
    assert finding.metrics["thresholds"]["min_degree"] == 4


# -- no hubs ------------------------------------------------------------------


def test_balanced_graph_has_no_hub(tmp_path: Path):
    """A ring: every service has identical degree, so nothing is an outlier."""
    edges = [edge("a", "b"), edge("b", "c"), edge("c", "d"), edge("d", "a")]
    assert HubDependencyDetector().detect(make_ctx(edges, tmp_path)) == []


def test_small_graph_below_absolute_floor_has_no_hub(tmp_path: Path):
    """Degree 2 is a statistical outlier here but is not a hub in any real sense."""
    edges = [edge("a", "c"), edge("b", "c")]
    assert HubDependencyDetector().detect(make_ctx(edges, tmp_path)) == []


def test_isolated_nodes_are_not_hubs(tmp_path: Path):
    edges = [*star("core", 4), edge("lonely-a", "lonely-b")]
    findings = HubDependencyDetector().detect(make_ctx(edges, tmp_path))
    assert [f.services[0] for f in findings] == ["core"]


def test_empty_graph_produces_no_findings(tmp_path: Path):
    assert HubDependencyDetector().detect(make_ctx([], tmp_path)) == []


# -- threshold behaviour ------------------------------------------------------

def test_absolute_floor_is_respected_exactly(tmp_path: Path):
    """Degree 3 with floor 4 is not a hub; the same graph with floor 3 is."""
    edges = star("core", 3)
    assert HubDependencyDetector().detect(make_ctx(edges, tmp_path, HubConfig(min_degree=4))) == []

    lowered = HubDependencyDetector().detect(make_ctx(edges, tmp_path, HubConfig(min_degree=3)))
    assert [f.services[0] for f in lowered] == ["core"]


def test_share_criterion_fires_when_distribution_is_flat(tmp_path: Path):
    """Two equally-connected hubs defeat the outlier test, so the share test must carry it."""
    edges = [
        *[edge(f"leaf{i}", "hub-a") for i in range(4)],
        *[edge(f"leaf{i}", "hub-b") for i in range(4)],
    ]
    hub = HubConfig(min_degree=4, stdev_multiplier=99.0, degree_ratio=0.5)
    findings = HubDependencyDetector().detect(make_ctx(edges, tmp_path, hub))

    assert {f.services[0] for f in findings} == {"hub-a", "hub-b"}
    assert all(f.metrics["is_outlier"] is False for f in findings)
    assert all(f.metrics["is_large_share"] is True for f in findings)


def test_outlier_criterion_fires_when_share_is_small(tmp_path: Path):
    """In a large sparse system a hub may touch few peers proportionally but still stand out."""
    edges = [*star("core", 5), *[edge(f"p{i}", f"q{i}") for i in range(12)]]
    hub = HubConfig(min_degree=4, stdev_multiplier=1.5, degree_ratio=0.99)
    findings = HubDependencyDetector().detect(make_ctx(edges, tmp_path, hub))

    assert [f.services[0] for f in findings] == ["core"]
    assert findings[0].metrics["is_outlier"] is True
    assert findings[0].metrics["is_large_share"] is False


def test_degree_mode_in_ignores_outbound_calls(tmp_path: Path):
    """An orchestrator calling five services is not an inbound hub."""
    edges = star("orchestrator", 5, inbound=False)
    hub = HubConfig(degree_mode="in", min_degree=4)
    assert HubDependencyDetector().detect(make_ctx(edges, tmp_path, hub)) == []


def test_mutual_pair_counts_as_one_neighbour_not_two(tmp_path: Path):
    """Bidirectional edges must not inflate the connectivity share."""
    edges = [*star("core", 4), edge("core", "svc0")]
    finding = HubDependencyDetector().detect(make_ctx(edges, tmp_path))[0]

    assert finding.metrics["degree"] == 5
    assert finding.metrics["neighbour_count"] == 4
    assert finding.metrics["share"] <= 1.0


def test_docker_compose_edges_do_not_create_hubs(tmp_path: Path):
    """Otherwise config-server is a hub in every Spring Cloud repository."""
    edges = [edge(f"svc{i}", "config-server", source="docker-compose") for i in range(6)]
    assert HubDependencyDetector().detect(make_ctx(edges, tmp_path)) == []
