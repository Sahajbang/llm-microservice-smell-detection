"""
Detector registry and execution.

Detectors are looked up by smell key, so enabling or disabling one is
configuration (``AnalysisConfig.enabled_detectors``), not an edit to the
orchestrator. ``run_detectors`` isolates failures: a detector that raises
records a ``PipelineError`` and the others still produce their findings,
so a bug in Hub-like Dependency cannot cost a run its Cyclic Dependency
results.

A detector whose required evidence is missing is *skipped with a recorded
reason* rather than silently returning nothing, so a run log can
distinguish "no shared persistence exists" from "no persistence evidence
was extracted".
"""

from __future__ import annotations

from typing import Any, Optional

from pipeline.config import AnalysisConfig, default_config
from pipeline.detectors.base import SmellDetector
from pipeline.detectors.cyclic_dependency import CyclicDependencyDetector
from pipeline.detectors.hub_dependency import HubDependencyDetector
from pipeline.detectors.shared_persistence import SharedPersistenceDetector
from pipeline.models import STAGE_DETECTION, AnalysisContext, Finding, PipelineError
from pipeline.smells import CYCLIC_DEPENDENCY, HUB_DEPENDENCY, SHARED_PERSISTENCE

DETECTOR_REGISTRY: dict[str, type[SmellDetector]] = {
    CYCLIC_DEPENDENCY: CyclicDependencyDetector,
    HUB_DEPENDENCY: HubDependencyDetector,
    SHARED_PERSISTENCE: SharedPersistenceDetector,
}

__all__ = [
    "DETECTOR_REGISTRY",
    "SmellDetector",
    "get_detector",
    "run_detectors",
    "CyclicDependencyDetector",
    "HubDependencyDetector",
    "SharedPersistenceDetector",
]


def get_detector(smell_key: str) -> SmellDetector:
    """Instantiate the detector for `smell_key`, or raise for an unknown key."""
    try:
        return DETECTOR_REGISTRY[smell_key]()
    except KeyError:
        raise KeyError(
            f"Unknown detector '{smell_key}'. Known detectors: {sorted(DETECTOR_REGISTRY)}"
        ) from None


def run_detectors(
    ctx: AnalysisContext, config: Optional[AnalysisConfig] = None
) -> tuple[list[Finding], list[PipelineError], list[dict[str, Any]]]:
    """Run every enabled detector against `ctx`.

    Returns ``(findings, errors, statuses)``. ``statuses`` records one
    entry per enabled detector -- ``ok``, ``skipped`` (with the missing
    evidence kind) or ``error`` (with the message) -- so a run can report
    exactly which detectors executed.
    """
    config = config or getattr(ctx, "config", None) or default_config()

    findings: list[Finding] = []
    errors: list[PipelineError] = []
    statuses: list[dict[str, Any]] = []

    for key in config.enabled_detectors:
        try:
            detector = get_detector(key)
        except KeyError as exc:
            errors.append(
                PipelineError(stage=STAGE_DETECTION, component=key, message=str(exc))
            )
            statuses.append({"detector": key, "status": "error", "reason": str(exc), "findings": 0})
            continue

        missing = detector.missing_evidence(ctx)
        if missing:
            statuses.append(
                {
                    "detector": key,
                    "status": "skipped",
                    "reason": f"no {missing} evidence was extracted",
                    "findings": 0,
                }
            )
            continue

        try:
            produced = detector.detect(ctx)
        except Exception as exc:  # noqa: BLE001 - deliberate isolation boundary
            errors.append(
                PipelineError(
                    stage=STAGE_DETECTION,
                    component=key,
                    message=f"{type(exc).__name__}: {exc}",
                )
            )
            statuses.append(
                {"detector": key, "status": "error", "reason": f"{type(exc).__name__}: {exc}", "findings": 0}
            )
            continue

        findings.extend(produced)
        statuses.append({"detector": key, "status": "ok", "findings": len(produced)})

    return findings, errors, statuses
