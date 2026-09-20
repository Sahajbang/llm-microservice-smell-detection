"""
Shared data contract for the multi-smell analysis pipeline.

Everything that crosses a module boundary -- extracted evidence, the
analysis context detectors receive, the findings they return, the errors
each stage records, and the final per-run result -- is defined here, so
there is exactly one place to read to understand what flows through the
pipeline.

Two deliberate compatibility constraints shape these types:

1. Dependency evidence is carried as plain dicts with the original Phase 1
   keys (``caller``, ``callee``, ``source``, ``file``, ``line``,
   ``evidence``). ``pipeline.graph_analysis.build_graph``, ``api/main.py``
   and the frontend all read that exact shape, so new fields
   (``confidence``, ``metadata``) are added alongside rather than
   replacing it.
2. Findings are serialized to JSON for run logs, so every field here is
   JSON-representable.
"""

from __future__ import annotations

import dataclasses
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

# Stages an error can come from. Kept distinct (rather than one generic
# "error") so a run log can answer "did extraction fail, or did the model
# fail?" without parsing message text.
STAGE_EXTRACTION = "extraction"
STAGE_DETECTION = "detection"
STAGE_LLM_DETECTION = "llm_detection"
STAGE_LLM_REFACTORING = "llm_refactoring"
STAGE_REPOSITORY = "repository"


@dataclass
class PipelineError:
    """One recorded, non-fatal failure. Errors are collected rather than
    raised so that one failing extractor or detector cannot take down the
    whole run (see pipeline.extractors / pipeline.detectors)."""

    stage: str
    component: str
    message: str
    detail: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

# Per-source confidence for a dependency edge, used to weight findings.
# Rationale: a declarative @FeignClient naming a service is near-certain; a
# URL literal whose client type could not be identified is weaker; a
# docker-compose depends_on entry is startup ordering and may not
# correspond to any call at all.
DEPENDENCY_SOURCE_CONFIDENCE: dict[str, float] = {
    "feign": 0.95,
    "resttemplate": 0.9,
    "webclient": 0.9,
    "discovery-client": 0.85,
    "http-literal": 0.6,
    "docker-compose": 0.4,
}
DEFAULT_DEPENDENCY_CONFIDENCE = 0.5


@dataclass(frozen=True)
class PersistenceEvidence:
    """One statically observed fact about a service's data layer.

    ``kind`` is one of:
      - ``datasource``   a JDBC/R2DBC URL or datasource config entry
      - ``entity``       a JPA ``@Entity`` / ``@Table`` declaration
      - ``table``        a ``CREATE TABLE`` in a schema or migration file
    ``value`` is the normalized identity used for comparison across
    services (a database identity for ``datasource``, a lowercased table
    name for ``entity``/``table``).
    """

    service: str
    kind: str
    value: str
    file: str
    line: int
    evidence: str
    confidence: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceBundle:
    """Everything the extraction layer produced for one repository.

    ``dependencies`` keeps the Phase 1 dict shape verbatim (see module
    docstring). ``services`` is the union of every service name any
    extractor saw, so detectors do not each re-derive it.
    """

    dependencies: list[dict] = field(default_factory=list)
    persistence: list[PersistenceEvidence] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    modules: dict[str, str] = field(default_factory=dict)  # module dir -> service name
    errors: list[PipelineError] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        by_source: dict[str, int] = {}
        for edge in self.dependencies:
            by_source[edge["source"]] = by_source.get(edge["source"], 0) + 1
        by_kind: dict[str, int] = {}
        for record in self.persistence:
            by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
        return {
            "services": len(self.services),
            "dependency_edges": len(self.dependencies),
            "dependency_edges_by_source": by_source,
            "persistence_records": len(self.persistence),
            "persistence_records_by_kind": by_kind,
            "extraction_errors": len(self.errors),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "dependencies": self.dependencies,
            "persistence": [p.to_dict() for p in self.persistence],
            "services": self.services,
            "modules": self.modules,
            "errors": [e.to_dict() for e in self.errors],
        }


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


@dataclass
class RepositoryInfo:
    """Provenance for the analyzed working tree, recorded with every run so
    a result can be traced back to exact source."""

    source: str  # the URL or path the user supplied
    kind: str  # "git" | "local"
    root: str  # absolute path actually analyzed
    name: str
    branch: Optional[str] = None
    commit: Optional[str] = None
    remote_url: Optional[str] = None
    is_temporary: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Analysis context (detector input)
