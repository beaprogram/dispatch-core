"""Tests for A*, Dijkstra, and coordinate snapping.

networkx is used here purely as an independent oracle for path costs. The search
itself is implemented in this repo; networkx never routes for it.
"""

import math
import random
from pathlib import Path

import networkx as nx
import pytest

from dispatch_core.bruteforce import brute_nearest
from dispatch_core.geo import haversine, project_to_local
from dispatch_core.graph import load_graph
from dispatch_core.quadtree import Point
from dispatch_core.routing import (
    LENGTH,
    TRAVEL_TIME,
    NodeLocator,
    NoPathError,
    RouteResult,
    astar,
    dijkstra,
    make_heuristic,
    max_edge_speed_mps,
)

FIXTURE_PATH = Path(__file__).parent.parent / "data" / "fixtures" / "downtown_small.graphml"
SEED = 42
PAIR_COUNT = 200
RELATIVE_TOLERANCE = 1e-9
WEIGHTS = [LENGTH, TRAVEL_TIME]


@pytest.fixture(scope="module")
def graph() -> nx.MultiDiGraph:
    """The committed fixture graph, loaded once for the module."""
    return load_graph(str(FIXTURE_PATH))


@pytest.fixture(scope="module")
def seeded_pairs(graph: nx.MultiDiGraph) -> list[tuple[int, int]]:
    """200 reproducible source and target pairs drawn from the fixture."""
    rng = random.Random(SEED)
    nodes = sorted(graph.nodes())
    pairs = []
    while len(pairs) < PAIR_COUNT:
        source, target = rng.choice(nodes), rng.choice(nodes)
        if source != target:
            pairs.append((source, target))
    return pairs


# Heuristic validity


@pytest.mark.parametrize("weight", WEIGHTS)
def test_heuristic_is_consistent_on_every_edge(graph: nx.MultiDiGraph, weight: str) -> None:
    """h(u) <= w(u, v) + h(v) on every edge, for a spread of targets.

    Consistency is the stronger property: it implies admissibility and guarantees a
    node's cost is final the first time it is expanded, which is what makes the
    closed set safe. Asserted with no tolerance, because it holds exactly here.
    """
    rng = random.Random(SEED)
    targets = rng.sample(sorted(graph.nodes()), 25)

    worst_slack = math.inf
    for target in targets:
        heuristic = make_heuristic(graph, target, weight)
        for u, v, data in graph.edges(data=True):
            slack = data[weight] + heuristic(v) - heuristic(u)
            worst_slack = min(worst_slack, slack)

    assert worst_slack >= 0.0, (
        f"heuristic for {weight!r} is inconsistent by {-worst_slack:.6e} at worst"
    )


@pytest.mark.parametrize("weight", WEIGHTS)
def test_heuristic_never_exceeds_true_cost(
    graph: nx.MultiDiGraph, seeded_pairs: list[tuple[int, int]], weight: str
) -> None:
    """Admissibility directly: the estimate never exceeds the real path cost."""
    for source, target in seeded_pairs[:50]:
        heuristic = make_heuristic(graph, target, weight)
        true_cost = nx.shortest_path_length(graph, source, target, weight=weight)
        assert heuristic(source) <= true_cost * (1 + RELATIVE_TOLERANCE)


def test_heuristic_at_target_is_zero(graph: nx.MultiDiGraph) -> None:
    """The estimate from the target to itself is zero, as any goal test needs."""
    target = next(iter(graph.nodes()))
    for weight in WEIGHTS:
        assert make_heuristic(graph, target, weight)(target) == 0.0


def test_heuristic_rejects_unknown_weight(graph: nx.MultiDiGraph) -> None:
    """An unsupported weight is a caller error, not a silent fallback."""
    target = next(iter(graph.nodes()))
    with pytest.raises(ValueError, match="weight must be one of"):
        make_heuristic(graph, target, "elevation")


def test_max_edge_speed_is_memoised(graph: nx.MultiDiGraph) -> None:
    """The max speed is cached on the graph, since every travel_time search needs it."""
    speed = max_edge_speed_mps(graph)
    assert speed > 0
    assert graph.graph["max_speed_mps"] == speed
    assert max_edge_speed_mps(graph) == speed


# Correctness against networkx


@pytest.mark.parametrize("weight", WEIGHTS)
def test_astar_cost_matches_networkx(
    graph: nx.MultiDiGraph, seeded_pairs: list[tuple[int, int]], weight: str
) -> None:
    """A* cost equals the networkx oracle on 200 seeded pairs."""
    for source, target in seeded_pairs:
        result = astar(graph, source, target, weight=weight)
        expected = nx.shortest_path_length(graph, source, target, weight=weight)
        assert result.cost == pytest.approx(expected, rel=RELATIVE_TOLERANCE)


