"""
Shared code-context helpers for Steps 4-5: turning Step 2/3's raw edge
evidence into LLM-readable prompt text. Split out of llm_detection.py once
llm_refactoring.py needed the same formatting for its own prompt.
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


def format_evidence_block(evidence: list[dict], repo_root: Path) -> str:
    """Render Step 2/3 evidence records (caller/callee/source/file/line/
    evidence dicts) as numbered blocks with surrounding source code, for
    inclusion in an LLM prompt."""
    lines: list[str] = []
    for i, e in enumerate(evidence, 1):
        lines.append(f"\n--- Evidence {i}: {e['caller']} -> {e['callee']} (source: {e['source']}) ---")
        lines.append(f"File: {e['file']}, line {e.get('line', '?')}")
        lines.append(f"Matched text: {e['evidence']}")
        snippet = read_snippet(repo_root / e["file"], e.get("line", 0))
        if snippet:
            lines.append("Surrounding code:")
            lines.append(snippet)
    return "\n".join(lines)
