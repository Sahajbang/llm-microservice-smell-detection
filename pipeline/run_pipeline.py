"""
Pipeline orchestrator: one command, one repository, all enabled smells.

    repository acquisition (Git URL or local path)
      -> evidence extraction (REST, docker-compose, persistence)
      -> service dependency graph
      -> deterministic smell detectors
      -> candidate findings
      -> LLM validation, per finding
      -> LLM refactoring proposal, per confirmed finding
      -> per-run logging
      -> unified structured result

This module coordinates; it contains no smell-specific logic. Detectors
come from the registry (:mod:`pipeline.detectors`), prompts from the smell
registry (:mod:`pipeline.smells`), thresholds from configuration
(:mod:`pipeline.config`). Adding a smell does not require editing this
file.

Error isolation is deliberate at every stage: a failed extractor, a failed
detector, or a failed LLM call for one finding is recorded on the result
and the run continues. Nothing is swallowed silently -- every failure ends
up in ``result.errors`` or on the individual finding.

Usage::

    python -m pipeline.run_pipeline target-repo
    python -m pipeline.run_pipeline https://github.com/owner/repo.git
    python -m pipeline.run_pipeline target-repo --no-llm --smells cyclic_dependency
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pipeline.config import ALL_DETECTORS, AnalysisConfig, default_config
from pipeline.detectors import run_detectors
from pipeline.extractors import collect_evidence
from pipeline.graph_analysis import build_graph
from pipeline.llm_detection import detect_finding
from pipeline.llm_refactoring import propose_for_finding
from pipeline.models import (
    STAGE_LLM_DETECTION,
    STAGE_LLM_REFACTORING,
    STAGE_REPOSITORY,
    AnalysisContext,
    AnalysisResult,
    Finding,
    FindingResult,
    PipelineError,
    RepositoryInfo,
    json_default,
)
from pipeline.repository import RepositoryError, acquire_repository
from pipeline.run_logger import RunLogger

_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _legacy_evidence(finding: Finding) -> list[dict]:
    """Evidence reshaped so the existing dashboard can render any smell.

    The Phase 1 API and frontend expect ``caller``/``callee``/``source`` on
    every evidence record. Persistence records carry ``service``/``value``/
    ``kind`` instead, so they are projected onto those names for the log's
    backward-compatible ``evidence`` field. The authoritative, unmodified
    evidence is always present under ``finding.evidence``.
    """
    projected: list[dict] = []
    for record in finding.evidence:
        if "caller" in record and "callee" in record:
            projected.append(record)
        elif "service" in record:
            projected.append(
                {
                    **record,
                    "caller": record.get("service"),
                    "callee": record.get("value"),
                    "source": record.get("kind", "persistence"),
                }
            )
        else:
            projected.append(record)
    return projected


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    """Highest severity first, then highest confidence, then stable by key."""
    return sorted(
        findings,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), -f.confidence, f.key),
    )


def analyze_repository(
    source: str,
    *,
    branch: Optional[str] = None,
    config: Optional[AnalysisConfig] = None,
    logger: Optional[RunLogger] = None,
    run_name: str = "pipeline",
    keep_clone: bool = False,
) -> AnalysisResult:
    """Run the full pipeline against one repository and return the result.

    `source` may be a Git URL or a local path; both are normalized to a
    local working tree by :mod:`pipeline.repository`.
    """
    config = config or default_config()
    logger = logger if logger is not None else RunLogger(run_name)
    result = AnalysisResult(
        run_id=logger.dir.name,
        started_at=_now(),
        llm_enabled=config.llm.enabled,
    )

    # -- Stage 1: repository ------------------------------------------------
    try:
        workspace = acquire_repository(source, branch=branch, config=config)
    except RepositoryError as exc:
        result.errors.append(
            PipelineError(stage=STAGE_REPOSITORY, component="acquire", message=str(exc))
        )
        result.finished_at = _now()
        logger.log_stage("00_run_metadata", _metadata(result, config, source))
        logger.log_stage("99_result", result.to_dict())
        return result

    result.repository = workspace.info
    logger.log_stage("00_run_metadata", _metadata(result, config, source))

    try:
        _analyze_workspace_tree(
            repo_root=workspace.root,
            repo_info=workspace.info,
            config=config,
            logger=logger,
            result=result,
        )
    finally:
        if not keep_clone and config.repository.cleanup_temporary_clones:
            if not workspace.cleanup():
                result.errors.append(
                    PipelineError(
                        stage=STAGE_REPOSITORY,
                        component="cleanup",
                        message=f"temporary clone could not be removed: {workspace.root}",
                    )
                )
        result.finished_at = _now()
        logger.log_stage("99_result", result.to_dict())

    return result


def _metadata(result: AnalysisResult, config: AnalysisConfig, source: str) -> dict[str, Any]:
    """Run provenance. Deliberately records configuration, never secrets."""
    return {
        "run_id": result.run_id,
        "started_at": result.started_at,
        "requested_source": source,
        "repository": result.repository.to_dict() if result.repository else None,
        "config": config.to_dict(),
    }


def _analyze_workspace_tree(
    *,
    repo_root: Path,
    repo_info: RepositoryInfo,
    config: AnalysisConfig,
    logger: RunLogger,
    result: AnalysisResult,
) -> None:
    """Stages 2-5 against an already-acquired working tree."""
    # -- Stage 2: evidence extraction ---------------------------------------
    evidence = collect_evidence(repo_root, config)
    result.errors.extend(evidence.errors)
    result.extraction_summary = evidence.summary()
    logger.log_stage("01_evidence", evidence.to_dict())

    # -- Stage 3: graph ------------------------------------------------------
    graph = build_graph(evidence.dependencies, exclude_sources=frozenset(config.excluded_edge_sources))
    result.graph_summary = {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "excluded_edge_sources": sorted(config.excluded_edge_sources),
        "services": sorted(graph.nodes()),
    }

    ctx = AnalysisContext(
        repo_root=repo_root,
        repository=repo_info,
        evidence=evidence,
        config=config,
        graph=graph,
    )

    # -- Stage 4: deterministic detectors -----------------------------------
    findings, detector_errors, statuses = run_detectors(ctx, config)
    result.errors.extend(detector_errors)
    result.detectors_run = [s["detector"] for s in statuses if s["status"] == "ok"]
    logger.log_stage(
        "02_detection",
        {
            "detector_statuses": statuses,
            "graph_summary": result.graph_summary,
            "findings": [f.to_dict() for f in _sort_findings(findings)],
        },
    )

    # -- Stage 5: LLM validation + refactoring, per finding -----------------
    for finding in _sort_findings(findings):
        result.results.append(_process_finding(finding, ctx, config, logger))


def _process_finding(
    finding: Finding,
    ctx: AnalysisContext,
    config: AnalysisConfig,
    logger: RunLogger,
) -> FindingResult:
    """Validate one finding with the LLM and, if warranted, ask for a fix.

    Failures are attached to this finding only -- one bad LLM response
    never stops the other findings from being processed.
    """
    entry = FindingResult(finding=finding)
    logger.log_stage(
        f"finding_{finding.key}",
        {
            # Back-compat fields so the existing dashboard can render this
            # run without frontend changes; see _legacy_evidence.
            "cycle": finding.metrics.get("cycle") or finding.services,
            "evidence": _legacy_evidence(finding),
            "finding": finding.to_dict(),
        },
    )

    if not config.llm.enabled:
        return entry
    if finding.confidence < config.llm.min_finding_confidence:
        entry.errors.append(
            PipelineError(
                stage=STAGE_LLM_DETECTION,
                component=finding.key,
                message=(
                    f"skipped: deterministic confidence {finding.confidence} is below "
                    f"llm.min_finding_confidence {config.llm.min_finding_confidence}"
                ),
            )
        )
        return entry

    try:
        entry.llm_detection = detect_finding(
            finding,
            ctx.repo_root,
            max_retries=config.llm.max_json_retries,
            temperature=config.llm.temperature,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001 - per-finding isolation boundary
        entry.errors.append(
            PipelineError(
                stage=STAGE_LLM_DETECTION,
                component=finding.key,
                message=f"{type(exc).__name__}: {exc}",
            )
        )
        return entry

    if not entry.confirmed:
        return entry
    if config.llm.refactor_only_when_recommended and not entry.llm_detection.get("refactoring_recommended"):
        return entry

    try:
        entry.refactoring = propose_for_finding(
            finding,
            entry.llm_detection,
            ctx.repo_root,
            max_retries=config.llm.max_json_retries,
            temperature=config.llm.temperature,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001 - per-finding isolation boundary
        entry.errors.append(
            PipelineError(
                stage=STAGE_LLM_REFACTORING,
                component=finding.key,
                message=f"{type(exc).__name__}: {exc}",
            )
        )

    return entry


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the full multi-smell analysis pipeline against a repository."
    )
    parser.add_argument(
        "repository",
        help="Git URL (https://github.com/owner/repo[.git]) or a local repository path",
    )
    parser.add_argument("--branch", default=None, help="Branch to check out (Git URLs only)")
    parser.add_argument(
        "--smells",
        nargs="+",
        default=None,
        metavar="SMELL",
        help=f"Detectors to run (default: all). Choices: {', '.join(ALL_DETECTORS)}",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Run deterministic detection only; skip all LLM calls (no API key needed)",
    )
    parser.add_argument("--run-name", default="pipeline", help="Label for the run log directory")
    parser.add_argument("--output", type=Path, default=None, help="Also write the result JSON here")
    parser.add_argument(
        "--keep-clone",
        action="store_true",
        help="Keep a cloned repository in the workspace instead of deleting it after the run",
    )
    args = parser.parse_args(argv)

    config = default_config()
    try:
        config = config.with_detectors(args.smells)
    except ValueError as exc:
        parser.error(str(exc))

    if args.no_llm:
        from dataclasses import replace

        config = replace(config, llm=replace(config.llm, enabled=False))

    logger = RunLogger(args.run_name)
    result = analyze_repository(
        args.repository,
        branch=args.branch,
        config=config,
        logger=logger,
        keep_clone=args.keep_clone,
    )

    payload = result.to_dict()
    if args.output:
        args.output.write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")

    print(json.dumps({"run_id": result.run_id, **payload["counts"]}, indent=2))
    print(f"\nRun log written to {logger.dir}")
    if args.output:
        print(f"Result JSON written to {args.output}")

    return 1 if result.errors and not result.results else 0


if __name__ == "__main__":
    sys.exit(main())