@pytest.mark.parametrize("weight", WEIGHTS)
def test_dijkstra_cost_matches_networkx(
    graph: nx.MultiDiGraph, seeded_pairs: list[tuple[int, int]], weight: str
) -> None:
    """Dijkstra cost equals the networkx oracle on the same 200 pairs."""
    for source, target in seeded_pairs:
        result = dijkstra(graph, source, target, weight=weight)
        expected = nx.shortest_path_length(graph, source, target, weight=weight)
        assert result.cost == pytest.approx(expected, rel=RELATIVE_TOLERANCE)


@pytest.mark.parametrize("weight", WEIGHTS)
def test_astar_and_dijkstra_agree(
    graph: nx.MultiDiGraph, seeded_pairs: list[tuple[int, int]], weight: str
) -> None:
    """The heuristic changes search order, never the answer."""
    for source, target in seeded_pairs[:50]:
        assert astar(graph, source, target, weight=weight).cost == pytest.approx(
            dijkstra(graph, source, target, weight=weight).cost, rel=RELATIVE_TOLERANCE
        )


@pytest.mark.parametrize("weight", WEIGHTS)
def test_returned_path_is_walkable_and_costs_what_is_claimed(
    graph: nx.MultiDiGraph, seeded_pairs: list[tuple[int, int]], weight: str
) -> None:
    """Every consecutive pair is a real edge, and the edges sum to the stated cost."""
    for source, target in seeded_pairs[:50]:
        result = astar(graph, source, target, weight=weight)

        assert result.path[0] == source
        assert result.path[-1] == target

        total = 0.0
        for u, v in zip(result.path, result.path[1:], strict=False):
            assert graph.has_edge(u, v), f"path uses missing edge {u} to {v}"
            total += min(data[weight] for data in graph[u][v].values())

        assert total == pytest.approx(result.cost, rel=RELATIVE_TOLERANCE)


def test_parallel_edges_use_the_minimum_weight() -> None:
    """With two edges between a pair, the search must take the cheaper one."""
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=-63.58, y=44.64)
    graph.add_node(2, x=-63.57, y=44.64)
    graph.add_edge(1, 2, key=0, length=900.0, travel_time=90.0, speed_kph=36.0)
    graph.add_edge(1, 2, key=1, length=800.0, travel_time=80.0, speed_kph=36.0)

    assert astar(graph, 1, 2, weight=LENGTH).cost == pytest.approx(800.0)
    assert astar(graph, 1, 2, weight=TRAVEL_TIME).cost == pytest.approx(80.0)


# Search efficiency


@pytest.mark.parametrize("weight", WEIGHTS)
def test_astar_expands_fewer_nodes_than_dijkstra_on_average(
    graph: nx.MultiDiGraph, seeded_pairs: list[tuple[int, int]], weight: str
) -> None:
    """Compared as means, because per pair a tie can order either way."""
    astar_total = sum(astar(graph, s, t, weight=weight).nodes_expanded for s, t in seeded_pairs)
    dijkstra_total = sum(
        dijkstra(graph, s, t, weight=weight).nodes_expanded for s, t in seeded_pairs
    )

    astar_mean = astar_total / len(seeded_pairs)
    dijkstra_mean = dijkstra_total / len(seeded_pairs)

    assert astar_mean < dijkstra_mean, (
        f"A* mean {astar_mean:.1f} is not below Dijkstra mean {dijkstra_mean:.1f}"
    )


# Degenerate and error cases


def test_source_equals_target_costs_nothing(graph: nx.MultiDiGraph) -> None:
    """A trivial route is zero cost and expands nothing."""
    node = next(iter(graph.nodes()))
    result = astar(graph, node, node)
    assert result == RouteResult(cost=0.0, path=[node], nodes_expanded=0)


def test_unreachable_target_raises_no_path_error(graph: nx.MultiDiGraph) -> None:
    """An isolated node cannot be reached, and that is raised rather than returned."""
    modified = graph.copy()
    isolated = 999_999_999
    modified.add_node(isolated, x=-63.5806, y=44.6476)

    source = next(iter(graph.nodes()))
    with pytest.raises(NoPathError, match="No directed path"):
        astar(modified, source, isolated)
    with pytest.raises(NoPathError, match="No directed path"):
        dijkstra(modified, source, isolated)


def test_unreachable_in_the_other_direction_also_raises(graph: nx.MultiDiGraph) -> None:
    """A node with only outgoing edges cannot be reached from the graph either."""
    modified = graph.copy()
    dangling = 999_999_998
    target = next(iter(graph.nodes()))
    modified.add_node(dangling, x=-63.5806, y=44.6476)
    modified.add_edge(dangling, target, length=10.0, travel_time=1.0, speed_kph=36.0)

    with pytest.raises(NoPathError):
        astar(modified, target, dangling)


def test_missing_source_or_target_raises_value_error(graph: nx.MultiDiGraph) -> None:
    """A node that is not in the graph is a caller error, distinct from no path."""
    node = next(iter(graph.nodes()))
    with pytest.raises(ValueError, match="Source node"):
        astar(graph, 123_456_789, node)
    with pytest.raises(ValueError, match="Target node"):
        astar(graph, node, 123_456_789)


