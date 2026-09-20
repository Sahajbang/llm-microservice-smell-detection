"""
Repository acquisition: turn whatever the user supplied -- a Git URL or a
local path -- into a local working tree plus provenance metadata.

The repository URL is untrusted input, so this module is deliberately
strict:

* Git is invoked through ``subprocess`` with an argument **list**; no shell
  string is ever constructed, so a URL cannot inject a command.
* Only the URL schemes in ``RepositoryConfig.allowed_url_schemes`` (https,
  http by default) are accepted. ``ext::``, ``file://`` and ssh URLs are
  rejected, and Git is additionally invoked with ``protocol.ext.allow=never``
  and ``protocol.file.allow=never`` so a redirect or submodule cannot
  re-enable them.
* A URL that looks like a command-line option is rejected, and ``--`` is
  passed before the URL so Git cannot interpret it as one either.
* The clone destination is derived from a sanitized repository name plus a
  hash of the URL, and the resolved path is asserted to be inside the
  configured workspace, so a crafted name cannot traverse out of it.
* Clones are shallow, time-limited and size-limited.
* Credential prompts are disabled, so a private repository fails fast
  instead of hanging. Private-repository authentication is NOT supported.

Nothing in this module (or anywhere else in the pipeline) executes code
from the analyzed repository: no build, no test run, no Python import. The
analysis is static.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import stat
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional
from urllib.parse import urlparse

from pipeline.config import AnalysisConfig, RepositoryConfig, default_config
from pipeline.models import RepositoryInfo

_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_GIT_ENV = {
    # Never prompt for credentials: a private repo must fail, not hang.
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "echo",
    "GCM_INTERACTIVE": "never",
}


class RepositoryError(RuntimeError):
    """Raised when a repository cannot be validated, cloned, or located."""


def _make_tree_writable(root: Path) -> None:
    """Clear the read-only bit Git sets on pack files, so the tree can be deleted."""
    for path in root.rglob("*"):
        try:
            path.chmod(stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass


@dataclass
class RepositoryWorkspace:
    """A ready-to-analyze working tree plus how to dispose of it."""

    info: RepositoryInfo
    root: Path

    def cleanup(self) -> bool:
        """Remove the tree if this pipeline created it. Never touches a
        user-supplied local path.

        Returns True if nothing is left behind. Git marks pack files
        read-only, which makes a plain ``rmtree`` fail on Windows, so a
        failed pass is retried after clearing the read-only bit. The result
        is reported rather than swallowed: a clone that could not be deleted
        is a disk leak the caller should be able to record.
        """
        if not self.info.is_temporary:
            return True
        try:
            shutil.rmtree(self.root)
        except OSError:
            _make_tree_writable(self.root)
            shutil.rmtree(self.root, ignore_errors=True)
        return not self.root.exists()


def looks_like_git_url(source: str) -> bool:
    """True if `source` should be treated as a remote URL rather than a path."""
    candidate = source.strip()
    if candidate.startswith(("http://", "https://", "git://", "ssh://")):
        return True
    # scp-style shorthand, e.g. git@github.com:owner/repo.git
    return bool(re.match(r"^[\w.-]+@[\w.-]+:", candidate))


def validate_git_url(url: str, config: Optional[RepositoryConfig] = None) -> str:
    """Return the URL if it is safe to hand to `git clone`, else raise.

    Accepts standard HTTPS Git URLs with or without a ``.git`` suffix.
    """
    config = config or RepositoryConfig()
    candidate = url.strip()

    if not candidate:
        raise RepositoryError("Repository URL is empty.")
    if candidate.startswith("-"):
        raise RepositoryError(f"Refusing URL that looks like a command-line option: {candidate!r}")
    if any(ch in candidate for ch in "\n\r\0"):
        raise RepositoryError("Repository URL contains control characters.")
    if candidate.startswith("ext::"):
        raise RepositoryError("The git ext:: transport is not allowed (it executes arbitrary commands).")

    parsed = urlparse(candidate)
    if not parsed.scheme:
        raise RepositoryError(
            f"Repository URL {candidate!r} has no scheme. Use an https:// URL, or pass a local path."
        )
    if parsed.scheme not in config.allowed_url_schemes:
        raise RepositoryError(
            f"URL scheme {parsed.scheme!r} is not allowed. Allowed: {sorted(config.allowed_url_schemes)}. "
            "Private repositories and ssh/file transports are not supported."
        )
    if not parsed.netloc:
        raise RepositoryError(f"Repository URL {candidate!r} has no host.")
    return candidate


def repository_name_from_url(url: str) -> str:
    """Derive a human-readable repository name from a URL ('owner/repo.git' -> 'repo')."""
    path = urlparse(url).path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else ""
    if name.endswith(".git"):
        name = name[: -len(".git")]
    return name or "repository"


def safe_clone_dir(url: str, workspace: Path) -> Path:
    """A collision-free clone directory inside `workspace`.

    The directory name is a sanitized repository name plus a short hash of
    the full URL: sanitizing alone could collide (two hosts, same repo
    name), and the hash alone would be unreadable in a log. The result is
    resolved and asserted to be inside the workspace, so no crafted name
    can escape it.
    """
    name = _SAFE_NAME_RE.sub("-", repository_name_from_url(url))
    # Collapse dot runs so no ".." can appear in the directory name. The
    # resolve-and-verify check below is the actual containment guarantee;
    # this keeps the name itself unambiguous too.
    name = re.sub(r"\.{2,}", ".", name).strip("-.") or "repository"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    workspace_root = workspace.resolve()
    target = (workspace_root / f"{name}-{digest}").resolve()
    if workspace_root not in target.parents:
        raise RepositoryError(f"Refusing clone destination outside the workspace: {target}")
    return target


def _run_git(args: list[str], *, cwd: Optional[Path] = None, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a git command from an argument list (never a shell string)."""
    env = {**os.environ, **_GIT_ENV}
    try:
        return subprocess.run(  # noqa: S603 - argument list, no shell, validated inputs
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RepositoryError("git executable not found on PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise RepositoryError(f"git command timed out after {timeout}s: git {' '.join(args)}") from exc


def _directory_size_mb(path: Path) -> float:
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue
    return total / (1024 * 1024)


def read_git_metadata(root: Path) -> dict[str, Optional[str]]:
    """Best-effort branch/commit/remote for an existing tree.

    A local directory may legitimately not be a Git repository at all (the
    Phase 1 ``target-repo/`` is exactly this case once its nested ``.git``
    is removed), so every field is optional and failure is not an error.
    """
    if not (root / ".git").exists():
        return {"branch": None, "commit": None, "remote_url": None}

    def first_line(args: list[str]) -> Optional[str]:
        result = _run_git(args, cwd=root, timeout=15)
        value = result.stdout.strip().splitlines()
        return value[0] if result.returncode == 0 and value else None

    return {
        "branch": first_line(["rev-parse", "--abbrev-ref", "HEAD"]),
        "commit": first_line(["rev-parse", "HEAD"]),
        "remote_url": first_line(["config", "--get", "remote.origin.url"]),
    }


def clone_repository(
    url: str,
    *,
    branch: Optional[str] = None,
    config: Optional[RepositoryConfig] = None,
    force: bool = False,
) -> RepositoryWorkspace:
    """Clone `url` into the configured workspace and return the workspace.

    Uses a shallow, single-branch clone (the pipeline analyzes a working
    tree, not history). If the destination already exists it is reused
    unless `force` is set, so repeated runs against the same repository do
    not re-download it.
    """
    config = config or RepositoryConfig()
    validated = validate_git_url(url, config)
    config.workspace.mkdir(parents=True, exist_ok=True)
    destination = safe_clone_dir(validated, config.workspace)

    if destination.exists() and force:
        shutil.rmtree(destination, ignore_errors=True)

    if not destination.exists():
        args = [
            "-c",
            "protocol.ext.allow=never",
            "-c",
            "protocol.file.allow=never",
            "clone",
            "--single-branch",
        ]
        if config.clone_depth > 0:
            args += ["--depth", str(config.clone_depth)]
        if branch:
            if branch.startswith("-"):
                raise RepositoryError(f"Refusing branch name that looks like an option: {branch!r}")
            args += ["--branch", branch]
        # "--" so a URL can never be parsed as an option.
        args += ["--", validated, str(destination)]

        result = _run_git(args, timeout=config.clone_timeout_seconds)
        if result.returncode != 0:
            shutil.rmtree(destination, ignore_errors=True)
            stderr = (result.stderr or "").strip()
            raise RepositoryError(f"git clone failed for {validated}: {stderr or 'unknown error'}")

    size_mb = _directory_size_mb(destination)
    if size_mb > config.max_repo_size_mb:
        shutil.rmtree(destination, ignore_errors=True)
        raise RepositoryError(
            f"Cloned repository is {size_mb:.0f} MB, above the {config.max_repo_size_mb} MB limit."
        )

    metadata = read_git_metadata(destination)
    info = RepositoryInfo(
        source=url,
        kind="git",
        root=str(destination),
        name=repository_name_from_url(validated),
        branch=branch or metadata["branch"],
        commit=metadata["commit"],
        remote_url=metadata["remote_url"] or validated,
        is_temporary=True,
    )
    return RepositoryWorkspace(info=info, root=destination)


def use_local_repository(path: Path | str) -> RepositoryWorkspace:
    """Wrap an existing local directory as a workspace (never deleted)."""
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise RepositoryError(f"Local repository path does not exist: {root}")
    if not root.is_dir():
        raise RepositoryError(f"Local repository path is not a directory: {root}")

    metadata = read_git_metadata(root)
    info = RepositoryInfo(
        source=str(path),
        kind="local",
        root=str(root),
        name=root.name,
        branch=metadata["branch"],
        commit=metadata["commit"],
        remote_url=metadata["remote_url"],
        is_temporary=False,
    )
    return RepositoryWorkspace(info=info, root=root)


def acquire_repository(
    source: str,
    *,
    branch: Optional[str] = None,
    config: Optional[AnalysisConfig] = None,
    force: bool = False,
) -> RepositoryWorkspace:
    """Resolve `source` -- a Git URL or a local path -- to a workspace.

    Both input kinds are supported deliberately: a Git URL is the product
    requirement, and a local path keeps development and the Phase 1
    ``target-repo/`` workflow working without network access.
    """
    config = config or default_config()
    if looks_like_git_url(source):
        return clone_repository(source, branch=branch, config=config.repository, force=force)
    return use_local_repository(source)


@contextmanager
def repository_workspace(
    source: str,
    *,
    branch: Optional[str] = None,
    config: Optional[AnalysisConfig] = None,
    force: bool = False,
    keep: bool = False,
) -> Iterator[RepositoryWorkspace]:
    """Context manager form: clones are cleaned up on exit, local paths are not.

    Cleanup is skipped when `keep` is set or when the config disables it,
    which is useful when a run's findings need to be inspected against the
    exact tree that produced them.
    """
    config = config or default_config()
    workspace = acquire_repository(source, branch=branch, config=config, force=force)
    try:
        yield workspace
    finally:
        if not keep and config.repository.cleanup_temporary_clones:
            workspace.cleanup()
