"""
Evidence extraction layer.

Each collector in this package answers one question about a repository
(who calls whom over REST, what depends on what at startup, what talks to
which database) and returns normalized evidence. Detectors consume that
evidence; they never re-scan the repository themselves, and they never
need to know how the repository was obtained.

``collect_evidence`` runs every collector with error isolation: a
collector that raises records a ``PipelineError`` and the run continues
with whatever the others produced, so (for example) a malformed
``docker-compose.yml`` cannot prevent Cyclic Dependency detection from
running. Errors are recorded, never swallowed silently.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from pipeline.config import AnalysisConfig, default_config
from pipeline.extractors.docker import extract_docker_compose_edges
from pipeline.extractors.persistence import extract_persistence_evidence
from pipeline.extractors.rest import discover_services, edges_to_dicts, extract_rest_edges
from pipeline.models import STAGE_EXTRACTION, EvidenceBundle, PipelineError

__all__ = [
    "collect_evidence",
    "discover_services",
    "extract_docker_compose_edges",
    "extract_persistence_evidence",
    "extract_rest_edges",
    "edges_to_dicts",
]


def _guard(
    bundle: EvidenceBundle, component: str, fn: Callable[[], None]
) -> None:
    """Run one collector, recording (not raising) any failure."""
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - deliberate isolation boundary
        bundle.errors.append(
            PipelineError(
                stage=STAGE_EXTRACTION,
                component=component,
                message=f"{type(exc).__name__}: {exc}",
            )
        )


def collect_evidence(repo_root: Path | str, config: Optional[AnalysisConfig] = None) -> EvidenceBundle:
    """Run every evidence collector against `repo_root`.

    Returns a bundle even when individual collectors fail; check
    ``bundle.errors`` to see what did not run.
    """
    config = config or default_config()
    repo_root = Path(repo_root).resolve()
    bundle = EvidenceBundle()

    modules: dict[str, str] = {}

    def _discover() -> None:
        nonlocal modules
        modules = discover_services(repo_root)
        bundle.modules = modules

    _guard(bundle, "service_discovery", _discover)

    rest_edges: list = []

    def _rest() -> None:
        nonlocal rest_edges
        rest_edges = extract_rest_edges(repo_root, modules)

    _guard(bundle, "rest", _rest)

    compose_edges: list = []

    def _docker() -> None:
        nonlocal compose_edges
        compose_edges = extract_docker_compose_edges(repo_root)

    _guard(bundle, "docker_compose", _docker)

    bundle.dependencies = edges_to_dicts([*rest_edges, *compose_edges])

    def _persistence() -> None:
        bundle.persistence = extract_persistence_evidence(
            repo_root, modules, config.shared_persistence
        )

    _guard(bundle, "persistence", _persistence)

    # Services are the union of everything any collector saw: module names
    # from service discovery, plus endpoints only compose or persistence
    # config knows about (e.g. a database container).
    services = set(modules.values())
    for edge in bundle.dependencies:
        services.add(edge["caller"])
        services.add(edge["callee"])
    for record in bundle.persistence:
        services.add(record.service)
    bundle.services = sorted(services)

    return bundle
