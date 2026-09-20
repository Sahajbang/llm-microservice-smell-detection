"""
Step 3: Graph Construction & Cyclic Dependency Detection.

Loads the edge list produced by Step 2 (pipeline.extractor) into a
NetworkX DiGraph and runs `networkx.simple_cycles` to find Cyclic
Dependencies -- the core, deterministic static-analysis signal that grounds
the LLM's reasoning in Step 4.

`docker-compose.yml` `depends_on` edges are excluded from cycle detection by
default: they encode container startup ordering, not the request-flow
coupling that "Cyclic Dependency" as an architectural smell is about. They
remain available in the full edge list for context. Pass
`exclude_sources=set()` to include them.

Cycles are canonicalized before being returned. `networkx.simple_cycles`
reports a cycle starting at whichever node its traversal reached first, so
the same cycle could be reported as ["a", "b", "a"] on one run and
["b", "a", "b"] on the next. Rotating each cycle to start at its
lexicographically smallest node (direction preserved -- never reversed)
makes the output stable across runs, which the run logs depend on for
reproducibility and which keeps equivalent rotations from being counted as
distinct findings.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import networkx as nx

DEFAULT_EXCLUDED_SOURCES = frozenset({"docker-compose"})


def build_graph(
    edges: list[dict], exclude_sources: frozenset[str] = DEFAULT_EXCLUDED_SOURCES
) -> nx.DiGraph:
    """Build a directed graph of service -> service calls from an edge list.

    Multiple raw edges between the same (caller, callee) pair (e.g. two
    different call sites, or a Feign edge and a RestTemplate edge) are
    collapsed into a single graph edge whose "evidence" attribute holds the
    full list of underlying edge records, so no detail is lost.
    """
    graph = nx.DiGraph()
    for e in edges:
        if e["source"] in exclude_sources:
            continue
        caller, callee = e["caller"], e["callee"]
        if graph.has_edge(caller, callee):
            graph[caller][callee]["evidence"].append(e)
        else:
            graph.add_edge(caller, callee, evidence=[e])
    return graph


def canonicalize_cycle(cycle: list[str]) -> list[str]:
    """Rotate an open cycle to start at its lexicographically smallest node.

    Direction is preserved: only rotation is applied, never reversal, so
    a -> b -> c stays a -> b -> c and does not become a -> c -> b (which
    would be a different dependency structure).

    Accepts either an open cycle (["b", "a"]) or a closed one
    (["b", "a", "b"]) and always returns the open form.
    """
    if not cycle:
        return []
    nodes = cycle[:-1] if len(cycle) > 1 and cycle[0] == cycle[-1] else list(cycle)
    if not nodes:
        return []
    start = min(range(len(nodes)), key=lambda i: nodes[i])
    return nodes[start:] + nodes[:start]


def detect_cycles(graph: nx.DiGraph) -> list[dict]:
    """Return one {"smell": "Cyclic Dependency", "cycle": [...]} per cycle found.

    `cycle` is the closed path (first node repeated at the end), matching
    the shape documented in CLAUDE.md Step 3, e.g.
    ["order-service", "payment-service", "order-service"].

    Each cycle is canonicalized (see `canonicalize_cycle`) and the list is
    sorted, so the same graph always produces byte-identical output
    regardless of NetworkX's internal traversal order.
    """
    results = []
    for cycle in nx.simple_cycles(graph):
        nodes = canonicalize_cycle(cycle)
        results.append({"smell": "Cyclic Dependency", "cycle": nodes + [nodes[0]]})
    results.sort(key=lambda r: (len(r["cycle"]), r["cycle"]))
    return results


def cycle_evidence(graph: nx.DiGraph, closed_cycle: list[str]) -> list[dict]:
    """Return the underlying edge records (file/line/evidence) for every hop in a closed cycle."""
    records: list[dict] = []
    for u, v in zip(closed_cycle, closed_cycle[1:]):
        records.extend(graph[u][v]["evidence"])
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 3: build the SDG and detect Cyclic Dependencies.")
    parser.add_argument("edges_json", type=Path, help="Path to the JSON edge list produced by pipeline.extractor")
    parser.add_argument(
        "--include-docker-compose",
        action="store_true",
        help="Include docker-compose depends_on edges in cycle detection (excluded by default)",
    )
    args = parser.parse_args()

    edges = json.loads(args.edges_json.read_text(encoding="utf-8"))
    exclude = frozenset() if args.include_docker_compose else DEFAULT_EXCLUDED_SOURCES
    graph = build_graph(edges, exclude_sources=exclude)
    cycles = detect_cycles(graph)
    print(json.dumps(cycles, indent=2))


if __name__ == "__main__":
    main()
