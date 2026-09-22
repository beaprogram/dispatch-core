"""Tests for graph loading and validation.

Every test here reads the committed fixture from disk. Nothing in this module
downloads anything, so CI stays offline. test_loading_fixture_makes_no_network_calls
enforces that by breaking the socket layer before loading.
"""

import socket
from pathlib import Path

import networkx as nx
import pytest

from dispatch_core.graph import (
    graph_stats,
    largest_strongly_connected_component,
    load_graph,
    save_graph,
    validate_graph,
)

FIXTURE_PATH = Path(__file__).parent.parent / "data" / "fixtures" / "downtown_small.graphml"


@pytest.fixture
def fixture_graph() -> nx.MultiDiGraph:
    """Load the committed 800 m Halifax downtown fixture."""
    return load_graph(str(FIXTURE_PATH))


def test_fixture_exists_and_is_small() -> None:
    """The fixture is committed and stays under the 2 MB budget."""
    assert FIXTURE_PATH.is_file()
    size_mb = FIXTURE_PATH.stat().st_size / (1024 * 1024)
    assert size_mb < 2.0, f"Fixture is {size_mb:.2f} MB, over the 2 MB budget"


def test_load_graph(fixture_graph: nx.MultiDiGraph) -> None:
    """The fixture loads as a directed multigraph with nodes and edges."""
    assert isinstance(fixture_graph, nx.MultiDiGraph)
    assert fixture_graph.number_of_nodes() > 0
    assert fixture_graph.number_of_edges() > 0


def test_loading_fixture_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading the fixture must not open a socket, so CI can run offline."""

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("load_graph attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)

    graph = load_graph(str(FIXTURE_PATH))
    assert graph.number_of_nodes() > 0


def test_fixture_is_strongly_connected(fixture_graph: nx.MultiDiGraph) -> None:
    """Every node must reach every other node despite one-way streets."""
    assert nx.is_strongly_connected(fixture_graph)


def test_every_edge_has_length_and_travel_time(fixture_graph: nx.MultiDiGraph) -> None:
    """All edges carry a positive length and travel_time."""
    is_valid, message = validate_graph(fixture_graph)
    assert is_valid, message


def test_edge_weights_load_as_numbers(fixture_graph: nx.MultiDiGraph) -> None:
    """GraphML stores strings, so the loader must restore numeric weights."""
    for _, _, data in fixture_graph.edges(data=True):
        assert isinstance(data["length"], float)
        assert isinstance(data["travel_time"], float)


def test_nodes_have_coordinates(fixture_graph: nx.MultiDiGraph) -> None:
    """Every node carries numeric lat/lon, needed for projection and snapping."""
    for _, data in fixture_graph.nodes(data=True):
        assert isinstance(data["x"], float)
        assert isinstance(data["y"], float)


def test_validate_graph_rejects_missing_length(fixture_graph: nx.MultiDiGraph) -> None:
    """A missing length attribute is reported."""
    graph = fixture_graph.copy()
    u, v, key = next(iter(graph.edges(keys=True)))
    del graph[u][v][key]["length"]
    is_valid, message = validate_graph(graph)
    assert not is_valid
    assert "length" in message


def test_validate_graph_rejects_missing_travel_time(fixture_graph: nx.MultiDiGraph) -> None:
    """A missing travel_time attribute is reported."""
    graph = fixture_graph.copy()
    u, v, key = next(iter(graph.edges(keys=True)))
    del graph[u][v][key]["travel_time"]
    is_valid, message = validate_graph(graph)
    assert not is_valid
    assert "travel_time" in message


def test_validate_graph_rejects_non_positive_length(fixture_graph: nx.MultiDiGraph) -> None:
    """A zero length is reported, since it would break distance weighting."""
    graph = fixture_graph.copy()
    u, v, key = next(iter(graph.edges(keys=True)))
    graph[u][v][key]["length"] = 0.0
    is_valid, message = validate_graph(graph)
    assert not is_valid
    assert "length" in message


def test_largest_strongly_connected_component_keeps_the_biggest() -> None:
    """The larger of two disjoint cycles survives, the smaller is dropped."""
    graph = nx.MultiDiGraph()
    small_cycle = [0, 1]
    large_cycle = [2, 3, 4, 5, 6]
    for cycle in (small_cycle, large_cycle):
        for i, node in enumerate(cycle):
            graph.add_edge(node, cycle[(i + 1) % len(cycle)], length=10.0, travel_time=1.0)

    scc = largest_strongly_connected_component(graph)

    assert set(scc.nodes()) == set(large_cycle)
    assert nx.is_strongly_connected(scc)


def test_largest_strongly_connected_component_drops_unreachable_node() -> None:
    """A node reachable one way only is dropped, mirroring a one-way dead end."""
    graph = nx.MultiDiGraph()
    for u, v in [(0, 1), (1, 2), (2, 0)]:
        graph.add_edge(u, v, length=10.0, travel_time=1.0)
    graph.add_edge(0, 99, length=10.0, travel_time=1.0)

    scc = largest_strongly_connected_component(graph)

    assert 99 not in scc.nodes()
    assert set(scc.nodes()) == {0, 1, 2}


def test_graph_stats(fixture_graph: nx.MultiDiGraph) -> None:
    """graph_stats agrees with networkx counts."""
    num_nodes, num_edges = graph_stats(fixture_graph)
    assert num_nodes == fixture_graph.number_of_nodes()
    assert num_edges == fixture_graph.number_of_edges()


def test_save_and_load_round_trip(tmp_path: Path, fixture_graph: nx.MultiDiGraph) -> None:
    """Saving then loading preserves nodes, edges, and numeric weights."""
    output_path = str(tmp_path / "round_trip.graphml")
    save_graph(fixture_graph, output_path)
    loaded = load_graph(output_path)

    assert loaded.number_of_nodes() == fixture_graph.number_of_nodes()
    assert loaded.number_of_edges() == fixture_graph.number_of_edges()

    for u, v, key, data in fixture_graph.edges(keys=True, data=True):
        assert loaded[u][v][key]["length"] == pytest.approx(data["length"])
        assert loaded[u][v][key]["travel_time"] == pytest.approx(data["travel_time"])
