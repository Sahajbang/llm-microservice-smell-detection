"""
Cyclic Dependency detector.

Wraps the Phase 1 implementation (``pipeline.graph_analysis``) in the
detector contract without changing its behaviour: the same NetworkX
``simple_cycles`` search over the same service dependency graph, with the
same docker-compose exclusion, and the same per-hop evidence mapping. The
only additions are canonicalized cycles (see
``graph_analysis.canonicalize_cycle``) and a deterministic severity and
confidence prior.

Severity rationale
------------------
Severity here reflects how tightly the cycle binds the services, which is
inversely related to its length: a two-service cycle means each service
directly requires the other, while a longer chain is looser and often
easier to break at one point. This is the deterministic prior only -- the
LLM stage assigns its own severity from the code, and never overwrites
this value.

Confidence rationale
--------------------
The cycle itself is a proven graph property, so uncertainty comes entirely
from the evidence that produced its edges: a cycle made of ``@FeignClient``
declarations is near-certain, one resting on an unclassified URL literal
is not. Confidence is therefore the minimum per-edge confidence along the
cycle (the weakest link determines whether the cycle is real).
"""

from __future__ import annotations

from pipeline.detectors.base import EVIDENCE_DEPENDENCIES, SmellDetector
from pipeline.graph_analysis import cycle_evidence, detect_cycles
from pipeline.models import DEFAULT_DEPENDENCY_CONFIDENCE, AnalysisContext, Finding
from pipeline.smells import CYCLIC_DEPENDENCY, get_spec


class CyclicDependencyDetector(SmellDetector):
    smell = CYCLIC_DEPENDENCY
    required_evidence = (EVIDENCE_DEPENDENCIES,)

    def detect(self, ctx: AnalysisContext) -> list[Finding]:
        if ctx.graph is None:
            return []

        spec = get_spec(self.smell)
        findings: list[Finding] = []

        for entry in detect_cycles(ctx.graph):
            closed = entry["cycle"]
            services = closed[:-1]
            evidence = cycle_evidence(ctx.graph, closed)
            confidences = [e.get("confidence", DEFAULT_DEPENDENCY_CONFIDENCE) for e in evidence]
            confidence = min(confidences) if confidences else DEFAULT_DEPENDENCY_CONFIDENCE

            findings.append(
                Finding(
                    smell=self.smell,
                    smell_name=spec.name,
                    key=f"{self.smell}:{'-'.join(services)}",
                    services=services,
                    files=sorted({e["file"] for e in evidence}),
                    severity=self._severity(len(services)),
                    confidence=round(confidence, 3),
                    description=(
                        f"{' -> '.join(closed)} form a closed dependency loop across "
                        f"{len(services)} services, via {len(evidence)} call site(s)."
                    ),
                    evidence=evidence,
                    metrics={
                        "cycle": closed,
                        "cycle_length": len(services),
                        "call_sites": len(evidence),
                        "edge_sources": sorted({e["source"] for e in evidence}),
                    },
                )
            )
        return findings

    @staticmethod
    def _severity(cycle_length: int) -> str:
        if cycle_length <= 2:
            return "HIGH"
        if cycle_length <= 4:
            return "MEDIUM"
        return "LOW"
