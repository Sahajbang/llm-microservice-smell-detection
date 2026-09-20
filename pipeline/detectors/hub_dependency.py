"""
Hub-like Dependency detector.

Implements CLAUDE.md's Phase 2+ item ("Hub-like Dependency detector --
degree/centrality threshold on the same SDG") on the same service
dependency graph the Cyclic Dependency detector uses, so no additional
extraction is required.

Threshold strategy (why it is not a hardcoded number)
-----------------------------------------------------
"High degree" is meaningless in absolute terms: degree 4 is unremarkable
in a 40-service system and dominant in a 5-service one. A service is
flagged only when it clears an absolute floor **and** at least one
size-relative criterion:

1. ``degree >= hub.min_degree`` -- absolute floor. Below this nothing is a
   hub in any useful sense, and it guards against tiny graphs where a
   standard deviation computed from three data points is noise.
2. ``degree >= mean + k * stdev`` -- statistical outlier against the
   graph's own degree distribution, so a system that is uniformly chatty
   does not produce a hub for every service.
3. ``distinct_neighbours >= ratio * (n_services - 1)`` -- directly
   connected to at least this share of all other services. Needed because
   criterion 2 fails exactly when several services are hubs (which
   flattens the distribution and raises the mean). Distinct neighbours,
   not degree, is the right quantity here: a mutual caller/callee pair
   contributes two edges but only one coupled service.

Criteria 2 and 3 are OR-ed; the floor is AND-ed. All four parameters live
in ``AnalysisConfig.hub`` so a run records the thresholds that produced it.

``degree_mode`` selects which degree is tested: ``total`` (default, both
directions), ``in`` (the classic "everything calls this service" hub), or
``out`` (an orchestrator calling everything).

Deliberate exclusions
---------------------
docker-compose ``depends_on`` edges are not in this graph (see
``AnalysisConfig.excluded_edge_sources``). Including them would make
``config-server`` and ``discovery-server`` hubs in every Spring Cloud
repository, which is infrastructure startup ordering, not an architectural
coupling smell.

What the detector cannot decide
-------------------------------
Whether high connectivity is *wrong*. An API gateway or BFF is supposed to
fan out. That judgement is explicitly left to the LLM stage, which is
given the role-relevant evidence; this detector only establishes that the
connectivity is statistically unusual.
"""

from __future__ import annotations

import statistics
from typing import Any

from pipeline.detectors.base import EVIDENCE_DEPENDENCIES, SmellDetector
from pipeline.models import DEFAULT_DEPENDENCY_CONFIDENCE, AnalysisContext, Finding
from pipeline.smells import HUB_DEPENDENCY, get_spec


class HubDependencyDetector(SmellDetector):
    smell = HUB_DEPENDENCY
    required_evidence = (EVIDENCE_DEPENDENCIES,)

    def detect(self, ctx: AnalysisContext) -> list[Finding]:
        graph = ctx.graph
        if graph is None or graph.number_of_nodes() < 2:
            return []

        cfg = ctx.config.hub
        spec = get_spec(self.smell)
        nodes = list(graph.nodes())
        degrees = {n: self._degree(graph, n, cfg.degree_mode) for n in nodes}
        values = list(degrees.values())

        mean = statistics.fmean(values)
        stdev = statistics.pstdev(values) if len(values) > 1 else 0.0
        outlier_threshold = mean + cfg.stdev_multiplier * stdev
        # Minimum distinct neighbours for the connectivity-share criterion.
        neighbour_threshold = cfg.degree_ratio * (len(nodes) - 1)

        findings: list[Finding] = []
        for service, degree in sorted(degrees.items(), key=lambda kv: (-kv[1], kv[0])):
            if degree < cfg.min_degree:
                continue

            callers = sorted(graph.predecessors(service))
            callees = sorted(graph.successors(service))
            neighbours = set(callers) | set(callees)
            # Distinct neighbours, not edge count: a service that both
            # calls and is called by one peer is coupled to one service,
            # not two.
            share = len(neighbours) / (len(nodes) - 1) if len(nodes) > 1 else 0.0

            is_outlier = degree >= outlier_threshold
            is_large_share = len(neighbours) >= neighbour_threshold
            if not (is_outlier or is_large_share):
                continue

            evidence = self._edges_touching(graph, service)
            in_degree = graph.in_degree(service)
            out_degree = graph.out_degree(service)
            confidences = [e.get("confidence", DEFAULT_DEPENDENCY_CONFIDENCE) for e in evidence]

            findings.append(
                Finding(
                    smell=self.smell,
                    smell_name=spec.name,
                    key=f"{self.smell}:{service}",
                    services=[service, *sorted(neighbours)],
                    files=sorted({e["file"] for e in evidence}),
                    severity=self._severity(share),
                    confidence=round(
                        statistics.fmean(confidences) if confidences else DEFAULT_DEPENDENCY_CONFIDENCE, 3
                    ),
                    description=(
                        f"{service} is directly connected to {len(neighbours)} of "
                        f"{len(nodes) - 1} other services ({degree} {cfg.degree_mode}-degree: "
                        f"{in_degree} inbound, {out_degree} outbound), against a graph mean of "
                        f"{mean:.2f}. Triggered by: "
                        f"{'statistical outlier' if is_outlier else ''}"
                        f"{' and ' if is_outlier and is_large_share else ''}"
                        f"{'connectivity share' if is_large_share else ''}."
                    ),
                    evidence=evidence,
                    metrics=self._metrics(
                        service=service,
                        degree=degree,
                        in_degree=in_degree,
                        out_degree=out_degree,
                        neighbour_count=len(neighbours),
                        callers=callers,
                        callees=callees,
                        mean=mean,
                        stdev=stdev,
                        outlier_threshold=outlier_threshold,
                        neighbour_threshold=neighbour_threshold,
                        share=share,
                        is_outlier=is_outlier,
                        is_large_share=is_large_share,
                        node_count=len(nodes),
                        cfg=cfg,
                    ),
                )
            )
        return findings

    @staticmethod
    def _degree(graph: Any, node: str, mode: str) -> int:
        if mode == "in":
            return int(graph.in_degree(node))
        if mode == "out":
            return int(graph.out_degree(node))
        return int(graph.in_degree(node)) + int(graph.out_degree(node))

    @staticmethod
    def _edges_touching(graph: Any, service: str) -> list[dict]:
        """Every underlying call-site record on an edge into or out of `service`."""
        records: list[dict] = []
        for caller, callee in graph.in_edges(service):
            records.extend(graph[caller][callee]["evidence"])
        for caller, callee in graph.out_edges(service):
            records.extend(graph[caller][callee]["evidence"])
        return records

    @staticmethod
    def _severity(share: float) -> str:
        """Severity from the share of other services the hub touches.

        Connected to three quarters of the system is qualitatively
        different from connected to half of it; below half the finding
        only fired on the statistical criterion and is weaker.
        """
        if share >= 0.75:
            return "HIGH"
        if share >= 0.5:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _metrics(**kw: Any) -> dict[str, Any]:
        cfg = kw.pop("cfg")
        return {
            **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in kw.items()},
            "degree_mode": cfg.degree_mode,
            "thresholds": {
                "min_degree": cfg.min_degree,
                "stdev_multiplier": cfg.stdev_multiplier,
                "degree_ratio": cfg.degree_ratio,
            },
        }
