"""
Pipeline configuration.

Every threshold and behavioural switch the detectors and orchestrator use
lives here rather than as a literal inside a detector, so a thesis run can
state exactly which parameters produced a result, and so sensitivity to
those parameters can be studied by changing one object.

Defaults are documented with their rationale in ``AnalysisConfig`` below.
They are deliberately conservative: a detector that fires on everything is
useless as evidence, and an arbitrary magic number is not defensible in a
thesis.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Dependency-edge sources that describe container startup ordering rather
# than a request-time call. Excluded from the service dependency graph used
# for cycle and hub detection (they remain in the evidence for context).
DEFAULT_EXCLUDED_EDGE_SOURCES = frozenset({"docker-compose"})

# Every detector key the pipeline knows about, in execution order.
ALL_DETECTORS = ("cyclic_dependency", "hub_dependency", "shared_persistence")


@dataclass(frozen=True)
class HubConfig:
    """Thresholds for Hub-like Dependency.

    A hub is a service an unusually large share of the system depends on
    (or that depends on an unusually large share of it). "Unusually large"
    cannot be a fixed number: degree 4 is unremarkable in a 40-service
    system and dominant in a 5-service one. So a candidate must clear an
    absolute floor AND at least one of two size-relative criteria:

    * ``min_degree`` -- absolute floor. Below this a service is not a hub
      in any meaningful sense, whatever the distribution says. Guards
      against tiny graphs where the standard deviation is meaningless.
    * ``stdev_multiplier`` -- statistical outlier test: degree >= mean +
      k * stdev of the graph's degree distribution. Adapts to the system's
      own coupling baseline.
    * ``degree_ratio`` -- share test: degree >= ratio * (number_of_services
      - 1), i.e. directly connected to at least this fraction of all other
      services. Catches hubs in flat distributions where no node is a
      statistical outlier because several are hubs.
    """

    min_degree: int = 4
    stdev_multiplier: float = 1.5
    degree_ratio: float = 0.5
    # Which degree to test. "total" counts both directions; "in" alone
    # models the classic "everyone calls this service" hub.
    degree_mode: str = "total"


@dataclass(frozen=True)
class SharedPersistenceConfig:
    """Thresholds and guards for Shared Persistence.

    ``in_memory_drivers`` are databases whose instances are private to the
    owning JVM process. Two services both using ``jdbc:hsqldb:mem:petclinic``
    do NOT share storage, so matching on that identity would be a
    guaranteed false positive; those identities are never reported.

    ``report_table_overlap_without_datasource`` controls the weaker,
    second-order signal: two services declaring the same table name when
    neither's datasource could be resolved statically. That is suggestive
    but unproven, so it is reported at low confidence and can be disabled.
    """

    in_memory_drivers: frozenset[str] = frozenset({"hsqldb", "h2", "derby", "sqlite"})
    min_services_sharing: int = 2
    report_table_overlap_without_datasource: bool = True
    # Confidence assigned to each rule; see pipeline/detectors/shared_persistence.py
    confidence_resolved_datasource: float = 0.9
    confidence_unresolved_datasource: float = 0.6
    confidence_table_overlap_only: float = 0.45


@dataclass(frozen=True)
class RepositoryConfig:
    """Repository acquisition limits and safety settings."""

    workspace: Path = PROJECT_ROOT / "workspace"
    clone_timeout_seconds: int = 300
    # Shallow clone by default: the pipeline analyzes a working tree, not
    # history, and a full clone of a large repository is wasted time/disk.
    clone_depth: int = 1
    max_repo_size_mb: int = 1024
    allowed_url_schemes: frozenset[str] = frozenset({"https", "http"})
    # Temporary clones are removed after a run unless the caller keeps them.
    cleanup_temporary_clones: bool = True


@dataclass(frozen=True)
class LLMConfig:
    """LLM behaviour. Provider/model/key themselves stay in
    pipeline.llm_client (and the environment) -- only per-run behaviour is
    configured here."""

    enabled: bool = True
    max_json_retries: int = 3
    temperature: float = 0.2
    # Deterministic findings below this confidence are not sent to the LLM
    # at all. 0.0 means "send everything"; raise it to spend fewer tokens
    # on weak candidates.
    min_finding_confidence: float = 0.0
    # Propose a refactoring only when the LLM both confirmed the smell and
    # recommended refactoring (Phase 1 behaviour).
    refactor_only_when_recommended: bool = True


@dataclass(frozen=True)
class AnalysisConfig:
    """Top-level configuration for one pipeline run."""

    enabled_detectors: tuple[str, ...] = ALL_DETECTORS
    excluded_edge_sources: frozenset[str] = DEFAULT_EXCLUDED_EDGE_SOURCES
    hub: HubConfig = field(default_factory=HubConfig)
    shared_persistence: SharedPersistenceConfig = field(default_factory=SharedPersistenceConfig)
    repository: RepositoryConfig = field(default_factory=RepositoryConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)

    def with_detectors(self, detectors: Optional[list[str]]) -> "AnalysisConfig":
        if not detectors:
            return self
        unknown = [d for d in detectors if d not in ALL_DETECTORS]
        if unknown:
            raise ValueError(f"Unknown detector(s): {unknown}. Known: {list(ALL_DETECTORS)}")
        return replace(self, enabled_detectors=tuple(detectors))

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe view, recorded with every run so results are reproducible."""

        def convert(value: Any) -> Any:
            if isinstance(value, frozenset):
                return sorted(value)
            if isinstance(value, Path):
                return str(value)
            if isinstance(value, tuple):
                return list(value)
            if isinstance(value, dict):
                return {k: convert(v) for k, v in value.items()}
            return value

        return {k: convert(v) for k, v in asdict(self).items()}


def default_config() -> AnalysisConfig:
    """Config with defaults, allowing a few environment overrides.

    Only settings a user plausibly changes per machine are read from the
    environment (workspace location, LLM on/off). Thresholds are not
    environment-driven: a thesis run should state them explicitly.
    """
    repo_cfg = RepositoryConfig()
    workspace = os.environ.get("PIPELINE_WORKSPACE")
    if workspace:
        repo_cfg = replace(repo_cfg, workspace=Path(workspace))

    llm_cfg = LLMConfig()
    if os.environ.get("PIPELINE_DISABLE_LLM", "").strip().lower() in {"1", "true", "yes"}:
        llm_cfg = replace(llm_cfg, enabled=False)

    return AnalysisConfig(repository=repo_cfg, llm=llm_cfg)
