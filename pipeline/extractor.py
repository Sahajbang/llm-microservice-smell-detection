"""
Step 2: Dependency Extractor (compatibility facade + CLI).

The implementation now lives in :mod:`pipeline.extractors`, split by
evidence source (``rest``, ``docker``, ``persistence``) so that multiple
detectors can consume normalized evidence. This module stays as the public
entry point the rest of the project already uses -- ``extract_all``,
``discover_services``, ``scan_java_file``, ``Edge``, and the
``python -m pipeline.extractor <repo> -o edges.json`` CLI all behave as
before.

Edge dicts produced here gained two additive fields in the multi-smell
refactor -- ``confidence`` (per-source, see
``pipeline.models.DEPENDENCY_SOURCE_CONFIDENCE``) and ``metadata`` -- next
to the original ``caller``/``callee``/``source``/``file``/``line``/
``evidence`` keys, which are unchanged.

For the detected patterns and their known limitations (regex matching, no
gRPC or message queues), see :mod:`pipeline.extractors.rest` and
:mod:`pipeline.extractors.docker`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pipeline.extractors.docker import extract_docker_compose_edges
from pipeline.extractors.rest import (
    Edge,
    discover_services,
    edges_to_dicts,
    extract_rest_edges,
    scan_java_file,
)

__all__ = [
    "Edge",
    "discover_services",
    "scan_java_file",
    "extract_docker_compose_edges",
    "extract_all",
    "main",
]


def extract_all(repo_root: Path) -> list[dict]:
    """Run every extraction pass and return a deduplicated, sorted edge list."""
    repo_root = Path(repo_root).resolve()
    module_to_service = discover_services(repo_root)
    edges = [
        *extract_rest_edges(repo_root, module_to_service),
        *extract_docker_compose_edges(repo_root),
    ]
    return edges_to_dicts(edges)


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 2: extract service dependency edges from a repo.")
    parser.add_argument("repo_root", type=Path, help="Path to the repository root")
    parser.add_argument("-o", "--output", type=Path, default=None, help="Write JSON here instead of stdout")
    args = parser.parse_args()

    edges = extract_all(args.repo_root)
    out = json.dumps(edges, indent=2)
    if args.output:
        args.output.write_text(out, encoding="utf-8")
        print(f"Wrote {len(edges)} edges to {args.output}")
    else:
        print(out)


if __name__ == "__main__":
    main()
