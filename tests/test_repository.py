"""Repository acquisition tests.

No test here reaches the network: clone behaviour is exercised by mocking
the git subprocess call, so the URL validation, destination derivation,
failure handling and cleanup logic are all covered offline.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.config import RepositoryConfig
from pipeline.repository import (
    RepositoryError,
    acquire_repository,
    clone_repository,
    looks_like_git_url,
    repository_name_from_url,
    repository_workspace,
    safe_clone_dir,
    use_local_repository,
    validate_git_url,
)


# -- URL classification and validation ---------------------------------------


@pytest.mark.parametrize(
    "source,expected",
    [
        ("https://github.com/owner/repo.git", True),
        ("https://github.com/owner/repo", True),
        ("git@github.com:owner/repo.git", True),
        ("ssh://git@host/repo.git", True),
        ("target-repo", False),
        ("./some/local/path", False),
        ("E:\\tarp-project\\target-repo", False),
    ],
)
def test_looks_like_git_url(source: str, expected: bool):
    assert looks_like_git_url(source) is expected


def test_validate_accepts_https_with_and_without_git_suffix():
    assert validate_git_url("https://github.com/owner/repo.git")
    assert validate_git_url("https://github.com/owner/repo")


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "--upload-pack=touch /tmp/pwned",
        "ext::sh -c 'touch /tmp/pwned'",
        "file:///etc/passwd",
        "ssh://git@github.com/owner/repo.git",
        "git://github.com/owner/repo.git",
        "https://",
        "not-a-url",
    ],
)
def test_validate_rejects_unsafe_or_unsupported_urls(url: str):
    with pytest.raises(RepositoryError):
        validate_git_url(url)


def test_validate_rejects_control_characters():
    with pytest.raises(RepositoryError):
        validate_git_url("https://github.com/owner/repo\n--exec=evil")


def test_repository_name_from_url():
    assert repository_name_from_url("https://github.com/owner/repo.git") == "repo"
    assert repository_name_from_url("https://github.com/owner/repo") == "repo"
    assert repository_name_from_url("https://host/") == "repository"


# -- Clone destination safety -------------------------------------------------


def test_safe_clone_dir_is_inside_workspace_and_stable(tmp_path: Path):
    url = "https://github.com/owner/repo.git"
    first = safe_clone_dir(url, tmp_path)
    second = safe_clone_dir(url, tmp_path)

    assert first == second, "same URL must map to the same directory"
    assert tmp_path.resolve() in first.parents
    assert first.name.startswith("repo-")


def test_safe_clone_dir_separates_same_name_different_hosts(tmp_path: Path):
    a = safe_clone_dir("https://github.com/one/repo.git", tmp_path)
    b = safe_clone_dir("https://gitlab.com/two/repo.git", tmp_path)
    assert a != b


def test_safe_clone_dir_neutralizes_path_traversal(tmp_path: Path):
    target = safe_clone_dir("https://host/owner/..%2f..%2fetc.git", tmp_path)
    assert tmp_path.resolve() in target.parents
    assert ".." not in target.name


# -- Cloning ------------------------------------------------------------------


def _fake_completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["git"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_clone_invokes_git_with_argument_list_and_no_shell(tmp_path: Path):
    config = RepositoryConfig(workspace=tmp_path / "ws")
    url = "https://github.com/owner/repo.git"

    def fake_run(args, **kwargs):
        # Simulate git creating the destination directory.
        Path(args[-1]).mkdir(parents=True, exist_ok=True)
        return _fake_completed()

    with patch("pipeline.repository.subprocess.run", side_effect=fake_run) as mock_run:
        workspace = clone_repository(url, config=config)

    called_args = mock_run.call_args.args[0]
    assert called_args[0] == "git"
    assert isinstance(called_args, list), "git must be invoked with an argument list, never a shell string"
    assert mock_run.call_args.kwargs.get("shell") in (None, False)
    assert "--" in called_args and called_args[called_args.index("--") + 1] == url
    assert "protocol.ext.allow=never" in called_args
    assert workspace.info.kind == "git"
    assert workspace.info.is_temporary is True


def test_clone_passes_branch_when_requested(tmp_path: Path):
    config = RepositoryConfig(workspace=tmp_path / "ws")

    def fake_run(args, **kwargs):
        Path(args[-1]).mkdir(parents=True, exist_ok=True)
        return _fake_completed()

    with patch("pipeline.repository.subprocess.run", side_effect=fake_run) as mock_run:
        clone_repository("https://github.com/owner/repo.git", branch="develop", config=config)

    args = mock_run.call_args.args[0]
    assert "--branch" in args and args[args.index("--branch") + 1] == "develop"


def test_clone_rejects_branch_that_looks_like_an_option(tmp_path: Path):
    config = RepositoryConfig(workspace=tmp_path / "ws")
    with patch("pipeline.repository.subprocess.run", return_value=_fake_completed()):
        with pytest.raises(RepositoryError, match="option"):
            clone_repository("https://github.com/owner/repo.git", branch="--upload-pack=evil", config=config)


def test_clone_failure_raises_and_cleans_up(tmp_path: Path):
    config = RepositoryConfig(workspace=tmp_path / "ws")

    def fake_run(args, **kwargs):
        Path(args[-1]).mkdir(parents=True, exist_ok=True)
        return _fake_completed(returncode=128, stderr="repository not found")

    with patch("pipeline.repository.subprocess.run", side_effect=fake_run):
        with pytest.raises(RepositoryError, match="repository not found"):
            clone_repository("https://github.com/owner/missing.git", config=config)

    assert not safe_clone_dir("https://github.com/owner/missing.git", config.workspace).exists()


def test_clone_rejects_repository_over_size_limit(tmp_path: Path):
    config = RepositoryConfig(workspace=tmp_path / "ws", max_repo_size_mb=0)

    def fake_run(args, **kwargs):
        dest = Path(args[-1])
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "big.bin").write_bytes(b"x" * 2048)
        return _fake_completed()

    with patch("pipeline.repository.subprocess.run", side_effect=fake_run):
        with pytest.raises(RepositoryError, match="above the"):
            clone_repository("https://github.com/owner/huge.git", config=config)


def test_clone_missing_git_executable_is_reported_clearly(tmp_path: Path):
    config = RepositoryConfig(workspace=tmp_path / "ws")
    with patch("pipeline.repository.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(RepositoryError, match="git executable not found"):
            clone_repository("https://github.com/owner/repo.git", config=config)


def test_clone_timeout_is_reported_clearly(tmp_path: Path):
    config = RepositoryConfig(workspace=tmp_path / "ws", clone_timeout_seconds=1)
    with patch(
        "pipeline.repository.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=1),
    ):
        with pytest.raises(RepositoryError, match="timed out"):
            clone_repository("https://github.com/owner/slow.git", config=config)


# -- Local repositories -------------------------------------------------------


def test_use_local_repository_records_metadata(tmp_path: Path):
    (tmp_path / "some-file.txt").write_text("x", encoding="utf-8")
    workspace = use_local_repository(tmp_path)

    assert workspace.info.kind == "local"
    assert workspace.info.is_temporary is False
    assert Path(workspace.info.root) == tmp_path.resolve()


def test_use_local_repository_without_git_metadata_is_not_an_error(tmp_path: Path):
    """The Phase 1 target-repo has no nested .git; that must still work."""
    workspace = use_local_repository(tmp_path)
    assert workspace.info.commit is None
    assert workspace.info.branch is None


def test_use_local_repository_rejects_missing_path(tmp_path: Path):
    with pytest.raises(RepositoryError, match="does not exist"):
        use_local_repository(tmp_path / "nope")


def test_use_local_repository_rejects_file(tmp_path: Path):
    file_path = tmp_path / "a-file"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(RepositoryError, match="not a directory"):
        use_local_repository(file_path)


def test_acquire_repository_dispatches_on_source_kind(tmp_path: Path):
    assert acquire_repository(str(tmp_path)).info.kind == "local"


# -- Cleanup ------------------------------------------------------------------


def test_cleanup_removes_temporary_clone_but_not_local_path(tmp_path: Path):
    local = use_local_repository(tmp_path)
    local.cleanup()
    assert tmp_path.exists(), "a user-supplied local path must never be deleted"

    config = RepositoryConfig(workspace=tmp_path / "ws")

    def fake_run(args, **kwargs):
        Path(args[-1]).mkdir(parents=True, exist_ok=True)
        return _fake_completed()

    with patch("pipeline.repository.subprocess.run", side_effect=fake_run):
        cloned = clone_repository("https://github.com/owner/repo.git", config=config)

    assert cloned.root.exists()
    cloned.cleanup()
    assert not cloned.root.exists()


def test_repository_workspace_context_manager_keeps_local_path(tmp_path: Path):
    with repository_workspace(str(tmp_path)) as workspace:
        assert workspace.root.exists()
    assert tmp_path.exists()
