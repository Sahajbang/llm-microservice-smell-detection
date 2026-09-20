"""
LLM Detection Agent (CLAUDE.md Step 4), generalized to any smell.

One direct call per candidate finding (no agent framework) to confirm
whether a statically-detected smell is a genuine architectural problem, and
to explain why, in structured JSON. The model is never asked to discover
smells: a deterministic detector has already produced the candidate, and
this stage supplies the reasoning about it, grounded in the evidence that
candidate carries.

Smell-awareness comes from :mod:`pipeline.smells`: the system prompt is
built from the smell's own definition and task framing, so there is no
``if smell == ...`` dispatch here and adding a smell requires no change to
this module.

Two entry points exist deliberately:

* :func:`detect_finding` -- the current path. Takes a ``Finding`` from any
  detector and includes the deterministic detector's own reasoning
  (description, metrics, confidence) in the prompt, which is what lets the
  model act as a validation layer rather than a re-discoverer.
* :func:`detect_smell` -- the Phase 1 cycle-specific path, kept verbatim
  (same prompt text, same return shape) so existing commands, tests and
  previously logged runs stay valid and comparable.

The LLM provider is NVIDIA's OpenAI-compatible endpoint (see
pipeline.llm_client), not the Claude API CLAUDE.md originally specified --
a project decision made when API access was easier to get through NVIDIA.
Nothing else about this step depends on which provider is behind
`call_llm`. API keys are never logged: only the prompt, the model name, and
the response are recorded.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Optional

from pipeline.code_context import format_evidence_block
from pipeline.graph_analysis import build_graph, cycle_evidence, detect_cycles
from pipeline.json_utils import extract_json
from pipeline.llm_client import call_llm
from pipeline.models import Finding
from pipeline.run_logger import RunLogger
from pipeline.smells import CYCLIC_DEPENDENCY, SmellSpec, detection_system_prompt, get_spec

# Back-compat aliases: the Phase 1 module exposed these names, and
# pipeline.llm_refactoring imports SMELL_DEFINITION from here.
SMELL_DEFINITION = get_spec(CYCLIC_DEPENDENCY).definition
SYSTEM_PROMPT = detection_system_prompt(get_spec(CYCLIC_DEPENDENCY))


def build_user_prompt(cycle: list[str], evidence: list[dict], repo_root: Path) -> str:
    """Phase 1 cycle-specific user prompt (unchanged)."""
    header = f"Candidate cycle: {' -> '.join(cycle)}\n\nCode evidence for each hop in the cycle:"
    return header + format_evidence_block(evidence, repo_root)


def build_finding_prompt(finding: Finding, spec: SmellSpec, repo_root: Path) -> str:
    """User prompt for any finding.

    Includes what the deterministic detector concluded and why, so the
    model validates a specific claim instead of re-deriving it, plus the
    smell's stated static-analysis limits so it does not over-claim.
    """
    lines = [
        f"Candidate {spec.name} in repository '{repo_root.name}'.",
        "",
        "What static analysis found:",
        f"  {finding.description}",
        f"  Services involved: {', '.join(finding.services)}",
        f"  Deterministic severity: {finding.severity} (confidence {finding.confidence})",
    ]
    if finding.metrics:
        lines.append(f"  Detector metrics: {json.dumps(finding.metrics, sort_keys=True, default=str)}")
    if spec.static_limits:
        lines += ["", f"Limits of this evidence: {spec.static_limits}"]
    lines += ["", f"Supporting {spec.evidence_noun}:"]
    return "\n".join(lines) + format_evidence_block(finding.evidence, repo_root)


def _run_detection(
    *,
    spec: SmellSpec,
    base_prompt: str,
    system_prompt: str,
    max_retries: int,
    temperature: float = 0.2,
) -> tuple[Optional[dict[str, Any]], list[dict[str, Any]], Optional[str]]:
    """Call the LLM, retrying with feedback until the response parses.

    Returns ``(parsed_or_None, attempts, last_error)``; the caller decides
    whether an exhausted retry budget is fatal (single-finding call) or
    merely recorded (batch run).
    """
    attempts: list[dict[str, Any]] = []
    parsed: Optional[dict[str, Any]] = None
    last_error: Optional[str] = None

    for attempt in range(1, max_retries + 1):
        prompt = base_prompt
        if attempt > 1:
            prompt += (
                "\n\nIMPORTANT: your previous response could not be parsed as JSON "
                f"({last_error}). Respond with ONLY the JSON object, nothing else."
            )
        result = call_llm(system_prompt, prompt, temperature=temperature)
        attempt_record: dict[str, Any] = {
            "attempt": attempt,
            "prompt": prompt,
            "raw_response": result.content,
            "reasoning": result.reasoning,
            "model": result.model,
        }
        try:
            parsed = extract_json(result.content)
            attempt_record["parse_ok"] = True
            attempts.append(attempt_record)
            break
        except ValueError as exc:
            last_error = str(exc)
            attempt_record["parse_ok"] = False
            attempt_record["parse_error"] = last_error
            attempts.append(attempt_record)

    return parsed, attempts, last_error


def detect_smell(
    cycle: list[str],
    evidence: list[dict],
    repo_root: Path,
    *,
    max_retries: int = 3,
    logger: RunLogger | None = None,
) -> dict[str, Any]:
    """Phase 1 entry point: run detection for one candidate cycle.

    Kept with its original prompt and return shape so existing commands,
    tests and logged runs remain valid.
    """
    spec = get_spec(CYCLIC_DEPENDENCY)
    base_prompt = build_user_prompt(cycle, evidence, repo_root)
    parsed, attempts, last_error = _run_detection(
        spec=spec, base_prompt=base_prompt, system_prompt=SYSTEM_PROMPT, max_retries=max_retries
    )

    record = {
        "cycle": cycle,
        "evidence": evidence,
        "system_prompt": SYSTEM_PROMPT,
        "attempts": attempts,
        "result": parsed,
    }
    if logger is not None:
        logger.log_stage(f"step4_detection_{'_'.join(cycle[:-1])}", record)

    if parsed is None:
        raise ValueError(f"LLM did not return parseable JSON after {max_retries} attempts: {last_error}")

    return {"cycle": cycle, **parsed}


def detect_finding(
    finding: Finding,
    repo_root: Path,
    *,
    max_retries: int = 3,
    temperature: float = 0.2,
    logger: RunLogger | None = None,
) -> dict[str, Any]:
    """Run detection for one finding of any smell.

    Returns the parsed verdict with the finding's identity attached. The
    LLM's ``severity``/``confidence`` are its own; the deterministic values
    stay on the ``Finding`` and are never overwritten here.
    """
    spec = get_spec(finding.smell)
    system_prompt = detection_system_prompt(spec)
    base_prompt = build_finding_prompt(finding, spec, repo_root)
    parsed, attempts, last_error = _run_detection(
        spec=spec,
        base_prompt=base_prompt,
        system_prompt=system_prompt,
        max_retries=max_retries,
        temperature=temperature,
    )

    record = {
        "smell": finding.smell,
        "finding_key": finding.key,
        "finding": finding.to_dict(),
        "system_prompt": system_prompt,
        "attempts": attempts,
        "result": parsed,
    }
    if logger is not None:
        logger.log_stage(f"detection_{finding.key}", record)

    if parsed is None:
        raise ValueError(f"LLM did not return parseable JSON after {max_retries} attempts: {last_error}")

    # The model's own "smell" field (a display name) is left exactly as it
    # answered; the pipeline's machine-readable identity is added under
    # distinct keys so neither overwrites the other.
    return {**parsed, "smell_key": finding.smell, "finding_key": finding.key}


def run(edges_json: Path, repo_root: Path, *, logger: RunLogger | None = None) -> dict[str, Any]:
    """Phase 1 entry point: Step 3 + Step 4 (detect cycles, confirm each)."""
    edges = json.loads(edges_json.read_text(encoding="utf-8"))
    graph = build_graph(edges)
    cycles = detect_cycles(graph)

    if logger is not None:
        logger.log_stage("step3_graph", {"edges": edges, "cycles": cycles})

    results = [
        detect_smell(c["cycle"], cycle_evidence(graph, c["cycle"]), repo_root, logger=logger) for c in cycles
    ]
    return {"cycles_found": len(cycles), "results": results}


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 4: LLM Detection Agent (Cyclic Dependency)")
    parser.add_argument("edges_json", type=Path, help="Path to the JSON edge list produced by pipeline.extractor")
    parser.add_argument("--repo-root", type=Path, default=Path("target-repo"))
    parser.add_argument("--no-log", action="store_true", help="Skip writing a run log under logs/runs/")
    args = parser.parse_args()

    logger = None if args.no_log else RunLogger("step4-detection")
    output = run(args.edges_json, args.repo_root, logger=logger)
    print(json.dumps(output, indent=2))
    if logger is not None:
        print(f"\nRun log written to {logger.dir}")


if __name__ == "__main__":
    main()
