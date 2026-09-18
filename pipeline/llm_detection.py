"""
Step 4: LLM Detection Agent.

One direct call per candidate cycle (no agent framework) to confirm whether
a statically-detected Cyclic Dependency (Step 3) is a genuine architectural
problem, and to explain why, in structured JSON. Per CLAUDE.md Step 4, the
prompt is grounded in the actual cycle path plus the specific calling code
(pulled by file path from Step 2's evidence) rather than asking the LLM to
find smells from nothing -- static analysis says "something suspicious
exists here"; this step supplies the reasoning.

The LLM provider is NVIDIA's OpenAI-compatible endpoint (see
pipeline.llm_client), not the Claude API CLAUDE.md originally specified --
a project decision made when API access was easier to get through NVIDIA.
Nothing else about this step depends on which provider is behind
`call_llm`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pipeline.code_context import format_evidence_block
from pipeline.graph_analysis import build_graph, cycle_evidence, detect_cycles
from pipeline.json_utils import extract_json
from pipeline.llm_client import call_llm
from pipeline.run_logger import RunLogger

SMELL_DEFINITION = """Cyclic Dependency (architectural smell): two or more microservices depend on
each other's APIs, directly or through a chain of calls, forming a closed
loop (A -> B -> ... -> A). This couples the services' availability,
deployability, and release cadence together, undermining a core purpose of
a microservice architecture -- independent evolution and failure isolation.
A synchronous cycle can also compound into cascading latency or, in the
worst case, deadlock, if calls are ever nested across it."""

SYSTEM_PROMPT = f"""You are an expert software architect specializing in microservice architecture smells.

{SMELL_DEFINITION}

You will be given ONE candidate Cyclic Dependency, found by static analysis of a real repository's source code (regex-matched service-to-service call sites -- not your own judgement). Your job is to confirm whether this is a genuine architectural problem in this specific case, using only the code evidence given, and to explain why.

Respond with ONLY a single JSON object -- no prose before or after it, no markdown code fences -- matching exactly this shape:
{{
  "smell": "Cyclic Dependency",
  "detected": true or false,
  "confidence": <number between 0.0 and 1.0>,
  "severity": "LOW" | "MEDIUM" | "HIGH",
  "rationale": "<2-4 sentences, grounded in the specific code evidence given>",
  "refactoring_recommended": true or false
}}"""


def build_user_prompt(cycle: list[str], evidence: list[dict], repo_root: Path) -> str:
    header = f"Candidate cycle: {' -> '.join(cycle)}\n\nCode evidence for each hop in the cycle:"
    return header + format_evidence_block(evidence, repo_root)


def detect_smell(
    cycle: list[str],
    evidence: list[dict],
    repo_root: Path,
    *,
    max_retries: int = 3,
    logger: RunLogger | None = None,
) -> dict[str, Any]:
    """Run Step 4 for one candidate cycle.

    Returns the parsed detection JSON (the dict CLAUDE.md's Step 4 Output
    shape describes). Retries up to `max_retries` times if the LLM's
    response can't be parsed as JSON, per CLAUDE.md's stated failure risk.
    Every attempt (prompt, raw response, reasoning trace) is recorded, and
    handed to `logger.log_stage(...)` if one is given.
    """
    base_prompt = build_user_prompt(cycle, evidence, repo_root)
    attempts = []
    parsed: dict[str, Any] | None = None
    last_error: str | None = None

    for attempt in range(1, max_retries + 1):
        prompt = base_prompt
        if attempt > 1:
            prompt += (
                "\n\nIMPORTANT: your previous response could not be parsed as JSON "
                f"({last_error}). Respond with ONLY the JSON object, nothing else."
            )
        result = call_llm(SYSTEM_PROMPT, prompt, temperature=0.2)
        attempt_record = {
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


def run(edges_json: Path, repo_root: Path, *, logger: RunLogger | None = None) -> dict[str, Any]:
    """Step 3 + Step 4 together: detect cycles, then confirm each with the LLM."""
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
    parser = argparse.ArgumentParser(description="Step 4: LLM Detection Agent")
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
