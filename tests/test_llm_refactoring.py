"""Step 5 tests.

The LLM call itself (pipeline.llm_client.call_llm) and Step 4's detect_smell
are mocked throughout -- these tests exercise the deterministic parts
(scope enforcement, prompt building, retry-on-bad-JSON, retry-on-out-of-
scope-files, run() wiring) without needing a real API key or network
access.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.llm_client import LLMResult
from pipeline.llm_refactoring import (
    allowed_module_prefixes,
    build_user_prompt,
    find_out_of_scope_files,
    propose_refactoring,
    run,
)


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


def _write_module(root: Path, module_name: str, service_name: str) -> None:
    module_dir = root / module_name
    module_dir.mkdir(parents=True, exist_ok=True)
    (module_dir / "pom.xml").write_text(f"<project><artifactId>{module_name}</artifactId></project>", encoding="utf-8")
    resources = module_dir / "src" / "main" / "resources"
    resources.mkdir(parents=True, exist_ok=True)
    (resources / "application.yml").write_text(f"spring:\n  application:\n    name: {service_name}\n", encoding="utf-8")


@pytest.fixture
def synthetic_repo(tmp_path: Path) -> Path:
    _write_module(tmp_path, "visits-mod", "visits-service")
    _write_module(tmp_path, "customers-mod", "customers-service")
    _write_module(tmp_path, "vets-mod", "vets-service")
    return tmp_path


def test_allowed_module_prefixes_maps_only_cycle_services(synthetic_repo: Path):
    prefixes = allowed_module_prefixes(["visits-service", "customers-service", "visits-service"], synthetic_repo)
    assert prefixes == {"visits-mod", "customers-mod"}


def test_find_out_of_scope_files_flags_files_outside_allowed_prefixes():
    plan = {
        "affected_files": ["visits-mod/src/main/java/X.java", "vets-mod/src/main/java/Y.java"],
        "changes": [{"file": "vets-mod/src/main/java/Y.java", "description": "sneaky"}],
    }
    out_of_scope = find_out_of_scope_files(plan, {"visits-mod", "customers-mod"})
    assert out_of_scope == ["vets-mod/src/main/java/Y.java"]


def test_find_out_of_scope_files_allows_files_within_scope_including_new_ones():
    plan = {
        "affected_files": [
            "visits-mod/src/main/java/Existing.java",
            "customers-mod/src/main/java/NewFile.java",
        ],
        "changes": [],
    }
    assert find_out_of_scope_files(plan, {"visits-mod", "customers-mod"}) == []


def test_build_user_prompt_includes_detection_context(tmp_path):
    detection = {"severity": "HIGH", "confidence": 0.9, "rationale": "It's bad because X."}
    prompt = build_user_prompt(["a", "b", "a"], [], detection, tmp_path)
    assert "severity=HIGH" in prompt
    assert "It's bad because X." in prompt


def test_propose_refactoring_accepts_in_scope_plan_on_first_try(synthetic_repo: Path):
    good_plan = {
        "smell": "Cyclic Dependency",
        "affected_files": ["visits-mod/src/main/java/CustomersServiceClient.java"],
        "changes": [{"file": "visits-mod/src/main/java/CustomersServiceClient.java", "description": "remove the call"}],
        "rationale": "Breaks the cycle.",
        "expected_impact": "One fewer coupling.",
    }
    detection = {"severity": "HIGH", "confidence": 0.9, "rationale": "..."}
    with patch("pipeline.llm_refactoring.call_llm", return_value=_fake_result(json.dumps(good_plan))) as mock_call:
        result = propose_refactoring(["visits-service", "customers-service", "visits-service"], [], detection, synthetic_repo)

    assert mock_call.call_count == 1
    assert result["cycle"] == ["visits-service", "customers-service", "visits-service"]
    assert result["affected_files"] == good_plan["affected_files"]


def test_propose_refactoring_reprompts_on_out_of_scope_file_then_succeeds(synthetic_repo: Path):
    bad_plan = {
        "smell": "Cyclic Dependency",
        "affected_files": ["vets-mod/src/main/java/Sneaky.java"],
        "changes": [],
        "rationale": "...",
        "expected_impact": "...",
    }
    good_plan = {**bad_plan, "affected_files": ["visits-mod/src/main/java/Fine.java"]}
    detection = {"severity": "HIGH", "confidence": 0.9, "rationale": "..."}

    responses = [_fake_result(json.dumps(bad_plan)), _fake_result(json.dumps(good_plan))]
    with patch("pipeline.llm_refactoring.call_llm", side_effect=responses) as mock_call:
        result = propose_refactoring(["visits-service", "customers-service", "visits-service"], [], detection, synthetic_repo)

    assert mock_call.call_count == 2
    assert result["affected_files"] == good_plan["affected_files"]
    # the second prompt should call out the specific offending file
    second_prompt = mock_call.call_args_list[1].args[1]
    assert "vets-mod/src/main/java/Sneaky.java" in second_prompt


def test_propose_refactoring_raises_after_exhausting_retries_on_scope_violation(synthetic_repo: Path):
    bad_plan = {
        "smell": "Cyclic Dependency",
        "affected_files": ["vets-mod/src/main/java/Sneaky.java"],
        "changes": [],
        "rationale": "...",
        "expected_impact": "...",
    }
    detection = {"severity": "HIGH", "confidence": 0.9, "rationale": "..."}
    with patch("pipeline.llm_refactoring.call_llm", return_value=_fake_result(json.dumps(bad_plan))) as mock_call:
        with pytest.raises(ValueError, match="2 attempts"):
            propose_refactoring(
                ["visits-service", "customers-service", "visits-service"], [], detection, synthetic_repo, max_retries=2
            )

    assert mock_call.call_count == 2


def test_run_skips_refactoring_when_not_recommended(tmp_path):
    edges_path = tmp_path / "edges.json"
    edges_path.write_text(
        json.dumps(
            [
                {"caller": "a", "callee": "b", "source": "feign", "file": "x.java", "line": 1, "evidence": "x"},
                {"caller": "b", "callee": "a", "source": "feign", "file": "y.java", "line": 1, "evidence": "y"},
            ]
        ),
        encoding="utf-8",
    )
    detection = {
        "smell": "Cyclic Dependency",
        "detected": True,
        "confidence": 0.6,
        "severity": "LOW",
        "rationale": "Minor.",
        "refactoring_recommended": False,
        "cycle": ["a", "b", "a"],
    }
    with (
        patch("pipeline.llm_refactoring.detect_smell", return_value=detection),
        patch("pipeline.llm_refactoring.call_llm") as mock_refactor_call,
    ):
        output = run(edges_path, tmp_path)

    mock_refactor_call.assert_not_called()
    assert output["cycles_found"] == 1
    assert "refactoring_plan" not in output["results"][0]


def test_run_proposes_refactoring_when_recommended(tmp_path):
    edges_path = tmp_path / "edges.json"
    edges_path.write_text(
        json.dumps(
            [
                {"caller": "a", "callee": "b", "source": "feign", "file": "x.java", "line": 1, "evidence": "x"},
                {"caller": "b", "callee": "a", "source": "feign", "file": "y.java", "line": 1, "evidence": "y"},
            ]
        ),
        encoding="utf-8",
    )
    detection = {
        "smell": "Cyclic Dependency",
        "detected": True,
        "confidence": 0.9,
        "severity": "HIGH",
        "rationale": "Bad.",
        "refactoring_recommended": True,
        "cycle": ["a", "b", "a"],
    }
    plan = {
        "smell": "Cyclic Dependency",
        "affected_files": [],
        "changes": [],
        "rationale": "...",
        "expected_impact": "...",
    }
    with (
        patch("pipeline.llm_refactoring.detect_smell", return_value=detection),
        patch("pipeline.llm_refactoring.call_llm", return_value=_fake_result(json.dumps(plan))),
    ):
        output = run(edges_path, tmp_path)

    assert output["cycles_found"] == 1
    assert output["results"][0]["refactoring_plan"]["cycle"] == ["a", "b", "a"]
