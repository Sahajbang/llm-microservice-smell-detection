"""Autonomous refactoring: patch generation, git worktree application,
build verification and the approve -> apply -> verify -> commit/rollback loop.

Real git (a temporary repository per test), mocked LLM, and a stub build
command (the test interpreter exiting 0 or 1) instead of Maven, so the
suite runs offline in seconds.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.config import RefactorConfig, default_config
from pipeline.llm_client import LLMResult
from pipeline.models import Finding
from pipeline.patch_applier import ApplyError, RefactorWorkspace
from pipeline.patch_generator import (
    FileChange,
    Patch,
    build_changes,
    generate_patch,
    normalize_rel_path,
    parse_patch_response,
)
from pipeline.run_logger import RunLogger
from pipeline.run_pipeline import analyze_repository, console_approver, main
from pipeline.verifier import Verifier, find_build_unit

ORDER_CLIENT = "payment-mod/src/main/java/OrderClient.java"
DELETE_ORDER_CLIENT = f"<<<DELETE {ORDER_CLIENT}>>>"

DETECTION_JSON = json.dumps(
    {
        "smell": "Cyclic Dependency",
        "detected": True,
        "confidence": 0.9,
        "severity": "HIGH",
        "rationale": "Real cycle.",
        "refactoring_recommended": True,
    }
)
PLAN = {
    "smell": "Cyclic Dependency",
    "affected_files": [ORDER_CLIENT],
    "changes": [{"file": ORDER_CLIENT, "method_or_class": "OrderClient", "description": "Remove the back-call."}],
    "rationale": "Payment no longer calls order, which breaks the cycle.",
    "expected_impact": "One-directional dependency.",
}

PASS_CMD = f'"{sys.executable}" -c "raise SystemExit(0)"'
FAIL_CMD = f'"{sys.executable}" -c "raise SystemExit(1)"'


def llm(content: str) -> LLMResult:
    return LLMResult(
        content=content,
        reasoning="",
        model="test-model",
        system_prompt="sys",
        user_prompt="user",
        temperature=0.2,
        max_tokens=4096,
    )


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def init_repo(path: Path) -> Path:
    git(path, "init", "-q")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "init")
    return path


def finding(services=("order-service", "payment-service")) -> Finding:
    return Finding(
        smell="cyclic_dependency",
        smell_name="Cyclic Dependency",
        key="cyclic_dependency:order-service-payment-service",
        services=list(services),
        files=[],
        severity="HIGH",
        confidence=0.95,
        description="order-service and payment-service call each other.",
    )


@pytest.fixture
def git_cycle_repo(cycle_repo: Path) -> Path:
    return init_repo(cycle_repo)


@pytest.fixture
def wt_root(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("worktrees")


@pytest.fixture
def logger(tmp_path_factory) -> RunLogger:
    return RunLogger("test-run", base_dir=tmp_path_factory.mktemp("runs"))


def apply_config(wt_root: Path, **refactor):
    config = default_config().with_detectors(["cyclic_dependency"])
    refactor.setdefault("verify_command", PASS_CMD)
    return replace(config, refactor=replace(config.refactor, worktree_root=wt_root, **refactor))


def run(repo: Path, config, logger, patch_response: str = DELETE_ORDER_CLIENT, approver=None):
    with (
        patch("pipeline.llm_detection.call_llm", return_value=llm(DETECTION_JSON)),
        patch("pipeline.llm_refactoring.call_llm", return_value=llm(json.dumps(PLAN))),
        patch("pipeline.patch_generator.call_llm", return_value=llm(patch_response)) as patch_call,
    ):
        result = analyze_repository(str(repo), config=config, logger=logger, approver=approver)
    return result, patch_call


def branches(repo: Path) -> list[str]:
    return [b.strip("* ").strip() for b in git(repo, "branch", "--list", "refactor/*").splitlines()]


# -- patch parsing and validation ----------------------------------------------


def test_parses_file_blocks_and_deletions_and_strips_stray_fences():
    text = (
        "<<<FILE a/X.java>>>\n```java\nclass X {}\n```\n<<<END FILE>>>\n"
        "<<<FILE a/Y.java>>>\nclass Y {}\n<<<END FILE>>>\n"
        "<<<DELETE a/Z.java>>>\n"
    )
    assert parse_patch_response(text) == {"a/X.java": "class X {}\n", "a/Y.java": "class Y {}\n", "a/Z.java": None}


@pytest.mark.parametrize("bad", ["../outside.java", "a/../../x", "/etc/passwd", "C:/Windows/x", "a/.git/config", ""])
def test_rejects_paths_that_escape_the_repository(bad: str):
    with pytest.raises(ValueError):
        normalize_rel_path(bad)


def test_normalizes_harmless_path_spellings():
    assert normalize_rel_path(" ./a\\b/C.java ") == "a/b/C.java"


def test_patch_outside_the_findings_modules_is_rejected(git_cycle_repo: Path):
    with pytest.raises(ValueError, match="outside the services"):
        build_changes(
            {"other-mod/X.java": "class X {}\n"},
            git_cycle_repo,
            allowed_prefixes={"order-mod", "payment-mod"},
            planned_files=set(),
        )


def test_without_resolvable_modules_only_planned_files_may_change(git_cycle_repo: Path):
    with pytest.raises(ValueError, match="not in the refactoring plan"):
        build_changes({"order-mod/X.java": "x\n"}, git_cycle_repo, allowed_prefixes=set(), planned_files={"a.java"})


def test_echoing_unchanged_files_is_not_a_patch(git_cycle_repo: Path):
    current = (git_cycle_repo / ORDER_CLIENT).read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="nothing would change"):
        build_changes({ORDER_CLIENT: current}, git_cycle_repo, allowed_prefixes={"payment-mod"}, planned_files=set())


def test_bad_patch_is_reprompted_with_the_reason(git_cycle_repo: Path):
    responses = [llm("I would delete the client."), llm(f"<<<FILE ../evil.txt>>>\nx\n<<<END FILE>>>"), llm(DELETE_ORDER_CLIENT)]
    with patch("pipeline.patch_generator.call_llm", side_effect=responses) as call:
        result = generate_patch(finding(), PLAN, git_cycle_repo, allowed_prefixes={"order-mod", "payment-mod"})

    assert result.paths == [ORDER_CLIENT]
    assert result.changes[0].action == "delete"
    assert "no <<<FILE" in call.call_args_list[1].args[1]
    assert "escapes the repository" in call.call_args_list[2].args[1]


def test_patch_prompt_carries_the_current_file_contents(git_cycle_repo: Path):
    with patch("pipeline.patch_generator.call_llm", return_value=llm(DELETE_ORDER_CLIENT)) as call:
        generate_patch(finding(), PLAN, git_cycle_repo, allowed_prefixes={"payment-mod"})
    assert '@FeignClient(name = "order-service")' in call.call_args.args[1]


def test_oversized_input_is_refused_rather_than_truncated(git_cycle_repo: Path):
    with pytest.raises(ValueError, match="too large"):
        generate_patch(finding(), PLAN, git_cycle_repo, allowed_prefixes={"payment-mod"}, max_input_chars=5)


# -- git worktree --------------------------------------------------------------


def make_patch(path: str, new: str | None, old: str | None, action: str) -> Patch:
    return Patch("k", [FileChange(path=path, action=action, old=old, new=new)], diff="")


def test_worktree_isolates_changes_from_the_users_checkout(git_cycle_repo: Path, wt_root: Path):
    ws = RefactorWorkspace.create(git_cycle_repo, "run1", wt_root)
    ws.write(make_patch(ORDER_CLIENT, None, "x", "delete"))
    sha = ws.commit(make_patch(ORDER_CLIENT, None, "x", "delete"), "drop client")
    summary = ws.close()

    assert (git_cycle_repo / ORDER_CLIENT).exists(), "the analyzed tree must never change"
    assert git(git_cycle_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() != "refactor/run1"
    assert summary["branch"] == "refactor/run1" and summary["commits"] == [sha]
    assert not ws.worktree.exists()
    assert ORDER_CLIENT not in git(git_cycle_repo, "ls-tree", "-r", "--name-only", "refactor/run1")


def test_rollback_restores_modified_and_removes_created_files(git_cycle_repo: Path, wt_root: Path):
    ws = RefactorWorkspace.create(git_cycle_repo, "run2", wt_root)
    original = (ws.root / ORDER_CLIENT).read_text(encoding="utf-8")
    p = Patch(
        "k",
        [
            FileChange(ORDER_CLIENT, "modify", original, "changed\n"),
            FileChange("payment-mod/src/main/java/New.java", "create", None, "class New {}\n"),
        ],
        "",
    )
    ws.write(p)
    ws.rollback(p)

    assert (ws.root / ORDER_CLIENT).read_text(encoding="utf-8") == original
    assert not (ws.root / "payment-mod/src/main/java/New.java").exists()
    assert ws.close()["branch"] is None
    assert branches(git_cycle_repo) == [], "a branch with no commits is deleted"


def test_crlf_files_keep_their_line_endings(git_cycle_repo: Path, wt_root: Path):
    ws = RefactorWorkspace.create(git_cycle_repo, "run3", wt_root)
    target = ws.root / ORDER_CLIENT
    target.write_bytes(b"a\r\nb\r\n")
    ws.write(make_patch(ORDER_CLIENT, "a\nc\n", "a\nb\n", "modify"))
    assert target.read_bytes() == b"a\r\nc\r\n"
    ws.close()


def test_refuses_a_tree_with_uncommitted_changes(git_cycle_repo: Path, wt_root: Path):
    (git_cycle_repo / ORDER_CLIENT).write_text("edited\n", encoding="utf-8")
    with pytest.raises(ApplyError, match="uncommitted"):
        RefactorWorkspace.create(git_cycle_repo, "run4", wt_root)


def test_refuses_a_directory_that_is_not_a_git_repository(cycle_repo: Path, wt_root: Path, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(cycle_repo.parent))  # in case the temp dir is in a repo
    with pytest.raises(ApplyError, match="not inside a git repository"):
        RefactorWorkspace.create(cycle_repo, "run5", wt_root)


def test_works_on_a_subdirectory_of_a_larger_repository(tmp_path: Path, wt_root: Path):
    from tests.conftest import write_module

    write_module(tmp_path / "target", "order-mod", "order-service")
    (tmp_path / "README.md").write_text("outer", encoding="utf-8")
    init_repo(tmp_path)
    ws = RefactorWorkspace.create(tmp_path / "target", "run6", wt_root)
    assert ws.root == (ws.worktree / "target").resolve()
    assert (ws.root / "order-mod" / "pom.xml").exists()
    ws.close()


# -- build verification ----------------------------------------------------------


def test_multi_module_build_runs_from_the_aggregator(tmp_path: Path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "pom.xml").write_text("<project/>", encoding="utf-8")
    unit = find_build_unit(tmp_path, tmp_path / "svc")
    assert unit.system.name == "maven" and unit.directory == tmp_path.resolve()


def test_independent_service_builds_run_per_service(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")  # root tooling, not the service build
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "build.gradle").write_text("", encoding="utf-8")
    unit = find_build_unit(tmp_path, tmp_path / "svc")
    assert unit.system.name == "gradle" and unit.directory == (tmp_path / "svc").resolve()


@pytest.mark.parametrize(
    "marker,system",
    [("build.xml", "ant"), ("go.mod", "go"), ("Cargo.toml", "cargo"), ("App.csproj", "dotnet"), ("package.json", "npm")],
)
def test_detects_non_jvm_and_ant_builds(tmp_path: Path, marker: str, system: str):
    (tmp_path / marker).write_text("", encoding="utf-8")
    assert find_build_unit(tmp_path, tmp_path).system.name == system


def test_no_build_system_is_skipped_not_failed(tmp_path: Path):
    verifier = Verifier(tmp_path, RefactorConfig())
    units = verifier.units_for([tmp_path])
    assert units == []
    assert verifier.verify(units)["status"] == "skipped"


def test_a_build_already_failing_before_the_patch_cannot_fail_it(tmp_path: Path):
    verifier = Verifier(tmp_path, RefactorConfig(verify_command=FAIL_CMD))
    units = verifier.units_for([])
    verifier.baseline(units)
    outcome = verifier.verify(units)
    assert outcome["status"] == "skipped"
    assert "already failing" in outcome["reason"]


def test_custom_command_result_is_reported(tmp_path: Path):
    ok = Verifier(tmp_path, RefactorConfig(verify_command=PASS_CMD))
    bad = Verifier(tmp_path, RefactorConfig(verify_command=FAIL_CMD))
    assert ok.verify(ok.units_for([]))["status"] == "passed"
    failed = bad.verify(bad.units_for([]))
    assert failed["status"] == "failed" and failed["runs"][0]["returncode"] == 1


# -- end to end ------------------------------------------------------------------


def test_auto_mode_applies_verifies_and_commits(git_cycle_repo: Path, wt_root: Path, logger: RunLogger):
    result, _ = run(git_cycle_repo, apply_config(wt_root, mode="auto"), logger)

    p = result.results[0].patch
    assert p["status"] == "verified", p
    assert p["verification"]["redetection"]["resolved"] is True
    assert p["verification"]["build"]["status"] == "passed"
    branch = result.refactor["branch"]
    assert branch and branches(git_cycle_repo) == [branch]
    assert ORDER_CLIENT not in git(git_cycle_repo, "ls-tree", "-r", "--name-only", branch)
    assert (git_cycle_repo / ORDER_CLIENT).exists(), "the analyzed tree must never change"
    assert result.counts()["patches_by_status"] == {"verified": 1}
    assert (logger.dir / "03_apply_summary.json").exists()


def test_failed_build_is_rolled_back_by_default(git_cycle_repo: Path, wt_root: Path, logger: RunLogger):
    # The baseline also fails with FAIL_CMD, so use a command that only fails
    # once the client file is gone -- i.e. the patch broke the build.
    probe = f'"{sys.executable}" -c "import os,sys; sys.exit(0 if os.path.exists(r\'{ORDER_CLIENT}\') else 1)"'
    result, _ = run(git_cycle_repo, apply_config(wt_root, mode="auto", verify_command=probe), logger)

    p = result.results[0].patch
    assert p["status"] == "rolled_back"
    assert p["verification"]["build"]["status"] == "failed"
    assert result.refactor["branch"] is None and branches(git_cycle_repo) == []


def test_keep_policy_commits_a_failed_patch_marked_unverified(git_cycle_repo: Path, wt_root: Path, logger: RunLogger):
    # Re-detection fails here: the patch changes a file but the cycle stays.
    keep_cycle = f"<<<FILE {ORDER_CLIENT}>>>\n// still here\n@FeignClient(name = \"order-service\")\npublic interface OrderClient {{}}\n<<<END FILE>>>"
    result, _ = run(
        git_cycle_repo, apply_config(wt_root, mode="auto", on_failure="keep"), logger, patch_response=keep_cycle
    )

    p = result.results[0].patch
    assert p["status"] == "failed_kept"
    assert p["verification"]["redetection"]["still_detected"] is True
    assert "[unverified]" in git(git_cycle_repo, "log", "-1", "--format=%s", result.refactor["branch"])


def test_ask_mode_respects_a_rejection(git_cycle_repo: Path, wt_root: Path, logger: RunLogger):
    asked = []

    def decide(kind, request):
        asked.append((kind, request["finding_key"], request["diff"]))
        return "reject"

    result, _ = run(git_cycle_repo, apply_config(wt_root, mode="ask"), logger, approver=decide)

    assert asked and asked[0][0] == "apply" and ORDER_CLIENT in asked[0][2]
    assert result.results[0].patch["status"] == "rejected"
    assert branches(git_cycle_repo) == []


def test_ask_mode_asks_what_to_do_with_a_failed_patch(git_cycle_repo: Path, wt_root: Path, logger: RunLogger):
    kinds = []

    def decide(kind, request):
        kinds.append(kind)
        return "approve" if kind == "apply" else "keep"

    config = apply_config(wt_root, mode="ask", on_failure="rollback")
    keep_cycle = f"<<<FILE {ORDER_CLIENT}>>>\n@FeignClient(name = \"order-service\")\ninterface OrderClient {{}}\n<<<END FILE>>>"
    result, _ = run(git_cycle_repo, config, logger, patch_response=keep_cycle, approver=decide)

    assert kinds == ["apply", "failure"]
    assert result.results[0].patch["status"] == "failed_kept"


def test_ask_mode_requires_an_approver(git_cycle_repo: Path, wt_root: Path, logger: RunLogger):
    with pytest.raises(ValueError, match="approver"):
        analyze_repository(str(git_cycle_repo), config=apply_config(wt_root, mode="ask"), logger=logger)


def test_dry_run_generates_a_diff_and_writes_nothing(git_cycle_repo: Path, wt_root: Path, logger: RunLogger):
    result, _ = run(git_cycle_repo, apply_config(wt_root, mode="dry-run"), logger)

    p = result.results[0].patch
    assert p["status"] == "proposed" and ORDER_CLIENT in p["diff"]
    assert branches(git_cycle_repo) == [] and list(wt_root.iterdir()) == []


def test_never_mode_does_not_generate_patches(git_cycle_repo: Path, wt_root: Path, logger: RunLogger):
    result, patch_call = run(git_cycle_repo, apply_config(wt_root, mode="never"), logger)
    patch_call.assert_not_called()
    assert result.results[0].patch is None and result.refactor is None
    assert "patches_by_status" not in result.counts()


def test_non_git_tree_is_an_error_not_a_crash(cycle_repo: Path, wt_root: Path, logger: RunLogger, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(cycle_repo.parent))
    result, _ = run(cycle_repo, apply_config(wt_root, mode="auto"), logger)
    assert any(e.stage == "apply" and "git" in e.message for e in result.errors)
    assert result.results[0].refactoring is not None, "earlier stages are unaffected"


def test_patch_generation_failure_is_isolated_to_the_finding(git_cycle_repo: Path, wt_root: Path, logger: RunLogger):
    result, _ = run(git_cycle_repo, apply_config(wt_root, mode="auto"), logger, patch_response="no blocks here")
    entry = result.results[0]
    assert entry.patch["status"] == "error"
    assert any(e.stage == "apply" for e in entry.errors)
    assert branches(git_cycle_repo) == []


# -- CLI -------------------------------------------------------------------------


def test_console_approver_maps_answers_and_defaults_safely():
    answers = iter(["maybe", "a"])
    decide = console_approver(ask=lambda _: next(answers), say=lambda _: None)
    request = {"finding_key": "k", "smell_name": "S", "files": [], "diff": ""}
    assert decide("apply", request) == "approve_all"

    def eof(_):
        raise EOFError

    silent = console_approver(ask=eof, say=lambda _: None)
    assert silent("apply", request) == "reject"
    failure = {**request, "default": "keep", "verification": {"build": {"status": "failed", "runs": []}, "redetection": {"resolved": False}}}
    assert silent("failure", failure) == "keep"


def test_cli_rejects_apply_without_llm(git_cycle_repo: Path):
    with pytest.raises(SystemExit):
        main([str(git_cycle_repo), "--no-llm", "--apply", "auto"])
