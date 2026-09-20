"""Smell-aware LLM detection/refactoring tests.

Every LLM call is mocked: these tests exercise prompt construction, smell
selection, JSON repair/retry, scope enforcement and logging without a key
or a network. They also lock the Phase 1 Cyclic Dependency prompts
byte-for-byte, so the generalization cannot silently change what the model
was asked in previously logged runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.llm_client import LLMResult
from pipeline.llm_detection import build_finding_prompt, detect_finding
from pipeline.llm_detection import SYSTEM_PROMPT as DETECTION_SYSTEM_PROMPT
from pipeline.llm_refactoring import SYSTEM_PROMPT as REFACTORING_SYSTEM_PROMPT
from pipeline.llm_refactoring import propose_for_finding
from pipeline.models import Finding
from pipeline.run_logger import RunLogger
from pipeline.smells import (
    CYCLIC_DEPENDENCY,
    HUB_DEPENDENCY,
    SHARED_PERSISTENCE,
    detection_system_prompt,
    get_spec,
    refactoring_system_prompt,
)
from tests.conftest import write_module


def fake_result(content: str) -> LLMResult:
    return LLMResult(
        content=content,
        reasoning="",
        model="nvidia/nemotron-3-ultra-550b-a55b",
        system_prompt="sys",
        user_prompt="user",
        temperature=0.2,
        max_tokens=4096,
    )


def cyclic_finding() -> Finding:
    return Finding(
        smell=CYCLIC_DEPENDENCY,
        smell_name="Cyclic Dependency",
        key="cyclic_dependency:a-b",
        services=["a-service", "b-service"],
        files=["a-mod/A.java"],
        severity="HIGH",
        confidence=0.95,
        description="a-service -> b-service -> a-service form a closed dependency loop.",
        evidence=[
            {
                "caller": "a-service",
                "callee": "b-service",
                "source": "feign",
                "file": "a-mod/A.java",
                "line": 1,
                "evidence": '@FeignClient("b-service")',
            }
        ],
        metrics={"cycle_length": 2},
    )


def hub_finding() -> Finding:
    return Finding(
        smell=HUB_DEPENDENCY,
        smell_name="Hub-like Dependency",
        key="hub_dependency:core",
        services=["a-service", "b-service"],
        files=["a-mod/A.java"],
        severity="HIGH",
        confidence=0.9,
        description="a-service is connected to 4 of 5 other services.",
        evidence=[],
        metrics={"degree": 5, "neighbour_count": 4},
    )


def persistence_finding() -> Finding:
    return Finding(
        smell=SHARED_PERSISTENCE,
        smell_name="Shared Persistence",
        key="shared_persistence:datasource:mysql-db-shop",
        services=["a-service", "b-service"],
        files=["a-mod/src/main/resources/application.yml"],
        severity="HIGH",
        confidence=0.9,
        description="2 services are configured against the same database identity.",
        evidence=[
            {
                "service": "a-service",
                "kind": "datasource",
                "value": "mysql://db:3306/shop",
                "file": "a-mod/src/main/resources/application.yml",
                "line": 4,
                "evidence": "url: jdbc:mysql://db:3306/shop",
                "confidence": 0.9,
                "metadata": {"driver": "mysql", "resolved": True},
            }
        ],
        metrics={"rule": "shared_datasource"},
    )


VALID_DETECTION = json.dumps(
    {
        "smell": "Cyclic Dependency",
        "detected": True,
        "confidence": 0.9,
        "severity": "HIGH",
        "rationale": "Because the services call each other.",
        "refactoring_recommended": True,
    }
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    write_module(tmp_path, "a-mod", "a-service")
    write_module(tmp_path, "b-mod", "b-service")
    write_module(tmp_path, "c-mod", "c-service")
    return tmp_path


# -- Phase 1 prompt compatibility --------------------------------------------


def test_cyclic_detection_system_prompt_is_unchanged_from_phase1():
    """Locks the Phase 1 wording so old and new runs stay comparable."""
    assert detection_system_prompt(get_spec(CYCLIC_DEPENDENCY)) == DETECTION_SYSTEM_PROMPT
    assert '"smell": "Cyclic Dependency"' in DETECTION_SYSTEM_PROMPT
    assert "ONE candidate Cyclic Dependency" in DETECTION_SYSTEM_PROMPT
    assert "grounded in the specific code evidence given" in DETECTION_SYSTEM_PROMPT


def test_cyclic_refactoring_system_prompt_is_unchanged_from_phase1():
    assert refactoring_system_prompt(get_spec(CYCLIC_DEPENDENCY)) == REFACTORING_SYSTEM_PROMPT
    assert "why this specific fix breaks the cycle" in REFACTORING_SYSTEM_PROMPT
    assert "outside the services named in the cycle" in REFACTORING_SYSTEM_PROMPT


# -- Smell-aware prompt construction -----------------------------------------


@pytest.mark.parametrize(
    "smell,expected_phrase",
    [
        (CYCLIC_DEPENDENCY, "Cyclic Dependency"),
        (HUB_DEPENDENCY, "Hub-like Dependency"),
        (SHARED_PERSISTENCE, "Shared Persistence"),
    ],
)
def test_each_smell_gets_its_own_definition_and_schema(smell: str, expected_phrase: str):
    prompt = detection_system_prompt(get_spec(smell))
    assert expected_phrase in prompt
    assert f'"smell": "{expected_phrase}"' in prompt


def test_hub_prompt_warns_against_flagging_legitimate_gateways():
    prompt = detection_system_prompt(get_spec(HUB_DEPENDENCY))
    assert "API gateway" in prompt


def test_persistence_prompt_states_static_analysis_limits():
    prompt = detection_system_prompt(get_spec(SHARED_PERSISTENCE))
    assert "externalized" in prompt.lower()


def test_finding_prompt_includes_deterministic_reasoning(repo: Path):
    """The model validates a stated claim; it is not asked to rediscover it."""
    finding = hub_finding()
    prompt = build_finding_prompt(finding, get_spec(HUB_DEPENDENCY), repo)

    assert finding.description in prompt
    assert "Deterministic severity: HIGH" in prompt
    assert "neighbour_count" in prompt
    assert "Limits of this evidence" in prompt


def test_finding_prompt_renders_persistence_evidence(repo: Path):
    prompt = build_finding_prompt(persistence_finding(), get_spec(SHARED_PERSISTENCE), repo)
    assert "a-service (datasource)" in prompt
    assert "mysql://db:3306/shop" in prompt
    assert "Extractor confidence: 0.9" in prompt


# -- Detection behaviour ------------------------------------------------------


def test_detect_finding_returns_verdict_with_finding_identity(repo: Path):
    with patch("pipeline.llm_detection.call_llm", return_value=fake_result(VALID_DETECTION)) as call:
        result = detect_finding(cyclic_finding(), repo)

    assert call.call_count == 1
    assert result["detected"] is True
    assert result["finding_key"] == "cyclic_dependency:a-b"
    assert result["smell_key"] == CYCLIC_DEPENDENCY
    # The model's own display string is preserved, not overwritten.
    assert result["smell"] == "Cyclic Dependency"


def test_detect_finding_uses_the_smells_own_system_prompt(repo: Path):
    with patch("pipeline.llm_detection.call_llm", return_value=fake_result(VALID_DETECTION)) as call:
        detect_finding(persistence_finding(), repo)

    system_prompt = call.call_args.args[0]
    assert "Shared Persistence" in system_prompt
    assert "Cyclic Dependency" not in system_prompt


def test_detect_finding_retries_on_malformed_json(repo: Path):
    responses = [fake_result("not json at all"), fake_result(VALID_DETECTION)]
    with patch("pipeline.llm_detection.call_llm", side_effect=responses) as call:
        result = detect_finding(cyclic_finding(), repo, max_retries=3)

    assert call.call_count == 2
    assert result["detected"] is True
    assert "could not be parsed as JSON" in call.call_args_list[1].args[1]


def test_detect_finding_raises_after_exhausting_retries(repo: Path):
    with patch("pipeline.llm_detection.call_llm", return_value=fake_result("nope")):
        with pytest.raises(ValueError, match="2 attempts"):
            detect_finding(cyclic_finding(), repo, max_retries=2)


def test_detect_finding_logs_prompt_response_and_result(repo: Path, tmp_path: Path):
    logger = RunLogger("test-detection", base_dir=tmp_path / "runs")
    with patch("pipeline.llm_detection.call_llm", return_value=fake_result(VALID_DETECTION)):
        detect_finding(cyclic_finding(), repo, logger=logger)

    logged = json.loads((logger.dir / "detection_cyclic_dependency-a-b.json").read_text(encoding="utf-8"))
    assert logged["result"]["detected"] is True
    assert logged["attempts"][0]["parse_ok"] is True
    assert logged["attempts"][0]["model"]
    assert "system_prompt" in logged
    assert "api_key" not in json.dumps(logged).lower()


# -- Refactoring behaviour ----------------------------------------------------


def _plan(files: list[str]) -> str:
    return json.dumps(
        {
            "smell": "Cyclic Dependency",
            "affected_files": files,
            "changes": [{"file": files[0], "method_or_class": "X", "description": "do the thing"}],
            "rationale": "Breaks the coupling.",
            "expected_impact": "Independent deployability.",
        }
    )


def test_propose_for_finding_accepts_in_scope_plan(repo: Path):
    detection = {"severity": "HIGH", "confidence": 0.9, "rationale": "bad"}
    with patch(
        "pipeline.llm_refactoring.call_llm", return_value=fake_result(_plan(["a-mod/src/main/java/A.java"]))
    ) as call:
        result = propose_for_finding(cyclic_finding(), detection, repo)

    assert call.call_count == 1
    assert result["affected_files"] == ["a-mod/src/main/java/A.java"]
    assert result["finding_key"] == "cyclic_dependency:a-b"


def test_propose_for_finding_rejects_and_reprompts_out_of_scope_files(repo: Path):
    detection = {"severity": "HIGH", "confidence": 0.9, "rationale": "bad"}
    responses = [
        fake_result(_plan(["c-mod/src/main/java/Sneaky.java"])),
        fake_result(_plan(["b-mod/src/main/java/Fine.java"])),
    ]
    with patch("pipeline.llm_refactoring.call_llm", side_effect=responses) as call:
        result = propose_for_finding(cyclic_finding(), detection, repo)

    assert call.call_count == 2
    assert "c-mod/src/main/java/Sneaky.java" in call.call_args_list[1].args[1]
    assert result["affected_files"] == ["b-mod/src/main/java/Fine.java"]


def test_scope_enforcement_applies_to_every_smell(repo: Path):
    """A Shared Persistence plan must not wander into an unrelated service."""
    detection = {"severity": "HIGH", "confidence": 0.9, "rationale": "shared db"}
    with patch(
        "pipeline.llm_refactoring.call_llm", return_value=fake_result(_plan(["c-mod/src/main/java/Other.java"]))
    ):
        with pytest.raises(ValueError, match="2 attempts"):
            propose_for_finding(persistence_finding(), detection, repo, max_retries=2)


def test_scope_check_is_skipped_and_disclosed_when_no_module_matches(repo: Path):
    """A finding about a compose-only service has no module to scope to."""
    finding = Finding(
        smell=SHARED_PERSISTENCE,
        smell_name="Shared Persistence",
        key="shared_persistence:datasource:db",
        services=["mysql-container", "redis-container"],
        files=["docker-compose.yml"],
        severity="MEDIUM",
        confidence=0.6,
        description="Two compose services share a database.",
        evidence=[],
        metrics={},
    )
    detection = {"severity": "MEDIUM", "confidence": 0.6, "rationale": "shared"}
    logger = RunLogger("scope-skip", base_dir=repo / "runs")

    with patch("pipeline.llm_refactoring.call_llm", return_value=fake_result(_plan(["anything/at/all.java"]))):
        result = propose_for_finding(finding, detection, repo, logger=logger)

    assert result["affected_files"] == ["anything/at/all.java"]
    logged = json.loads(next(logger.dir.glob("refactoring_*.json")).read_text(encoding="utf-8"))
    assert logged["scope_enforced"] is False
    assert logged["attempts"][0]["scope_enforced"] is False


def test_refactoring_uses_the_smells_own_guidance(repo: Path):
    detection = {"severity": "HIGH", "confidence": 0.9, "rationale": "shared db"}
    with patch(
        "pipeline.llm_refactoring.call_llm",
        return_value=fake_result(_plan(["a-mod/src/main/resources/application.yml"])),
    ) as call:
        propose_for_finding(persistence_finding(), detection, repo)

    system_prompt = call.call_args.args[0]
    assert "own datasource/schema" in system_prompt
    assert "breaks the cycle" not in system_prompt
