"""
Pipeline orchestrator: one command, one repository, all enabled smells.

    repository acquisition (Git URL or local path)
      -> evidence extraction (REST, docker-compose, persistence)
      -> service dependency graph
      -> deterministic smell detectors
      -> candidate findings
      -> LLM validation, per finding
      -> LLM refactoring proposal, per confirmed finding
      -> (opt-in) patch generation -> approval -> apply -> verify -> commit/rollback
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
    python -m pipeline.run_pipeline target-repo --apply ask --verify-level test

Autonomous refactoring (``--apply``) is off by default. When on, each
refactoring plan is turned into a concrete patch, approved (by a human in
``ask`` mode, automatically in ``auto``), applied in a separate git worktree
on a ``refactor/<run id>`` branch, verified (build + re-detection), and then
committed or -- per ``--on-failure`` -- rolled back or kept for inspection.
The analyzed working tree itself is never modified.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pipeline.config import (
    ALL_DETECTORS,
    APPLY_MODES,
    FAILURE_POLICIES,
    VERIFY_LEVELS,
    AnalysisConfig,
    default_config,
)
from pipeline.detectors import run_detectors
from pipeline.extractors import collect_evidence
from pipeline.graph_analysis import build_graph
from pipeline.llm_detection import detect_finding
from pipeline.llm_refactoring import allowed_module_prefixes, propose_for_finding
from pipeline.models import (
    STAGE_APPLY,
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
from pipeline.patch_applier import ApplyError, RefactorWorkspace, module_dirs
from pipeline.patch_generator import generate_patch
from pipeline.repository import RepositoryError, acquire_repository
from pipeline.run_logger import RunLogger
from pipeline.verifier import Verifier, redetect

_SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# How a human (or the dashboard) is asked about a patch. Called as
# decide("apply", request) -> "approve" | "reject" | "approve_all", and
# decide("failure", request) -> "rollback" | "keep". Any other answer is
# treated as the safe choice: reject, and rollback.
Decide = Callable[[str, dict[str, Any]], str]


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
    approver: Optional[Decide] = None,
) -> AnalysisResult:
    """Run the full pipeline against one repository and return the result.

    `source` may be a Git URL or a local path; both are normalized to a
    local working tree by :mod:`pipeline.repository`. `approver` is
    required when ``config.refactor.mode`` is ``ask``.
    """
    config = config or default_config()
    if config.refactor.mode == "ask" and approver is None:
        raise ValueError("refactor mode 'ask' needs an approver to ask")
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
            approver=approver,
        )
    finally:
        # A clone holding a refactor branch with commits is the only copy of
        # that work, so it is kept regardless of the cleanup setting.
        kept_branch = bool(result.refactor and result.refactor.get("commits"))
        if kept_branch and workspace.info.is_temporary:
            result.refactor["clone_kept_at"] = str(workspace.root)
        if not keep_clone and not kept_branch and config.repository.cleanup_temporary_clones:
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
    approver: Optional[Decide] = None,
) -> None:
    """Stages 2-6 against an already-acquired working tree."""
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

    # -- Stage 6: autonomous refactoring (opt-in) ---------------------------
    _apply_refactorings(ctx, config, logger, result, approver)


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


def _apply_refactorings(
    ctx: AnalysisContext,
    config: AnalysisConfig,
    logger: RunLogger,
    result: AnalysisResult,
    decide: Optional[Decide],
) -> None:
    """Turn every refactoring plan into a patch and, unless dry-run, apply it.

    Findings are handled in result order (highest severity first) on one
    branch, so each patch is generated against the tree as the previous
    ones left it.
    """
    rcfg = config.refactor
    entries = [r for r in result.results if r.refactoring]
    if rcfg.mode == "never" or not entries:
        return

    summary: dict[str, Any] = {
        "mode": rcfg.mode,
        "on_failure": rcfg.on_failure,
        "verify_level": rcfg.verify_level,
        "verify_command": rcfg.verify_command,
    }
    result.refactor = summary

    workspace: Optional[RefactorWorkspace] = None
    tree = ctx.repo_root
    if rcfg.mode != "dry-run":
        try:
            workspace = RefactorWorkspace.create(ctx.repo_root, result.run_id, rcfg.worktree_root)
        except (ApplyError, RepositoryError) as exc:
            result.errors.append(PipelineError(stage=STAGE_APPLY, component="workspace", message=str(exc)))
            summary["error"] = str(exc)
            logger.log_stage("03_apply_summary", summary)
            return
        tree = workspace.root
        summary.update(branch=workspace.branch, base_commit=workspace.base_commit)

    verifier = Verifier(tree, rcfg)
    mode = rcfg.mode
    try:
        for entry in entries:
            mode = _apply_one(entry, ctx, config, logger, tree, workspace, verifier, mode, decide)
    except ApplyError as exc:  # the worktree is in an unknown state; stop applying
        result.errors.append(PipelineError(stage=STAGE_APPLY, component="workspace", message=str(exc)))
    finally:
        if workspace is not None:
            try:
                summary.update(workspace.close())
            except (ApplyError, RepositoryError) as exc:
                result.errors.append(PipelineError(stage=STAGE_APPLY, component="cleanup", message=str(exc)))
        logger.log_stage("03_apply_summary", summary)


def _apply_one(
    entry: FindingResult,
    ctx: AnalysisContext,
    config: AnalysisConfig,
    logger: RunLogger,
    tree: Path,
    workspace: Optional[RefactorWorkspace],
    verifier: Verifier,
    mode: str,
    decide: Optional[Decide],
) -> str:
    """Generate, approve, apply and verify one patch. Returns the mode for the
    next finding ("approve all remaining" turns ask into auto)."""
    finding = entry.finding
    rcfg = config.refactor
    allowed = allowed_module_prefixes(finding.services, tree)

    def log(state: dict[str, Any]) -> None:
        logger.log_stage(f"apply_{finding.key}", {"finding_key": finding.key, **state})

    try:
        patch = generate_patch(
            finding,
            entry.refactoring,
            tree,
            allowed_prefixes=allowed,
            max_retries=config.llm.max_json_retries,
            temperature=config.llm.temperature,
            max_tokens=rcfg.patch_max_tokens,
            max_input_chars=rcfg.max_patch_input_chars,
            logger=logger,
        )
    except Exception as exc:  # noqa: BLE001 - per-finding isolation boundary
        message = f"patch generation failed: {type(exc).__name__}: {exc}"
        entry.errors.append(PipelineError(stage=STAGE_APPLY, component=finding.key, message=message))
        entry.patch = {"status": "error", "files": [], "diff": "", "error": message}
        log(entry.patch)
        return mode

    state: dict[str, Any] = {
        **patch.to_dict(),
        "status": "proposed",
        "decision": None,
        "verification": None,
        "commit": None,
        "branch": workspace.branch if workspace else None,
    }
    entry.patch = state
    if mode == "dry-run" or workspace is None:
        log(state)
        return mode

    request = {
        "finding_key": finding.key,
        "smell_name": finding.smell_name,
        "services": finding.services,
        "files": state["files"],
        "diff": patch.diff,
        "rationale": entry.refactoring.get("rationale"),
    }
    if mode == "ask":
        answer = decide("apply", request)
        state["decision"] = answer
        if answer == "approve_all":
            mode = "auto"
        elif answer != "approve":
            state["status"] = "rejected"
            log(state)
            return mode
    else:
        state["decision"] = "auto"

    units = verifier.units_for(module_dirs(tree, allowed))
    try:
        verifier.baseline(units)
        workspace.write(patch)
        build = verifier.verify(units)
        redetection = redetect(finding, tree, ctx.repository, config)
        # A skipped build (none detected, or already broken) does not fail the
        # patch; re-detection still has to show the smell is gone.
        passed = build["status"] != "failed" and redetection["resolved"]
        state["verification"] = {"passed": passed, "build": build, "redetection": redetection}

        if passed:
            state["commit"] = workspace.commit(patch, _commit_message(finding, entry.refactoring, None))
            verifier.record_head(units, build)
            state["status"] = "verified"
        else:
            policy = (
                decide("failure", {**request, "verification": state["verification"], "default": rcfg.on_failure})
                if mode == "ask"
                else rcfg.on_failure
            )
            state["failure_policy"] = policy
            if policy == "keep":
                state["commit"] = workspace.commit(
                    patch, _commit_message(finding, entry.refactoring, state["verification"])
                )
                verifier.record_head(units, build)
                state["status"] = "failed_kept"
            else:
                workspace.rollback(patch)
                state["status"] = "rolled_back"
    except Exception as exc:  # noqa: BLE001 - per-finding isolation boundary
        message = f"apply/verify failed: {type(exc).__name__}: {exc}"
        entry.errors.append(PipelineError(stage=STAGE_APPLY, component=finding.key, message=message))
        state.update(status="error", error=message)
        try:
            workspace.rollback(patch)
        except Exception as rollback_exc:  # noqa: BLE001
            log(state)
            raise ApplyError(f"could not revert a failed patch for {finding.key}: {rollback_exc}") from rollback_exc

    log(state)
    return mode


def _commit_message(finding: Finding, plan: dict[str, Any], failed: Optional[dict[str, Any]]) -> str:
    subject = f"refactor({finding.smell}): {finding.key}"
    body = str(plan.get("rationale") or "").strip()
    if failed is None:
        return f"{subject}\n\n{body}\n\nVerified: build and re-detection passed."
    return (
        f"[unverified] {subject}\n\n{body}\n\n"
        f"Kept despite failed verification: build {failed['build']['status']}, "
        f"smell resolved: {failed['redetection']['resolved']}."
    )


def console_approver(ask: Callable[[str], str] = input, say: Callable[[str], None] = print) -> Decide:
    """Interactive approval for the CLI. End of input (a non-interactive
    stdin) answers with the safe choice instead of hanging or crashing."""

    def decide(kind: str, request: dict[str, Any]) -> str:
        if kind == "apply":
            say(f"\n=== Patch for {request['smell_name']}: {request['finding_key']} ===")
            for f in request["files"]:
                say(f"  {f['action']:>6}  {f['path']}")
            say(request["diff"])
            prompt, choices, default = (
                "Apply this patch? [y]es / [n]o / [a]ll remaining: ",
                {"y": "approve", "n": "reject", "a": "approve_all"},
                "reject",
            )
        else:
            v = request["verification"]
            say(
                f"\nVerification failed for {request['finding_key']}: build {v['build']['status']}, "
                f"smell resolved: {v['redetection']['resolved']}"
            )
            for run in v["build"].get("runs") or []:
                if run.get("passed") is False:
                    say(f"--- {run.get('command')} (in {run.get('cwd')}) ---\n{run.get('output_tail', '')}")
            default = request["default"]
            prompt, choices = f"[r]ollback / [k]eep on the branch (default: {default}): ", {"r": "rollback", "k": "keep"}

        while True:
            try:
                raw = ask(prompt).strip().lower()
            except EOFError:
                return default
            if not raw and kind == "failure":
                return default
            if raw[:1] in choices:
                return choices[raw[:1]]

    return decide


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
    refactor = parser.add_argument_group("autonomous refactoring")
    refactor.add_argument(
        "--apply",
        choices=APPLY_MODES,
        default="never",
        help="never: propose only (default). dry-run: generate patches, write nothing. "
        "ask: approve each patch interactively. auto: apply every patch without asking",
    )
    refactor.add_argument(
        "--on-failure",
        choices=FAILURE_POLICIES,
        default="rollback",
        help="A patch that fails verification is reverted (rollback, default) or "
        "committed to the refactor branch marked unverified (keep)",
    )
    refactor.add_argument(
        "--verify-level",
        choices=VERIFY_LEVELS,
        default="compile",
        help="Build check after each patch, using the detected build tool (Maven, Gradle, Ant, "
        "Go, Cargo, .NET, npm, Python). 'none' relies on re-detection only. Default: compile",
    )
    refactor.add_argument(
        "--verify-cmd",
        default=None,
        metavar="CMD",
        help="Shell command that replaces the detected build check, run from the repository root",
    )
    args = parser.parse_args(argv)

    config = default_config()
    try:
        config = config.with_detectors(args.smells)
    except ValueError as exc:
        parser.error(str(exc))

    if args.no_llm:
        if args.apply != "never":
            parser.error("--apply needs the LLM: patches are generated from LLM refactoring plans")
        config = replace(config, llm=replace(config.llm, enabled=False))

    config = replace(
        config,
        refactor=replace(
            config.refactor,
            mode=args.apply,
            on_failure=args.on_failure,
            verify_level=args.verify_level,
            verify_command=args.verify_cmd,
        ),
    )

    logger = RunLogger(args.run_name)
    result = analyze_repository(
        args.repository,
        branch=args.branch,
        config=config,
        logger=logger,
        keep_clone=args.keep_clone,
        approver=console_approver() if args.apply == "ask" else None,
    )

    payload = result.to_dict()
    if args.output:
        args.output.write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")

    print(json.dumps({"run_id": result.run_id, **payload["counts"]}, indent=2))
    if result.refactor:
        branch = result.refactor.get("branch")
        if branch:
            print(f"\nRefactor branch: {branch}  ({result.refactor.get('inspect')})")
        elif args.apply not in ("never", "dry-run"):
            print("\nNo patch was committed, so no refactor branch was kept.")
    print(f"\nRun log written to {logger.dir}")
    if args.output:
        print(f"Result JSON written to {args.output}")

    return 1 if result.errors and not result.results else 0


if __name__ == "__main__":
    sys.exit(main())
