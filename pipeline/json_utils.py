"""
Shared JSON extraction/repair helpers for LLM responses (Steps 4-5).

LLMs asked for "only JSON" frequently still wrap it in a markdown code
fence, or add a stray sentence before/after it. Per CLAUDE.md's stated
failure risk ("Malformed JSON output... plan for a JSON-repair/retry step
rather than treating one failure as a system failure"), this module handles
the repair half; the retry half lives in the calling agent (llm_detection.py,
llm_refactoring.py), since only it knows how to re-prompt.
"""

from __future__ import annotations

import json
import re

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def extract_json(text: str) -> dict:
    """Best-effort extraction of a single JSON object from an LLM response.

    Tries, in order: the whole text as-is, the contents of a ```json fenced
    block, and the substring between the first '{' and the last '}'.
    Raises ValueError (not JSONDecodeError) if none parse, so callers can
    catch one exception type regardless of which candidate failed.
    """
    text = text.strip()
    candidates = [text]

    fence_match = _CODE_FENCE_RE.search(text)
    if fence_match:
        candidates.append(fence_match.group(1))

    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last != -1 and last > first:
        candidates.append(text[first : last + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    raise ValueError(f"Could not extract valid JSON from LLM response:\n{text[:500]}")