# ---------------------------------------------------------------------------


@dataclass
class AnalysisContext:
    """What every detector receives. Deliberately contains no knowledge of
    how the repository was obtained -- a detector works the same whether
    the tree came from a clone or a local path."""

    repo_root: Path
    repository: RepositoryInfo
    evidence: EvidenceBundle
    config: Any  # pipeline.config.AnalysisConfig (untyped here to avoid a cycle)
    graph: Any = None  # networkx.DiGraph over non-excluded dependency edges

    def module_for_service(self, service: str) -> Optional[str]:
        for module_dir, name in self.evidence.modules.items():
            if name == service:
                return module_dir
        return None


# ---------------------------------------------------------------------------
# Findings (detector output)
# ---------------------------------------------------------------------------

SEVERITIES = ("LOW", "MEDIUM", "HIGH")


@dataclass
class Finding:
    """One deterministic smell candidate produced by a detector.

    This is the unit handed to the LLM layer, so it must carry enough
    context for a model to judge it without re-scanning the repository:
    which services, which files, the concrete evidence, and why the
    deterministic detector flagged it (``description`` + ``metrics``).

    ``severity`` and ``confidence`` here are the *deterministic* prior from
    static analysis. The LLM's own severity/confidence is recorded
    separately on the result, never overwriting these.
    """

    smell: str  # smell key, e.g. "cyclic_dependency"
    smell_name: str  # human-readable, e.g. "Cyclic Dependency"
    key: str  # stable identity for this finding within a run
    services: list[str]
    files: list[str]
    severity: str
    confidence: float
    description: str
    evidence: list[dict] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Run result
# ---------------------------------------------------------------------------


@dataclass
class FindingResult:
    """A finding plus whatever the LLM layer concluded about it."""

    finding: Finding
    llm_detection: Optional[dict[str, Any]] = None
    refactoring: Optional[dict[str, Any]] = None
    errors: list[PipelineError] = field(default_factory=list)

    @property
    def confirmed(self) -> bool:
        return bool(self.llm_detection and self.llm_detection.get("detected"))

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding": self.finding.to_dict(),
            "llm_detection": self.llm_detection,
            "refactoring": self.refactoring,
            "errors": [e.to_dict() for e in self.errors],
        }


@dataclass
class AnalysisResult:
    """The complete outcome of one pipeline run, across all smells.

    Deliberately not shaped around a single cycle: ``results`` holds one
    entry per finding, of any smell type, in one run.
    """

    run_id: str
    started_at: str
    finished_at: Optional[str] = None
    repository: Optional[RepositoryInfo] = None
    detectors_run: list[str] = field(default_factory=list)
    extraction_summary: dict[str, Any] = field(default_factory=dict)
    graph_summary: dict[str, Any] = field(default_factory=dict)
    results: list[FindingResult] = field(default_factory=list)
    errors: list[PipelineError] = field(default_factory=list)
    llm_enabled: bool = True

    def counts(self) -> dict[str, Any]:
        by_smell: dict[str, int] = {}
        confirmed = 0
        refactorings = 0
        for r in self.results:
            by_smell[r.finding.smell] = by_smell.get(r.finding.smell, 0) + 1
            if r.confirmed:
                confirmed += 1
            if r.refactoring:
                refactorings += 1
        return {
            "findings": len(self.results),
            "findings_by_smell": by_smell,
            "confirmed_by_llm": confirmed,
            "refactoring_proposals": refactorings,
            "errors": len(self.errors) + sum(len(r.errors) for r in self.results),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "repository": self.repository.to_dict() if self.repository else None,
            "detectors_run": self.detectors_run,
            "llm_enabled": self.llm_enabled,
            "extraction_summary": self.extraction_summary,
            "graph_summary": self.graph_summary,
            "counts": self.counts(),
            "results": [r.to_dict() for r in self.results],
            "errors": [e.to_dict() for e in self.errors],
        }


def json_default(obj: Any) -> Any:
    """Fallback serializer for dataclasses/Paths that slip into a log."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    return str(obj)
