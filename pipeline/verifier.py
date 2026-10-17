"""
Verification gate for applied patches: does it still build, and is the smell gone?

Two independent checks, both recorded:

1. **Build** -- the repository's own build tool, detected from marker files
   (table below), at ``compile`` or ``test`` level. ``verify_command``
   replaces detection entirely for anything the table does not cover.
2. **Re-detection** -- extraction plus the finding's own detector re-run on
   the patched tree. The smell is resolved when a finding with the same key
   no longer appears.

Where the build runs: for each module the finding touches, the build tool
is the one whose marker is *nearest* the module, and the directory is the
*topmost* one (between the repository root and the module) holding that
tool's marker. That builds a multi-module Maven/Gradle project from its
aggregator, so sibling modules resolve, while a repository whose services
each have an independent build gets one build per affected service.
Wrapper scripts (mvnw, gradlew) are preferred over a tool on PATH.

Every build runs twice in effect: once as a **baseline** on the branch head
before the patch is written, and once after. A build that was already
failing cannot tell us anything about the patch, so it is reported as
``skipped`` with that reason -- never as a pass, never as the patch's fault.

SECURITY: this is the one place the pipeline executes code from the
analyzed repository -- build plugins and tests are arbitrary code. It only
runs when the user opts into applying refactorings with a verify level
other than ``none``; do not enable it for repositories you do not trust.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from pipeline.config import AnalysisConfig, RefactorConfig
from pipeline.detectors import run_detectors
from pipeline.extractors import collect_evidence
from pipeline.graph_analysis import build_graph
from pipeline.models import AnalysisContext, Finding, RepositoryInfo

_OUTPUT_TAIL_CHARS = 4000

Commands = tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class BuildSystem:
    name: str
    markers: tuple[str, ...]  # glob patterns matched in one directory
    tool: str  # executable looked up on PATH
    compile: Commands  # one or more argument lists, run in order
    test: Commands
    wrapper: Optional[str] = None  # e.g. "mvnw"; mvnw.cmd/gradlew.bat on Windows


# Order matters only when one directory holds markers of several tools.
BUILD_SYSTEMS: tuple[BuildSystem, ...] = (
    BuildSystem("maven", ("pom.xml",), "mvn", (("-B", "-q", "test-compile"),), (("-B", "-q", "test"),), "mvnw"),
    BuildSystem(
        "gradle",
        ("settings.gradle", "settings.gradle.kts", "build.gradle", "build.gradle.kts"),
        "gradle",
        (("--console=plain", "-q", "testClasses"),),
        (("--console=plain", "-q", "test"),),
        "gradlew",
    ),
    BuildSystem("ant", ("build.xml",), "ant", (("compile",),), (("test",),)),
    BuildSystem("go", ("go.mod",), "go", (("build", "./..."),), (("test", "./..."),)),
    BuildSystem("cargo", ("Cargo.toml",), "cargo", (("build",),), (("test",),)),
    BuildSystem("dotnet", ("*.sln", "*.csproj"), "dotnet", (("build",),), (("test",),)),
    # A fresh worktree has no node_modules, so install comes first.
    BuildSystem(
        "npm",
        ("package.json",),
        "npm",
        (("install", "--no-audit", "--no-fund"), ("run", "build", "--if-present")),
        (("install", "--no-audit", "--no-fund"), ("test",)),
    ),
    # Syntax-level only: the repository's own dependencies are not installed.
    BuildSystem(
        "python", ("pyproject.toml", "setup.py"), "python", (("-m", "compileall", "-q", "."),), (("-m", "pytest", "-q"),)
    ),
)


@dataclass(frozen=True)
class BuildUnit:
    system: BuildSystem
    directory: Path

    @property
    def key(self) -> str:
        return f"{self.system.name}:{self.directory}"


def _has_marker(directory: Path, system: BuildSystem) -> bool:
    return any(any(directory.glob(pattern)) for pattern in system.markers)


def _chain(repo_root: Path, start: Path) -> list[Path]:
    """Directories from `start` up to and including `repo_root`, nearest first."""
    repo_root, start = repo_root.resolve(), start.resolve()
    if start != repo_root and repo_root not in start.parents:
        return [repo_root]
    chain = [start]
    while chain[-1] != repo_root:
        chain.append(chain[-1].parent)
    return chain


def find_build_unit(repo_root: Path, start: Path) -> Optional[BuildUnit]:
    """Nearest marker picks the tool; topmost directory with that tool's marker
    is where it runs (see module docstring)."""
    chain = _chain(repo_root, start)
    for directory in chain:
        system = next((s for s in BUILD_SYSTEMS if _has_marker(directory, s)), None)
        if system:
            topmost = next(d for d in reversed(chain) if _has_marker(d, system))
            return BuildUnit(system, topmost)
    return None


def resolve_executable(unit: BuildUnit, repo_root: Path) -> Optional[list[str]]:
    """Wrapper script nearest the build directory, else the tool on PATH."""
    if unit.system.wrapper:
        names = (
            [f"{unit.system.wrapper}.cmd", f"{unit.system.wrapper}.bat"] if os.name == "nt" else [unit.system.wrapper]
        )
        for directory in _chain(repo_root, unit.directory):
            for name in names:
                script = directory / name
                if script.is_file():
                    if os.name != "nt" and not os.access(script, os.X_OK):
                        return ["sh", str(script)]
                    return [str(script)]
    found = shutil.which(unit.system.tool)
    return [found] if found else None


def _run(command: list[str] | str, cwd: Path, timeout: int) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 - see module SECURITY note
            command,
            cwd=str(cwd),
            shell=isinstance(command, str),  # only the user's own --verify-cmd is a string
            stdout=subprocess.PIPE,
            # Interleaved, so the tail ends with the real error rather than
            # with whatever the tool printed to stderr (JVM warnings, mostly).
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        returncode, output = proc.returncode, proc.stdout or ""
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        returncode, output = None, f"{partial}\ntimed out after {timeout}s"
    except OSError as exc:
        returncode, output = None, f"could not start: {exc}"
    return {
        "command": command if isinstance(command, str) else " ".join(command),
        "cwd": str(cwd),
        "returncode": returncode,
        "passed": returncode == 0,
        "duration_s": round(time.monotonic() - started, 1),
        "output_tail": output[-_OUTPUT_TAIL_CHARS:],
    }


class Verifier:
    """Builds the affected parts of one tree, tracking what the branch head
    looked like before each patch so pre-existing failures are not blamed on it."""

    def __init__(self, tree: Path, config: RefactorConfig):
        self.tree = Path(tree)
        self.config = config
        self._head: dict[str, bool] = {}  # unit key -> did the branch head build?

    def units_for(self, modules: list[Path]) -> list[BuildUnit]:
        if self.config.verify_command:
            return [BuildUnit(BuildSystem("custom", (), "", (), ()), self.tree)]
        units: dict[str, BuildUnit] = {}
        for start in modules or [self.tree]:
            unit = find_build_unit(self.tree, start)
            if unit:
                units.setdefault(unit.key, unit)
        return list(units.values())

    def _build(self, unit: BuildUnit) -> dict[str, Any]:
        timeout = self.config.verify_timeout_seconds
        if self.config.verify_command:
            return {"build_system": "custom", **_run(self.config.verify_command, unit.directory, timeout)}
        exe = resolve_executable(unit, self.tree)
        if exe is None:
            return {
                "build_system": unit.system.name,
                "cwd": str(unit.directory),
                "passed": None,
                "skipped": f"'{unit.system.tool}' not found on PATH and no wrapper script in the repository",
            }
        steps = unit.system.test if self.config.verify_level == "test" else unit.system.compile
        runs = []
        for args in steps:
            runs.append(_run([*exe, *args], unit.directory, timeout))
            if not runs[-1]["passed"]:
                break
        return {"build_system": unit.system.name, **runs[-1], "steps": runs if len(runs) > 1 else None}

    def baseline(self, units: list[BuildUnit]) -> None:
        """Build each unit's current head once, before a patch is written."""
        if self.config.verify_level == "none":
            return
        for unit in units:
            if unit.key not in self._head:
                self._head[unit.key] = bool(self._build(unit).get("passed"))

    def verify(self, units: list[BuildUnit]) -> dict[str, Any]:
        """Build the patched tree. Status is passed / failed / skipped."""
        if self.config.verify_level == "none":
            return {"status": "skipped", "level": "none", "reason": "verify level is 'none'", "runs": []}
        if not units:
            return {
                "status": "skipped",
                "level": self.config.verify_level,
                "reason": "no build system detected (use --verify-cmd to supply one)",
                "runs": [],
            }
        runs = []
        for unit in units:
            if not self._head.get(unit.key, True):
                runs.append(
                    {
                        "build_system": unit.system.name,
                        "cwd": str(unit.directory),
                        "passed": None,
                        "skipped": "this build was already failing before the patch, so it cannot judge it",
                    }
                )
            else:
                runs.append(self._build(unit))
        if any(r["passed"] is False for r in runs):
            status = "failed"
        elif all(r["passed"] is None for r in runs):
            status = "skipped"
        else:
            status = "passed"
        reason = "; ".join(r["skipped"] for r in runs if r.get("skipped")) or None
        return {"status": status, "level": self.config.verify_level, "reason": reason, "runs": runs}

    def record_head(self, units: list[BuildUnit], build: dict[str, Any]) -> None:
        """A patch was committed: its build result is now the head's state."""
        for unit, run in zip(units, build.get("runs") or []):
            if run.get("passed") is not None:
                self._head[unit.key] = bool(run["passed"])


def redetect(
    finding: Finding, tree: Path, repository: RepositoryInfo, config: AnalysisConfig
) -> dict[str, Any]:
    """Re-run extraction and the finding's own detector on the patched tree."""
    cfg = config.with_detectors([finding.smell])
    evidence = collect_evidence(tree, cfg)
    graph = build_graph(evidence.dependencies, exclude_sources=frozenset(cfg.excluded_edge_sources))
    ctx = AnalysisContext(repo_root=Path(tree), repository=repository, evidence=evidence, config=cfg, graph=graph)
    findings, errors, statuses = run_detectors(ctx, cfg)
    still_detected = any(f.key == finding.key for f in findings)
    problems = [e.message for e in (*evidence.errors, *errors)]
    return {
        "resolved": not still_detected and not problems,
        "still_detected": still_detected,
        "remaining_findings": sorted(f.key for f in findings),
        "detector_statuses": statuses,
        "errors": problems,
    }
