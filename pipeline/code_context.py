"""
Shared code-context helpers for the LLM stages: turning extracted evidence
into LLM-readable prompt text. Split out of llm_detection.py once
llm_refactoring.py needed the same formatting for its own prompt.

Evidence records are self-describing, so formatting dispatches on the
record's own shape rather than on which smell is being analyzed: a
dependency record (caller/callee/file/line) renders as a call site with
surrounding source, a persistence record (service/kind/value) renders as a
configuration or schema fact. A detector can therefore attach whichever
evidence it has without the prompt layer needing to know the smell.
"""

from __future__ import annotations

from pathlib import Path


def read_snippet(file_path: Path, line: int, context: int = 6) -> str:
    """Return a few lines of source around `line` (1-indexed), numbered, or
    "" if the file/line isn't available -- the LLM still gets the matched
    text itself in that case, just not the surrounding context."""
    if not file_path.exists() or line <= 0:
        return ""
    try:
        all_lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start = max(0, line - 1 - context)
    end = min(len(all_lines), line + context)
    return "\n".join(f"{n + 1:>5}: {all_lines[n]}" for n in range(start, end))


def _format_dependency_record(index: int, e: dict, repo_root: Path) -> list[str]:
    lines = [f"\n--- Evidence {index}: {e['caller']} -> {e['callee']} (source: {e['source']}) ---"]
    lines.append(f"File: {e['file']}, line {e.get('line', '?')}")
    lines.append(f"Matched text: {e['evidence']}")
    snippet = read_snippet(repo_root / e["file"], e.get("line", 0))
    if snippet:
        lines.append("Surrounding code:")
        lines.append(snippet)
    return lines


def _format_persistence_record(index: int, e: dict, repo_root: Path) -> list[str]:
    """Render a persistence record, including the confidence the extractor
    assigned it -- the model is told how much the static evidence is worth
    rather than being left to assume certainty."""
    metadata = e.get("metadata") or {}
    lines = [f"\n--- Evidence {index}: {e['service']} ({e.get('kind', 'persistence')}) ---"]
    lines.append(f"File: {e['file']}, line {e.get('line', '?')}")
    lines.append(f"Normalized value: {e.get('value')}")
    lines.append(f"Matched text: {e.get('evidence')}")
    lines.append(f"Extractor confidence: {e.get('confidence')}")
    details = {k: v for k, v in metadata.items() if k not in {"url", "identity"} and v is not None}
    if details:
        lines.append(f"Details: {details}")
    snippet = read_snippet(repo_root / e["file"], e.get("line", 0))
    if snippet:
        lines.append("Surrounding configuration:")
        lines.append(snippet)
    return lines


def format_evidence_block(evidence: list[dict], repo_root: Path) -> str:
    """Render evidence records as numbered blocks for an LLM prompt.

    Dependency records (with caller/callee) render with surrounding source
    code; persistence records (with service/kind/value) render as
    configuration facts with their extractor confidence. Unknown shapes
    fall back to a plain key/value dump rather than raising, so a new
    detector's evidence can never break prompt construction.
    """
    lines: list[str] = []
    for i, e in enumerate(evidence, 1):
        if "caller" in e and "callee" in e:
            lines.extend(_format_dependency_record(i, e, repo_root))
        elif "service" in e:
            lines.extend(_format_persistence_record(i, e, repo_root))
        else:
            lines.append(f"\n--- Evidence {i} ---")
            lines.append(str(e))
    return "\n".join(lines)
