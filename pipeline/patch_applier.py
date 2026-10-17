"""
Patch application: write generated patches to a dedicated branch, commit or
revert them, and never touch the analyzed working tree.

Every apply-mode run gets its own ``git worktree`` -- a second checkout of
the repository's HEAD on a new branch ``refactor/<run id>``, placed under
``RefactorConfig.worktree_root``. Patches are written, built and committed
there. Compared with switching branches in place, this means:

* the user's checkout (and any uncommitted work in it) is never modified,
  and the branch they are on never changes under them;
* rolling back is ``git reset --hard`` inside a checkout that contains
  nothing but pipeline changes, so it cannot destroy anything else;
* a running dashboard API is not restarted by files appearing in its
  directory (the worktree lives outside the project by default).

When the run ends the worktree directory is removed. The branch is kept if
anything was committed to it (inspect with ``git diff <base>..<branch>``,
merge when satisfied) and deleted otherwise.

The analyzed tree must be inside a git repository and its tracked files
must match HEAD: the worktree is a checkout of HEAD, so uncommitted edits
would mean the patch is built against different code than was analyzed.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pipeline.patch_generator import Patch, resolve_inside
from pipeline.repository import _make_tree_writable, _run_git

_BRANCH_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
# Used only when the repository has no git identity configured (CI, a fresh
# machine); a configured user.name/user.email always wins.
_FALLBACK_IDENTITY = ("Refactoring Pipeline", "refactoring-pipeline@localhost")


class ApplyError(RuntimeError):
    """The patch could not be applied, committed or reverted."""


def _raw_git(args: list[str], cwd: Path, timeout: int = 120):
    # core.longpaths: deep Java package paths in a worktree can pass Windows'
    # 260-character limit; a no-op elsewhere.
    return _run_git(["-c", "core.longpaths=true", *args], cwd=cwd, timeout=timeout)


def _git(args: list[str], cwd: Path, timeout: int = 120) -> str:
    result = _raw_git(args, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        raise ApplyError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout


def _slug(text: str) -> str:
    return _BRANCH_SAFE_RE.sub("-", text).strip("-.") or "run"


@dataclass
class RefactorWorkspace:
    toplevel: Path  # the original repository (where the branch lives)
    worktree: Path  # the separate checkout
    root: Path  # the analyzed directory, inside the worktree
    branch: str
    base_commit: str
    commits: list[str] = field(default_factory=list)

    @classmethod
    def create(cls, analyzed_root: Path, run_id: str, worktree_root: Path) -> "RefactorWorkspace":
        analyzed_root = Path(analyzed_root).resolve()
        probe = _raw_git(["rev-parse", "--show-toplevel", "--show-prefix"], cwd=analyzed_root, timeout=30)
        if probe.returncode != 0:
            raise ApplyError(
                f"{analyzed_root} is not inside a git repository. Applying refactorings needs git, "
                "so that every change lands on its own branch and can be reverted."
            )
        lines = probe.stdout.splitlines()
        toplevel = Path(lines[0].strip())
        prefix = lines[1].strip() if len(lines) > 1 else ""

        dirty = _git(["status", "--porcelain", "--untracked-files=no", "--", "."], cwd=analyzed_root).splitlines()
        if dirty:
            shown = ", ".join(line[3:] for line in dirty[:5]) + (" ..." if len(dirty) > 5 else "")
            raise ApplyError(
                f"The analyzed tree has uncommitted changes to tracked files ({shown}). "
                "Commit or stash them first: patches are built against the last commit."
            )

        base_commit = _git(["rev-parse", "HEAD"], cwd=toplevel).strip()
        branch = f"refactor/{_slug(run_id)}"
        # A short hash rather than a readable name keeps paths under Windows'
        # limit; the branch name is what identifies the run.
        digest = hashlib.sha256(f"{toplevel}|{run_id}".encode("utf-8")).hexdigest()[:10]
        worktree = Path(worktree_root).resolve() / digest
        if worktree.exists():
            raise ApplyError(f"Worktree directory already exists: {worktree}")
        worktree.parent.mkdir(parents=True, exist_ok=True)
        _git(["worktree", "add", "-b", branch, str(worktree), base_commit], cwd=toplevel, timeout=600)
        return cls(
            toplevel=toplevel,
            worktree=worktree,
            root=(worktree / prefix).resolve() if prefix else worktree,
            branch=branch,
            base_commit=base_commit,
        )

    # -- per-patch operations ------------------------------------------------

    def write(self, patch: Patch) -> None:
        """Write every change, preserving each existing file's line endings."""
        for change in patch.changes:
            target = resolve_inside(self.root, change.path)
            if change.action == "delete":
                target.unlink(missing_ok=True)
                continue
            crlf = target.is_file() and b"\r\n" in target.read_bytes()
            text = change.new or ""
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("utf-8"))

    def commit(self, patch: Patch, message: str) -> str:
        _git(["add", "-A", "--", *patch.paths], cwd=self.root)
        identity: list[str] = []
        if not _raw_git(["config", "user.email"], cwd=self.root).stdout.strip():
            identity = ["-c", f"user.name={_FALLBACK_IDENTITY[0]}", "-c", f"user.email={_FALLBACK_IDENTITY[1]}"]
        _git([*identity, "commit", "-q", "-m", message], cwd=self.root)
        sha = _git(["rev-parse", "HEAD"], cwd=self.root).strip()
        self.commits.append(sha)
        return sha

    def rollback(self, patch: Patch) -> None:
        """Return the worktree to the branch head. Only pipeline changes live
        here, so a hard reset cannot discard anything else; created files are
        untracked and removed separately."""
        _git(["reset", "-q", "--hard", "HEAD"], cwd=self.worktree)
        for change in patch.changes:
            if change.action == "create":
                resolve_inside(self.root, change.path).unlink(missing_ok=True)

    # -- end of run ------------------------------------------------------------

    def close(self) -> dict[str, Any]:
        """Remove the worktree; keep the branch only if it holds commits."""
        summary: dict[str, Any] = {
            "branch": self.branch if self.commits else None,
            "base_commit": self.base_commit,
            "commits": list(self.commits),
            "worktree_removed": True,
        }
        removed = _raw_git(["worktree", "remove", "--force", str(self.worktree)], cwd=self.toplevel, timeout=300)
        if removed.returncode != 0:
            # Windows can refuse while a build daemon still holds a file open.
            _make_tree_writable(self.worktree)
            shutil.rmtree(self.worktree, ignore_errors=True)
            _raw_git(["worktree", "prune"], cwd=self.toplevel)
            summary["worktree_removed"] = not self.worktree.exists()
            if not summary["worktree_removed"]:
                summary["worktree"] = str(self.worktree)
        if not self.commits:
            _raw_git(["branch", "-D", self.branch], cwd=self.toplevel)
        else:
            summary["inspect"] = f"git diff {self.base_commit[:12]}..{self.branch}"
        return summary


def module_dirs(tree: Path, prefixes: set[str]) -> list[Path]:
    """Absolute module directories for the module prefixes a finding may touch."""
    return [tree / p for p in sorted(prefixes) if (tree / p).is_dir()]

