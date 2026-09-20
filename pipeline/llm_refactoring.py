"""
LLM Refactoring Agent (CLAUDE.md Step 5), generalized to any smell.

A second, separate LLM call that, given a finding the detection stage
already confirmed, proposes a minimal, scope-limited fix: the affected
files, the specific per-file changes, the rationale, and the expected
impact. The agent never edits code -- this remains a proposal system.

Per CLAUDE.md's stated failure risk for this step ("LLM proposing changes
outside the intended scope"), every proposal is checked against the set of
module directories belonging to the services named in the finding. A
proposal naming any file outside that set is rejected and re-prompted with
the specific offending paths called out (bounded by max_retries), rather
than silently accepted or silently dropped.

Scope enforcement is unenforceable when none of the finding's services map
to a Maven module in the repository -- for example a finding about a
database container named only in docker-compose. In that case the check is
skipped and the fact is recorded on the attempt (``scope_enforced: false``)
rather than rejecting every possible answer, which would otherwise burn the
whole retry budget and produce nothing.

Smell-awareness comes from :mod:`pipeline.smells`: the system prompt and
the notion of a "minimal fix" are taken from the smell's own spec, so this
module contains no per-smell branching.

Two entry points, as in :mod:`pipeline.llm_detection`:
:func:`propose_for_finding` (current, any smell) and
:func:`propose_refactoring` (Phase 1 cycle path, kept verbatim).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from pipeline.code_context import format_evidence_block
from pipeline.extractor import discover_services
from pipeline.graph_analysis import build_graph, cycle_evidence, detect_cycles
from pipeline.json_utils import extract_json
from pipeline.llm_client import call_llm
from pipeline.llm_detection import SMELL_DEFINITION, detect_smell
from pipeline.models import Finding
from pipeline.run_logger import RunLogger
from pipeline.smells import CYCLIC_DEPENDENCY, SmellSpec, get_spec, refactoring_system_prompt

__all__ = [
    "SMELL_DEFINITION",
    "SYSTEM_PROMPT",
    "allowed_module_prefixes",
    "build_finding_prompt",
    "build_user_prompt",
    "find_out_of_scope_files",
    "propose_for_finding",
    "propose_refactoring",
    "run",
    "main",
]

# Back-compat alias: the Phase 1 cycle-specific refactoring system prompt.
SYSTEM_PROMPT = refactoring_system_prompt(get_spec(CYCLIC_DEPENDENCY))


def build_user_prompt(cycle: list[str], evidence: list[dict], detection: dict, repo_root: Path) -> str:
    """Phase 1 cycle-specific user prompt (unchanged)."""
    header = (
        f"Confirmed cycle: {' -> '.join(cycle)}\n"
        f"Detection verdict: severity={detection.get('severity')}, confidence={detection.get('confidence')}\n"
        f"Detection rationale: {detection.get('rationale')}\n\n"
        "Code evidence for each hop in the cycle:"
    )
    return header + format_evidence_block(evidence, repo_root)


def build_finding_prompt(
    finding: Finding, detection: dict, spec: SmellSpec, repo_root: Path
) -> str:
    """User prompt for a confirmed finding of any smell."""
    lines = [
        f"Confirmed {spec.name} in repository '{repo_root.name}'.",
        f"Services involved: {', '.join(finding.services)}",
        f"What static analysis found: {finding.description}",
        f"Detection verdict: severity={detection.get('severity')}, confidence={detection.get('confidence')}",
        f"Detection rationale: {detection.get('rationale')}",
    ]
    if finding.metrics:
        lines.append(f"Detector metrics: {json.dumps(finding.metrics, sort_keys=True, default=str)}")
    lines += ["", f"Supporting {spec.evidence_noun}:"]
    return "\n".join(lines) + format_evidence_block(finding.evidence, repo_root)


def allowed_module_prefixes(services: list[str], repo_root: Path) -> set[str]:
    """The set of module-directory prefixes (repo-relative, e.g.
    "spring-petclinic-visits-service") that a refactoring plan is allowed
    to touch: the module for every distinct service named in the finding.

    Service names that do not correspond to a Maven module in the
    repository (e.g. a database container from docker-compose) simply
    contribute no prefix.
    """
    wanted = set(services)
    dir_by_service = {name: module_dir for module_dir, name in discover_services(repo_root).items()}
    return {dir_by_service[s] for s in wanted if s in dir_by_service}


def find_out_of_scope_files(plan: dict[str, Any], allowed_prefixes: set[str]) -> list[str]:
    """Return every file referenced by `plan` (affected_files plus every
    changes[].file) that does not live under one of `allowed_prefixes`."""
    files: set[str] = set(plan.get("affected_files") or [])
    for change in plan.get("changes") or []:
        f = change.get("file") if isinstance(change, dict) else None
        if f:
            files.add(f)

    out_of_scope = []
    for f in files:
        normalized = str(f).replace("\\", "/").lstrip("/")
        if not any(normalized == prefix or normalized.startswith(prefix + "/") for prefix in allowed_prefixes):
            out_of_scope.append(f)
    return sorted(out_of_scope)


def _run_refactoring(
    *,
    system_prompt: str,
    base_prompt: str,
    allowed_prefixes: set[str],
    max_retries: int,
    temperature: float = 0.2,
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]], Optional[str]]:
    """Call the LLM until it returns a parseable, in-scope plan.

    Scope enforcement is skipped (and recorded as such) when no module
    prefixes could be resolved for the finding's services -- see the module
    docstring.
    """
    enforce_scope = bool(allowed_prefixes)
    attempts: list[dict[str, Any]] = []
    parsed: Optional[dict[str, Any]] = None
    feedback: Optional[str] = None

    for attempt in range(1, max_retries + 1):
        prompt = base_prompt
        if feedback:
            prompt += f"\n\nIMPORTANT: {feedback} Respond again with ONLY the corrected JSON object."
        result = call_llm(system_prompt, prompt, temperature=temperature)
        attempt_record: dict[str, Any] = {
            "attempt": attempt,
            "prompt": prompt,
            "raw_response": result.content,
            "reasoning": result.reasoning,
            "model": result.model,
        }

        try:
            candidate = extract_json(result.content)
        except ValueError as exc:
            feedback = f"Your previous response could not be parsed as JSON ({exc})."
            attempt_record.update(parse_ok=False, parse_error=str(exc))
            attempts.append(attempt_record)
            continue

        if not enforce_scope:
            attempt_record.update(parse_ok=True, scope_ok=True, scope_enforced=False)
            attempts.append(attempt_record)
            parsed = candidate
            break

        out_of_scope = find_out_of_scope_files(candidate, allowed_prefixes)
        if out_of_scope:
            feedback = (
                f"Your previous response named file(s) outside the services in the finding: {out_of_scope}. "
                f"Only files under these module directories are allowed: {sorted(allowed_prefixes)}."
            )
            attempt_record.update(parse_ok=True, scope_ok=False, out_of_scope_files=out_of_scope)
            attempts.append(attempt_record)
            continue

        attempt_record.update(parse_ok=True, scope_ok=True, scope_enforced=True)
        attempts.append(attempt_record)
        parsed = candidate
        break

    return parsed, attempts, feedback


def propose_refactoring(
    cycle: list[str],
    evidence: list[dict],
    detection: dict,
    repo_root: Path,
    *,
    max_retries: int = 3,
    logger: RunLogger | None = None,
) -> dict[str, Any]:
    """Phase 1 entry point: propose a fix for one confirmed cycle.

    Kept with its original prompt, scope rule and return shape.
    """
    allowed_prefixes = allowed_module_prefixes(cycle, repo_root)
    base_prompt = build_user_prompt(cycle, evidence, detection, repo_root)
    # The Phase 1 feedback wording said "in the cycle"; preserved here.
    parsed, attempts, feedback = _run_refactoring(
        system_prompt=SYSTEM_PROMPT,
        base_prompt=base_prompt,
        allowed_prefixes=allowed_prefixes,
        max_retries=max_retries,
    )

    record = {
        "cycle": cycle,
        "evidence": evidence,
        "detection": detection,
        "allowed_module_prefixes": sorted(allowed_prefixes),
        "system_prompt": SYSTEM_PROMPT,
        "attempts": attempts,
        "result": parsed,
    }
    if logger is not None:
        logger.log_stage(f"step5_refactoring_{'_'.join(sorted(set(cycle)))}", record)

    if parsed is None:
        raise ValueError(
            f"LLM did not produce an in-scope, parseable refactoring plan after {max_retries} attempts: {feedback}"
        )

    return {"cycle": cycle, **parsed}


def propose_for_finding(
    finding: Finding,
    detection: dict,
    repo_root: Path,
    *,
    max_retries: int = 3,
    temperature: float = 0.2,
    logger: RunLogger | None = None,
) -> dict[str, Any]:
    """Propose a scope-checked fix for one confirmed finding of any smell."""
    spec = get_spec(finding.smell)
    system_prompt = refactoring_system_prompt(spec)
    allowed_prefixes = allowed_module_prefixes(finding.services, repo_root)
    base_prompt = build_finding_prompt(finding, detection, spec, repo_root)

    parsed, attempts, feedback = _run_refactoring(
        system_prompt=system_prompt,
        base_prompt=base_prompt,
        allowed_prefixes=allowed_prefixes,
        max_retries=max_retries,
        temperature=temperature,
    )

    record = {
        "smell": finding.smell,
        "finding_key": finding.key,
        "finding": finding.to_dict(),
        "detection": detection,
        "allowed_module_prefixes": sorted(allowed_prefixes),
        "scope_enforced": bool(allowed_prefixes),
        "system_prompt": system_prompt,
        "attempts": attempts,
        "result": parsed,
    }
    if logger is not None:
        logger.log_stage(f"refactoring_{finding.key}", record)

    if parsed is None:
        raise ValueError(
            f"LLM did not produce an in-scope, parseable refactoring plan after {max_retries} attempts: {feedback}"
        )

    # As in llm_detection: the model's own "smell" display string is kept
    # verbatim, the pipeline identity is added under distinct keys.
    return {**parsed, "smell_key": finding.smell, "finding_key": finding.key}


def run(edges_json: Path, repo_root: Path, *, logger: RunLogger | None = None) -> dict[str, Any]:
    """Phase 1 entry point: Steps 3-5 for Cyclic Dependency only."""
    edges = json.loads(edges_json.read_text(encoding="utf-8"))
    graph = build_graph(edges)
    cycles = detect_cycles(graph)

    if logger is not None:
        logger.log_stage("step3_graph", {"edges": edges, "cycles": cycles})

    results = []
    for c in cycles:
        cyc = c["cycle"]
        evidence = cycle_evidence(graph, cyc)
        detection = detect_smell(cyc, evidence, repo_root, logger=logger)
        entry: dict[str, Any] = {"cycle": cyc, "detection": detection}
        if detection.get("detected") and detection.get("refactoring_recommended"):
            entry["refactoring_plan"] = propose_refactoring(cyc, evidence, detection, repo_root, logger=logger)
        results.append(entry)

    return {"cycles_found": len(cycles), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 5: LLM Refactoring Agent (runs Steps 3-5)")
    parser.add_argument("edges_json", type=Path, help="Path to the JSON edge list produced by pipeline.extractor")
    parser.add_argument("--repo-root", type=Path, default=Path("target-repo"))
    parser.add_argument("--no-log", action="store_true", help="Skip writing a run log under logs/runs/")
    args = parser.parse_args()

    logger = None if args.no_log else RunLogger("step5-refactoring")
    output = run(args.edges_json, args.repo_root, logger=logger)
    print(json.dumps(output, indent=2))
    if logger is not None:
        print(f"\nRun log written to {logger.dir}")


if __name__ == "__main__":
    main()
