"""Step 4 tests.

The LLM call itself (pipeline.llm_client.call_llm) is mocked throughout --
these tests exercise the deterministic parts (prompt building, retry-on-
bad-JSON, result shape) without needing a real API key or network access.
Actually calling the configured NVIDIA endpoint is a manual smoke test, not
something the automated suite should depend on. Code-snippet reading itself
is covered in test_code_context.py.
"""

import json
from unittest.mock import patch

from pipeline.llm_client import LLMResult
from pipeline.llm_detection import build_user_prompt, detect_smell, run


def _fake_result(content: str, reasoning: str = "") -> LLMResult:
    return LLMResult(
        content=content,
        reasoning=reasoning,
        model="nvidia/nemotron-3-ultra-550b-a55b",
        system_prompt="sys",
        user_prompt="user",
        temperature=0.2,
        max_tokens=4096,
    )


def test_build_user_prompt_includes_cycle_and_evidence(tmp_path):
    evidence = [
        {
            "caller": "visits-service",
            "callee": "customers-service",
            "source": "resttemplate",
            "file": "does-not-exist.java",
            "line": 0,
            "evidence": '"http://customers-service/owners/-/pets/{petId}"',
        }
    ]
    prompt = build_user_prompt(["visits-service", "customers-service", "visits-service"], evidence, tmp_path)

    assert "visits-service -> customers-service -> visits-service" in prompt
    assert "resttemplate" in prompt
    assert "http://customers-service/owners/-/pets/{petId}" in prompt


def test_detect_smell_parses_valid_json_on_first_try():
    expected = {
        "smell": "Cyclic Dependency",
        "detected": True,
        "confidence": 0.9,
        "severity": "HIGH",
        "rationale": "Two services call each other synchronously.",
        "refactoring_recommended": True,
    }
    with patch("pipeline.llm_detection.call_llm", return_value=_fake_result(json.dumps(expected))) as mock_call:
        result = detect_smell(["a", "b", "a"], [], tmp_path_evidence(), max_retries=3)

    assert mock_call.call_count == 1
    assert result["cycle"] == ["a", "b", "a"]
    assert result["detected"] is True
    assert result["severity"] == "HIGH"


def test_detect_smell_retries_on_malformed_json_then_succeeds():
    good = {
        "smell": "Cyclic Dependency",
        "detected": False,
        "confidence": 0.4,
        "severity": "LOW",
        "rationale": "Not a real problem here.",
        "refactoring_recommended": False,
    }
    responses = [_fake_result("not json"), _fake_result(json.dumps(good))]
    with patch("pipeline.llm_detection.call_llm", side_effect=responses) as mock_call:
        result = detect_smell(["a", "b", "a"], [], tmp_path_evidence(), max_retries=3)

    assert mock_call.call_count == 2
    assert result["detected"] is False


def test_detect_smell_raises_after_exhausting_retries():
    with patch("pipeline.llm_detection.call_llm", return_value=_fake_result("still not json")):
        try:
            detect_smell(["a", "b", "a"], [], tmp_path_evidence(), max_retries=2)
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "2 attempts" in str(exc)


def test_run_reports_zero_cycles_without_calling_llm(tmp_path):
    edges_path = tmp_path / "edges.json"
    edges_path.write_text(json.dumps([
        {"caller": "api-gateway", "callee": "customers-service", "source": "webclient", "file": "x.java", "line": 1, "evidence": "x"},
    ]), encoding="utf-8")

    with patch("pipeline.llm_detection.call_llm") as mock_call:
        output = run(edges_path, tmp_path)

    mock_call.assert_not_called()
    assert output == {"cycles_found": 0, "results": []}


def tmp_path_evidence():
    # detect_smell only needs repo_root to resolve evidence file paths for
    # snippet reading; the tests above pass empty evidence lists, so any
    # existing directory works here.
    from pathlib import Path

    return Path(".")
