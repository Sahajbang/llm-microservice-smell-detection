"""
Step 5: LLM Refactoring Agent.

A second, separate LLM call (no agent framework) that, given a Cyclic
Dependency Step 4 already confirmed, proposes a minimal, scope-limited fix.
Per CLAUDE.md Step 5, the response must explicitly list affected files, the
specific per-file changes, rationale, and expected impact.

Per CLAUDE.md's stated failure risk for this step ("LLM proposing changes
outside the intended scope"), every proposal is checked against the set of
files that actually belong to the services participating in the cycle
before being accepted. A proposal naming any file outside that set is
rejected and re-prompted with the specific offending paths called out
(bounded by max_retries), rather than silently accepted or silently
dropped -- CLAUDE.md's own guidance is "reject/re-prompt", not just reject.

Provider: NVIDIA's OpenAI-compatible endpoint, same as Step 4 (see
pipeline.llm_client).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pipeline.code_context import format_evidence_block
from pipeline.extractor import discover_services
from pipeline.graph_analysis import build_graph, cycle_evidence, detect_cycles
from pipeline.json_utils import extract_json
from pipeline.llm_client import call_llm
from pipeline.llm_detection import SMELL_DEFINITION, detect_smell
from pipeline.run_logger import RunLogger

SYSTEM_PROMPT = f"""You are an expert software architect specializing in microservice architecture refactoring.

{SMELL_DEFINITION}

You will be given a CONFIRMED Cyclic Dependency -- already verified as a genuine problem by a separate review step -- between the services in the cycle, plus the specific code evidence for each hop. Propose the MINIMAL change that breaks the cycle. Do not redesign the services beyond what is needed to remove the cyclic call, and do not reference any file outside the services named in the cycle.

Respond with ONLY a single JSON object -- no prose before or after it, no markdown code fences -- matching exactly this shape:
{{
  "smell": "Cyclic Dependency",
  "affected_files": ["<repo-relative path>", ...],
  "changes": [
    {{
      "file": "<repo-relative path, must appear in affected_files>",
      "method_or_class": "<the specific class/method touched>",
      "description": "<precise, actionable description of the change>"
    }}
  ],
  "rationale": "<why this specific fix breaks the cycle, 2-4 sentences>",
  "expected_impact": "<what becomes safer/possible afterward, and any tradeoff introduced, 2-4 sentences>"
}}

Every path in "affected_files" and every "changes[].file" MUST be either one of the real files you were shown evidence for, or a new file you are proposing to add -- and in both cases it MUST live inside one of the module directories of the services participating in the cycle. Never name a file belonging to a service outside the cycle."""


def build_user_prompt(cycle: list[str], evidence: list[dict], detection: dict, repo_root: Path) -> str:
    header = (
        f"Confirmed cycle: {' -> '.join(cycle)}\n"
        f"Detection verdict: severity={detection.get('severity')}, confidence={detection.get('confidence')}\n"
        f"Detection rationale: {detection.get('rationale')}\n\n"
        "Code evidence for each hop in the cycle:"
    )
    return header + format_evidence_block(evidence, repo_root)


def allowed_module_prefixes(cycle: list[str], repo_root: Path) -> set[str]:
    """The set of module-directory prefixes (repo-relative, e.g.
    "spring-petclinic-visits-service") that a refactoring plan is allowed
    to touch: the module for every distinct service in the cycle."""
    services = set(cycle)
    dir_by_service = {name: module_dir for module_dir, name in discover_services(repo_root).items()}
    return {dir_by_service[s] for s in services if s in dir_by_service}


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


def propose_refactoring(
    cycle: list[str],
    evidence: list[dict],
    detection: dict,
    repo_root: Path,
    *,
    max_retries: int = 3,
    logger: RunLogger | None = None,
) -> dict[str, Any]:
    """Run Step 5 for one confirmed cycle.

    Retries up to `max_retries` times, re-prompting with specific feedback,
    if the LLM's response either isn't parseable JSON or names files
    outside the services in the cycle (the scope-enforcement check
    CLAUDE.md's Step 5 calls for). Every attempt is recorded and handed to
    `logger.log_stage(...)` if one is given.
    """
    allowed_prefixes = allowed_module_prefixes(cycle, repo_root)
    base_prompt = build_user_prompt(cycle, evidence, detection, repo_root)
    attempts = []
    parsed: dict[str, Any] | None = None
    feedback: str | None = None

    for attempt in range(1, max_retries + 1):
        prompt = base_prompt
        if feedback:
            prompt += f"\n\nIMPORTANT: {feedback} Respond again with ONLY the corrected JSON object."
        result = call_llm(SYSTEM_PROMPT, prompt, temperature=0.2)
        attempt_record = {
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

        out_of_scope = find_out_of_scope_files(candidate, allowed_prefixes)
        if out_of_scope:
            feedback = (
                f"Your previous response named file(s) outside the services in the cycle: {out_of_scope}. "
                f"Only files under these module directories are allowed: {sorted(allowed_prefixes)}."
            )
            attempt_record.update(parse_ok=True, scope_ok=False, out_of_scope_files=out_of_scope)
            attempts.append(attempt_record)
            continue

        attempt_record.update(parse_ok=True, scope_ok=True)
        attempts.append(attempt_record)
        parsed = candidate
        break

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


def run(edges_json: Path, repo_root: Path, *, logger: RunLogger | None = None) -> dict[str, Any]:
    """Steps 3-5 together: detect cycles, confirm each with the LLM (Step 4),
    then propose a scope-checked fix (Step 5) for every cycle the detection
    agent both confirmed and recommended refactoring for."""
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
