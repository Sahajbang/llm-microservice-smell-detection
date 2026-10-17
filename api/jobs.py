"""
In-process analysis job runner for the dashboard.

The dashboard needs to *start* a pipeline run and watch it progress, which
the read-only endpoints in api/main.py cannot do. A run takes anywhere from
a second (``--no-llm``) to a few minutes (LLM validation + refactoring per
finding), so it is executed on a background thread and polled.

Progress is not simulated: :class:`ProgressLogger` subclasses the pipeline's
own :class:`~pipeline.run_logger.RunLogger`, so every event the UI shows
corresponds to a stage the orchestrator actually finished and wrote to disk.

In ``ask`` refactoring mode the pipeline thread blocks in :meth:`Job.ask`
until the browser answers through ``POST /api/jobs/{id}/decision`` (or the
approval timeout passes, which takes the safe choice). The pending question
is part of the polled job payload, so no extra endpoint is needed to see it.

ponytail: jobs live in a module-level dict, so they are lost on restart and
do not survive more than one uvicorn worker. That is the right size for a
single-user research dashboard; move to a real queue only if runs ever need
to outlive the server process.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pipeline.config import (
    ALL_DETECTORS,
    APPLY_MODES,
    FAILURE_POLICIES,
    VERIFY_LEVELS,
    AnalysisConfig,
    default_config,
)
from pipeline.repository import looks_like_git_url, validate_git_url
from pipeline.run_logger import RunLogger

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# The orchestrator's stage sequence, as the UI presents it. Each phase is a
# station on the pipeline; `stage_phase` maps a logged stage file name onto
# one of these.
PHASES: tuple[tuple[str, str], ...] = (
    ("acquire", "Acquire repository"),
    ("extract", "Extract evidence"),
    ("detect", "Detect smells"),
    ("validate", "LLM validation"),
    ("refactor", "LLM refactoring"),
    ("apply", "Apply & verify"),
    ("complete", "Complete"),
)
PHASE_ORDER = {key: i for i, (key, _) in enumerate(PHASES)}

DECISION_OPTIONS = {"apply": ("approve", "reject", "approve_all"), "failure": ("rollback", "keep")}


class JobError(ValueError):
    """Invalid job request (bad source, unknown smell, bad decision)."""


class StaleDecision(LookupError):
    """The decision being answered is no longer pending (answered or timed out)."""


def resolve_source(source: str) -> str:
    """Validate a user-supplied repository source before it reaches the pipeline.

    This is the trust boundary the CLI does not have: the CLI's argument
    comes from whoever runs the process, an HTTP body comes from a browser.
    Git URLs are handed to the existing strict validator; local paths are
    confined to the project directory so the endpoint cannot be used to read
    arbitrary directories on the machine.
    """
    candidate = (source or "").strip()
    if not candidate:
        raise JobError("A repository URL or local path is required.")

    if looks_like_git_url(candidate):
        try:
            return validate_git_url(candidate)
        except Exception as exc:  # repository.RepositoryError and friends
            raise JobError(str(exc)) from exc

    path = Path(candidate)
    resolved = (path if path.is_absolute() else PROJECT_ROOT / path).resolve()
    if resolved != PROJECT_ROOT and PROJECT_ROOT not in resolved.parents:
        raise JobError("Local paths must be inside the project directory.")
    if not resolved.is_dir():
        raise JobError(f"No such directory: {candidate}")
    return str(resolved)


def resolve_smells(smells: Optional[list[str]]) -> Optional[list[str]]:
    if not smells:
        return None
    unknown = [s for s in smells if s not in ALL_DETECTORS]
    if unknown:
        raise JobError(f"Unknown smell(s): {', '.join(unknown)}")
    return list(smells)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Job:
    id: str
    source: str
    smells: list[str]
    llm: bool
    branch: Optional[str] = None
    apply_mode: str = "never"
    on_failure: str = "rollback"
    verify_level: str = "compile"
    status: str = "running"  # running | done | failed
    started_at: str = field(default_factory=_now)
    finished_at: Optional[str] = None
    run_id: Optional[str] = None
    phase: str = "acquire"
    events: list[dict[str, Any]] = field(default_factory=list)
    counts: Optional[dict[str, Any]] = None
    refactor: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    pending: Optional[dict[str, Any]] = None  # the question the run is blocked on
    _answer: Optional[str] = field(default=None, repr=False)
    _decided: threading.Event = field(default_factory=threading.Event, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(self, phase: str, label: str, detail: Optional[str] = None) -> None:
        with self._lock:
            if PHASE_ORDER.get(phase, -1) >= PHASE_ORDER.get(self.phase, -1):
                self.phase = phase
            self.events.append({"at": _now(), "phase": phase, "label": label, "detail": detail})

    def ask(self, kind: str, request: dict[str, Any], timeout: float) -> str:
        """Block the pipeline thread until the browser answers (see module docstring).

        No answer within `timeout` takes the safe default: reject the patch,
        or the configured failure policy.
        """
        default = "reject" if kind == "apply" else str(request.get("default") or "rollback")
        with self._lock:
            self.pending = {
                "id": uuid.uuid4().hex[:10],
                "kind": kind,
                "options": list(DECISION_OPTIONS[kind]),
                "default": default,
                **request,
            }
            self._answer = None
            self._decided.clear()
        what = "approve this patch" if kind == "apply" else "decide what to do with a failed patch"
        self.record("apply", f"Waiting for you to {what}", request.get("finding_key"))

        answered = self._decided.wait(timeout)
        with self._lock:
            answer = self._answer if answered and self._answer else default
            self.pending = None
        self.record("apply", f"Decision: {answer}" + ("" if answered else " (no answer, timed out)"), request.get("finding_key"))
        return answer

    def answer(self, decision_id: str, answer: str) -> None:
        with self._lock:
            if not self.pending or self.pending["id"] != decision_id:
                raise StaleDecision("That decision is no longer pending -- it was answered or timed out.")
            if answer not in self.pending["options"]:
                raise JobError(f"Answer must be one of {self.pending['options']}.")
            self._answer = answer
            self.pending = None  # so the response to this very request already shows it answered
        self._decided.set()

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "source": self.source,
                "smells": self.smells,
                "llm": self.llm,
                "branch": self.branch,
                "applyMode": self.apply_mode,
                "onFailure": self.on_failure,
                "verifyLevel": self.verify_level,
                "status": self.status,
                "startedAt": self.started_at,
                "finishedAt": self.finished_at,
                "runId": self.run_id,
                "phase": self.phase,
                "events": list(self.events),
                "counts": self.counts,
                "refactor": self.refactor,
                "pendingDecision": dict(self.pending) if self.pending else None,
                "error": self.error,
            }


class ProgressLogger(RunLogger):
    """RunLogger that also reports each written stage to a Job.

    Subclassing keeps the orchestrator untouched: it logs stages exactly as
    before and progress falls out of the logging it already does.
    """

    def __init__(self, job: Job, run_name: str, base_dir: Optional[Path] = None):
        super().__init__(run_name, **({"base_dir": base_dir} if base_dir else {}))
        self.job = job
        job.run_id = self.dir.name

    def log_stage(self, stage_name: str, data: Any) -> Path:
        path = super().log_stage(stage_name, data)
        try:
            phase, label, detail = describe_stage(stage_name, data)
            self.job.record(phase, label, detail)
        except Exception as exc:  # noqa: BLE001 - progress must never break a run
            self.job.record(self.job.phase, f"Stage {stage_name} logged", str(exc))
        return path


def describe_stage(stage_name: str, data: Any) -> tuple[str, str, Optional[str]]:
    """Turn a logged stage into the phase + human label the UI shows.

    Labels quote real numbers out of the stage payload rather than generic
    text, so the progress screen doubles as a summary of what was found.
    """
    d = data if isinstance(data, dict) else {}

    if stage_name.startswith("00_run_metadata"):
        repo = d.get("repository") or {}
        name = repo.get("name", "repository")
        ref = repo.get("commit") or repo.get("branch")
        return "acquire", f"Working tree ready: {name}", (ref[:12] if isinstance(ref, str) else None)

    if stage_name.startswith("01_evidence"):
        deps = len(d.get("dependencies") or [])
        pers = len(d.get("persistence") or [])
        services = len(d.get("services") or [])
        return (
            "extract",
            f"{deps} dependency edges, {pers} persistence records",
            f"across {services} services",
        )

    if stage_name.startswith("02_detection"):
        findings = d.get("findings") or []
        ran = [s.get("detector") for s in (d.get("detector_statuses") or []) if s.get("status") == "ok"]
        return (
            "detect",
            f"{len(findings)} candidate finding(s)",
            f"{len(ran)} detector(s) ran: {', '.join(str(r) for r in ran)}" if ran else None,
        )

    if stage_name.startswith("finding_"):
        f = d.get("finding") or {}
        services = ", ".join(f.get("services") or [])
        return "detect", f"Candidate: {f.get('smell_name', 'finding')}", f"{services} ({f.get('severity', '?')})"

    if stage_name.startswith("detection_"):
        result = d.get("result") or {}
        verdict = "confirmed" if result.get("detected") else "dismissed"
        conf = result.get("confidence")
        detail = f"{result.get('severity', '?')} - confidence {conf}" if result.get("detected") else None
        return "validate", f"LLM {verdict}: {result.get('smell', 'finding')}", detail

    if stage_name.startswith("refactoring_"):
        result = d.get("result") or {}
        files = result.get("affected_files") or []
        return "refactor", f"Refactoring plan: {len(files)} file(s)", ", ".join(files[:4]) or None

    if stage_name.startswith("patch_"):
        files = d.get("files") or []
        if not files:
            return "apply", "Patch generation failed", d.get("finding_key")
        return "apply", f"Patch generated: {len(files)} file(s)", ", ".join(f.get("path", "") for f in files[:4])

    if stage_name.startswith("apply_"):
        status = d.get("status", "?")
        label = {
            "proposed": "Patch previewed (dry run)",
            "rejected": "Patch rejected",
            "verified": "Patch verified and committed",
            "rolled_back": "Patch failed verification, rolled back",
            "failed_kept": "Patch failed verification, kept on branch",
            "error": "Patch could not be applied",
        }.get(status, f"Patch {status}")
        verification = d.get("verification") or {}
        detail = None
        if verification:
            build = (verification.get("build") or {}).get("status")
            resolved = (verification.get("redetection") or {}).get("resolved")
            detail = f"build {build}, smell {'resolved' if resolved else 'still present'}"
        return "apply", label, detail or d.get("error") or d.get("finding_key")

    if stage_name.startswith("03_apply_summary"):
        if d.get("error"):
            return "apply", "Refactoring not applied", d["error"]
        if d.get("branch"):
            return "apply", f"Refactor branch kept: {d['branch']}", f"{len(d.get('commits') or [])} commit(s)"
        return "apply", "No patch committed", None

    if stage_name.startswith("99_result"):
        counts = d.get("counts") or {}
        return (
            "complete",
            f"{counts.get('findings', 0)} finding(s), {counts.get('confirmed_by_llm', 0)} confirmed",
            f"{counts.get('refactoring_proposals', 0)} refactoring proposal(s)",
        )

    return "detect", f"Stage {stage_name}", None


# --- registry ---------------------------------------------------------------

_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()
_active: Optional[str] = None


def get_job(job_id: str) -> Optional[Job]:
    with _jobs_lock:
        return _jobs.get(job_id)


def active_job() -> Optional[Job]:
    with _jobs_lock:
        return _jobs.get(_active) if _active else None


def recent_jobs(limit: int = 20) -> list[Job]:
    with _jobs_lock:
        return sorted(_jobs.values(), key=lambda j: j.started_at, reverse=True)[:limit]


def start_job(
    source: str,
    *,
    smells: Optional[list[str]] = None,
    llm: bool = True,
    branch: Optional[str] = None,
    run_name: str = "dashboard",
    log_dir: Optional[Path] = None,
    apply_mode: str = "never",
    on_failure: str = "rollback",
    verify_level: str = "compile",
) -> Job:
    """Validate the request, then run the pipeline on a background thread.

    Only one analysis runs at a time: the pipeline is CPU- and API-bound and
    a second concurrent run would mostly buy rate-limit errors.

    There is deliberately no way to pass a custom verify command here: it
    is a shell command, and an HTTP body must not be able to run one.
    """
    global _active

    resolved = resolve_source(source)
    selected = resolve_smells(smells)
    for value, allowed, name in (
        (apply_mode, APPLY_MODES, "applyMode"),
        (on_failure, FAILURE_POLICIES, "onFailure"),
        (verify_level, VERIFY_LEVELS, "verifyLevel"),
    ):
        if value not in allowed:
            raise JobError(f"{name} must be one of {list(allowed)}.")
    if apply_mode != "never" and not llm:
        raise JobError("Applying refactorings needs LLM validation: patches come from LLM refactoring plans.")
    config = _build_config(selected, llm)
    config = replace(
        config,
        refactor=replace(config.refactor, mode=apply_mode, on_failure=on_failure, verify_level=verify_level),
    )

    with _jobs_lock:
        current = _jobs.get(_active) if _active else None
        if current and current.status == "running":
            raise RuntimeError(f"An analysis is already running (job {current.id}).")
        job = Job(
            id=uuid.uuid4().hex[:12],
            source=source.strip(),
            smells=selected or list(ALL_DETECTORS),
            llm=llm,
            branch=branch or None,
            apply_mode=apply_mode,
            on_failure=on_failure,
            verify_level=verify_level,
        )
        _jobs[job.id] = job
        _active = job.id

    thread = threading.Thread(
        target=_run,
        args=(job, resolved, branch, config, run_name, log_dir),
        daemon=True,
        name=f"analysis-{job.id}",
    )
    thread.start()
    return job


def _build_config(smells: Optional[list[str]], llm: bool) -> AnalysisConfig:
    config = default_config().with_detectors(smells)
    if not llm:
        config = replace(config, llm=replace(config.llm, enabled=False))
    return config


def _run(
    job: Job,
    source: str,
    branch: Optional[str],
    config: AnalysisConfig,
    run_name: str,
    log_dir: Optional[Path] = None,
) -> None:
    # Imported here so an import-time failure in the pipeline surfaces as a
    # failed job rather than preventing the API from starting.
    from pipeline.run_pipeline import analyze_repository

    try:
        logger = ProgressLogger(job, run_name, base_dir=log_dir)
        job.record("acquire", f"Analyzing {job.source}", ", ".join(job.smells))
        timeout = config.refactor.approval_timeout_seconds
        result = analyze_repository(
            source,
            branch=branch,
            config=config,
            logger=logger,
            run_name=run_name,
            approver=(lambda kind, request: job.ask(kind, request, timeout)) if job.apply_mode == "ask" else None,
        )
        job.counts = result.counts()
        job.refactor = result.refactor
        job.run_id = result.run_id
        job.status = "failed" if (result.errors and not result.results) else "done"
        if job.status == "failed":
            job.error = "; ".join(f"[{e.stage}] {e.message}" for e in result.errors[:3])
        job.record("complete", "Run finished", job.error)
    except Exception as exc:  # noqa: BLE001 - a job must never kill the server
        job.status = "failed"
        job.error = f"{type(exc).__name__}: {exc}"
        job.record("complete", "Run failed", job.error)
    finally:
        job.finished_at = _now()
