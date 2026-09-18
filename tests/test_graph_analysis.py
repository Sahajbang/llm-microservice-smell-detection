"""Step 3 tests: synthetic edge lists with known cycle / no-cycle outcomes.

Per CLAUDE.md's guidance for Step 3: "construct a small synthetic edge list
with a known cycle and confirm the function finds it".
"""

from pipeline.graph_analysis import build_graph, cycle_evidence, detect_cycles


def _edge(caller, callee, source="feign", file="x.java", line=1):
    return {"caller": caller, "callee": callee, "source": source, "file": file, "line": line, "evidence": f"{caller}->{callee}"}


def test_detects_known_two_node_cycle():
    edges = [
        _edge("order-service", "payment-service"),
        _edge("payment-service", "order-service"),
    ]
    graph = build_graph(edges)
    cycles = detect_cycles(graph)

    assert len(cycles) == 1
    assert cycles[0]["smell"] == "Cyclic Dependency"
    cycle = cycles[0]["cycle"]
    assert cycle[0] == cycle[-1]
    assert set(cycle[:-1]) == {"order-service", "payment-service"}


def test_detects_known_three_node_cycle():
    edges = [
        _edge("a", "b"),
        _edge("b", "c"),
        _edge("c", "a"),
    ]
    graph = build_graph(edges)
    cycles = detect_cycles(graph)

    assert len(cycles) == 1
    assert set(cycles[0]["cycle"][:-1]) == {"a", "b", "c"}


def test_no_cycle_in_a_dag():
    edges = [
        _edge("api-gateway", "customers-service"),
        _edge("api-gateway", "visits-service"),
        _edge("genai-service", "vets-service"),
    ]
    graph = build_graph(edges)
    assert detect_cycles(graph) == []


def test_docker_compose_edges_excluded_by_default():
    edges = [
        _edge("a", "b", source="docker-compose", file="docker-compose.yml", line=0),
        _edge("b", "a", source="docker-compose", file="docker-compose.yml", line=0),
    ]
    graph = build_graph(edges)
    assert graph.number_of_edges() == 0
    assert detect_cycles(graph) == []


def test_docker_compose_edges_can_be_opted_in():
    edges = [
        _edge("a", "b", source="docker-compose", file="docker-compose.yml", line=0),
        _edge("b", "a", source="docker-compose", file="docker-compose.yml", line=0),
    ]
    graph = build_graph(edges, exclude_sources=frozenset())
    assert len(detect_cycles(graph)) == 1


def test_multiple_edges_between_same_pair_collapse_but_keep_evidence():
    edges = [
        _edge("customers-service", "visits-service", source="resttemplate", file="A.java", line=10),
        _edge("visits-service", "customers-service", source="resttemplate", file="B.java", line=20),
    ]
    graph = build_graph(edges)
    cycles = detect_cycles(graph)
    assert len(cycles) == 1

    records = cycle_evidence(graph, cycles[0]["cycle"])
    files = {r["file"] for r in records}
    assert files == {"A.java", "B.java"}