def test_search_rejects_unknown_weight(graph: nx.MultiDiGraph) -> None:
    """An unsupported weight is rejected before any searching happens."""
    nodes = sorted(graph.nodes())
    with pytest.raises(ValueError, match="weight must be one of"):
        astar(graph, nodes[0], nodes[1], weight="elevation")


# Snapping


def test_snapping_matches_brute_force(graph: nx.MultiDiGraph) -> None:
    """The quadtree snap agrees with a linear scan over the same projected metric."""
    locator = NodeLocator(graph)
    assert len(locator) == graph.number_of_nodes()

    projected = [
        Point(
            node,
            *project_to_local(data["y"], data["x"], locator.center_lat, locator.center_lon),
        )
        for node, data in graph.nodes(data=True)
    ]

    rng = random.Random(SEED)
    latitudes = [data["y"] for _, data in graph.nodes(data=True)]
    longitudes = [data["x"] for _, data in graph.nodes(data=True)]

    for _ in range(PAIR_COUNT):
        lat = rng.uniform(min(latitudes), max(latitudes))
        lon = rng.uniform(min(longitudes), max(longitudes))

        snapped = locator.snap(lat, lon)
        x, y = project_to_local(lat, lon, locator.center_lat, locator.center_lon)
        expected = brute_nearest(projected, x, y)

        assert expected is not None
        snapped_point = next(p for p in projected if p.id == snapped)
        assert math.hypot(snapped_point.x - x, snapped_point.y - y) == pytest.approx(
            math.hypot(expected.x - x, expected.y - y)
        )


def test_snapping_agrees_with_haversine_nearest(graph: nx.MultiDiGraph) -> None:
    """The flat projection must not change which node is nearest on the sphere.

    The quadtree works in projected meters while routing costs are great circle
    based, so this checks the two metrics pick the same node at this scale.
    """
    locator = NodeLocator(graph)
    coordinates = {node: (data["y"], data["x"]) for node, data in graph.nodes(data=True)}

    rng = random.Random(SEED)
    latitudes = [lat for lat, _ in coordinates.values()]
    longitudes = [lon for _, lon in coordinates.values()]

    for _ in range(PAIR_COUNT):
        lat = rng.uniform(min(latitudes), max(latitudes))
        lon = rng.uniform(min(longitudes), max(longitudes))

        snapped = locator.snap(lat, lon)
        by_haversine = min(coordinates, key=lambda n: haversine(lat, lon, *coordinates[n]))

        snapped_distance = haversine(lat, lon, *coordinates[snapped])
        best_distance = haversine(lat, lon, *coordinates[by_haversine])
        assert snapped_distance == pytest.approx(best_distance, rel=1e-6)


def test_snapping_a_node_returns_that_node(graph: nx.MultiDiGraph) -> None:
    """Snapping an exact node position returns a node at zero distance."""
    locator = NodeLocator(graph)
    for node, data in list(graph.nodes(data=True))[:50]:
        snapped = locator.snap(data["y"], data["x"])
        assert haversine(
            data["y"], data["x"], graph.nodes[snapped]["y"], graph.nodes[snapped]["x"]
        ) == pytest.approx(0.0, abs=1e-6)
        assert snapped == node or graph.nodes[snapped] == data


def test_snapping_outside_the_graph_still_works(graph: nx.MultiDiGraph) -> None:
    """A query far outside the node bounds snaps to the closest edge of the network."""
    locator = NodeLocator(graph)
    snapped = locator.snap(44.0, -64.0)
    assert snapped in graph


def test_locator_rejects_empty_graph() -> None:
    """There is nothing to snap to in a graph with no nodes."""
    with pytest.raises(ValueError, match="no nodes"):
        NodeLocator(nx.MultiDiGraph())


def test_max_edge_speed_rejects_edgeless_graph() -> None:
    """A graph with nodes but no edges has no speed to take a maximum of."""
    graph = nx.MultiDiGraph()
    graph.add_node(1, x=-63.58, y=44.64)
    with pytest.raises(ValueError, match="no edges"):
        max_edge_speed_mps(graph)


def test_dijkstra_rejects_missing_target(graph: nx.MultiDiGraph) -> None:
    """Dijkstra builds no heuristic, so it validates the target itself."""
    node = next(iter(graph.nodes()))
    with pytest.raises(ValueError, match="Target node"):
        dijkstra(graph, node, 123_456_789)


def test_dijkstra_rejects_unknown_weight(graph: nx.MultiDiGraph) -> None:
    """Dijkstra validates the weight itself for the same reason."""
    nodes = sorted(graph.nodes())
    with pytest.raises(ValueError, match="weight must be one of"):
        dijkstra(graph, nodes[0], nodes[1], weight="elevation")
