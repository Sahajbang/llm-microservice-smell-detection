"""
Docker Compose dependency evidence collector.

``depends_on`` entries are a secondary, lower-confidence edge source: they
encode container startup ordering, not necessarily a request-time call
between services. They are kept in the evidence for context (and shown in
the dashboard) but excluded by default from the graph used for cycle and
hub detection -- see ``AnalysisConfig.excluded_edge_sources``.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from pipeline.extractors.rest import Edge, make_edge

COMPOSE_FILENAMES = ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml")


def find_compose_files(repo_root: Path) -> list[Path]:
    """Compose files at the repository root, in preference order."""
    return [repo_root / name for name in COMPOSE_FILENAMES if (repo_root / name).exists()]


def extract_docker_compose_edges(repo_root: Path) -> list[Edge]:
    """One edge per ``depends_on`` entry across the repository's compose files."""
    repo_root = Path(repo_root)
    edges: list[Edge] = []

    for compose_path in find_compose_files(repo_root):
        with compose_path.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            continue
        rel_path = compose_path.relative_to(repo_root).as_posix()
        for caller, svc_def in (data.get("services") or {}).items():
            if not isinstance(svc_def, dict):
                continue
            depends_on = svc_def.get("depends_on")
            if not depends_on:
                continue
            callees = depends_on.keys() if isinstance(depends_on, dict) else depends_on
            for callee in callees:
                edges.append(
                    make_edge(
                        caller,
                        callee,
                        "docker-compose",
                        rel_path,
                        0,
                        f"{caller} depends_on {callee}",
                        compose_file=rel_path,
                    )
                )
    return edges
