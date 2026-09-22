"""A* and Dijkstra over the road graph, with instrumentation.

Both searches share one implementation. Dijkstra is A* with a zero heuristic,
which keeps the comparison in CP5 honest: any difference in nodes expanded comes
from the heuristic alone, not from two separately tuned code paths.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass
from heapq import heappop, heappush

import networkx as nx

from dispatch_core.geo import haversine, project_to_local
from dispatch_core.quadtree import Quadtree, bounds_from_points

NodeId = int
Heuristic = Callable[[NodeId], float]

LENGTH = "length"
TRAVEL_TIME = "travel_time"
SUPPORTED_WEIGHTS = (LENGTH, TRAVEL_TIME)


class NoPathError(Exception):
    """Raised when no directed path exists from source to target.

    One-way streets make the road network directed, so an unreachable target is a
    real outcome rather than a bug, and it is raised explicitly instead of being
    reported as an infinite cost that a caller might use in arithmetic.
    """


@dataclass(frozen=True, slots=True)
class RouteResult:
    """The outcome of a single search."""

    cost: float
    path: list[NodeId]
    nodes_expanded: int


def max_edge_speed_mps(graph: nx.MultiDiGraph) -> float:
    """Fastest edge speed in the graph, in meters per second.

    Memoised onto graph.graph, because the travel_time heuristic needs it on every
    call and rescanning every edge per search would dominate the search itself.

    Time complexity: O(E) on the first call, O(1) after.
    """
    cached = graph.graph.get("max_speed_mps")
    if cached is not None:
        return float(cached)

    speeds = [float(data["speed_kph"]) for _, _, data in graph.edges(data=True)]
    if not speeds:
        raise ValueError("Graph has no edges, so it has no maximum speed")

    value = max(speeds) * 1000.0 / 3600.0
    graph.graph["max_speed_mps"] = value
    return value


def make_heuristic(graph: nx.MultiDiGraph, target: NodeId, weight: str) -> Heuristic:
    """Build a consistent heuristic estimating remaining cost to target.

    For "length" this is the haversine distance to the target, which never exceeds
    the true road distance because a road cannot be shorter than the straight line
    between its endpoints.

    For "travel_time" it is that same distance divided by the fastest edge speed in
    the graph. No edge can be travelled faster than the fastest edge, so this cannot
    overestimate the remaining time.

    Time complexity: O(1) per call after construction.
    """
    if weight not in SUPPORTED_WEIGHTS:
        raise ValueError(f"weight must be one of {SUPPORTED_WEIGHTS}, got {weight!r}")
    if target not in graph:
        raise ValueError(f"Target node {target!r} is not in the graph")

    nodes = graph.nodes
    target_lat = float(nodes[target]["y"])
    target_lon = float(nodes[target]["x"])
    divisor = 1.0 if weight == LENGTH else max_edge_speed_mps(graph)

    def heuristic(node: NodeId) -> float:
        data = nodes[node]
        return haversine(float(data["y"]), float(data["x"]), target_lat, target_lon) / divisor

    return heuristic


def _zero_heuristic(node: NodeId) -> float:
    """The heuristic that turns A* into Dijkstra."""
    return 0.0


def _reconstruct_path(came_from: dict[NodeId, NodeId], node: NodeId) -> list[NodeId]:
    """Walk parent links back to the source and return the path forwards."""
    path = [node]
    while node in came_from:
        node = came_from[node]
        path.append(node)
    path.reverse()
    return path


def _search(
    graph: nx.MultiDiGraph,
    source: NodeId,
    target: NodeId,
    weight: str,
    heuristic: Heuristic,
) -> RouteResult:
    """Best-first shortest path search shared by A* and Dijkstra.

    Frontier entries are (f, counter, node). The counter makes every tuple unique
    before the node is ever reached, so equal f values are broken by insertion order
    and node ids are never compared.

    A node is expanded once. With a consistent heuristic its cost is final the first
    time it is popped, so the closed set is a correctness-preserving optimisation
    rather than an approximation.

    Time complexity: O(E log V) worst case, far less when the heuristic is informative.
    """
    if source not in graph:
        raise ValueError(f"Source node {source!r} is not in the graph")
    if target not in graph:
        raise ValueError(f"Target node {target!r} is not in the graph")
    if weight not in SUPPORTED_WEIGHTS:
        raise ValueError(f"weight must be one of {SUPPORTED_WEIGHTS}, got {weight!r}")

    if source == target:
        return RouteResult(cost=0.0, path=[source], nodes_expanded=0)

    counter = itertools.count()
    best_cost: dict[NodeId, float] = {source: 0.0}
    came_from: dict[NodeId, NodeId] = {}
    closed: set[NodeId] = set()
    frontier: list[tuple[float, int, NodeId]] = [(heuristic(source), next(counter), source)]
    nodes_expanded = 0

    while frontier:
        _, _, node = heappop(frontier)
        if node in closed:
            continue
        closed.add(node)
        nodes_expanded += 1

        if node == target:
            return RouteResult(
                cost=best_cost[node],
                path=_reconstruct_path(came_from, node),
                nodes_expanded=nodes_expanded,
            )

        cost_so_far = best_cost[node]
        for neighbor, parallel_edges in graph[node].items():
            if neighbor in closed:
                continue
            # Parallel edges between the same pair: only the cheapest can matter.
            step = min(float(data[weight]) for data in parallel_edges.values())
            tentative = cost_so_far + step
            if tentative < best_cost.get(neighbor, math.inf):
                best_cost[neighbor] = tentative
                came_from[neighbor] = node
                heappush(frontier, (tentative + heuristic(neighbor), next(counter), neighbor))

    raise NoPathError(f"No directed path from {source!r} to {target!r} using weight {weight!r}")


def astar(
    graph: nx.MultiDiGraph, source: NodeId, target: NodeId, weight: str = LENGTH
) -> RouteResult:
    """Shortest path from source to target, guided by a haversine heuristic.

    Raises NoPathError when the target is unreachable.

    Time complexity: O(E log V) worst case, typically far fewer expansions than
    Dijkstra because the heuristic orders the frontier toward the target.
    """
    return _search(graph, source, target, weight, make_heuristic(graph, target, weight))


def dijkstra(
    graph: nx.MultiDiGraph, source: NodeId, target: NodeId, weight: str = LENGTH
) -> RouteResult:
    """Shortest path from source to target with no heuristic guidance.

    Identical code to astar with the heuristic fixed at zero, so the two differ only
    in search order.

    Time complexity: O(E log V)
    """
    return _search(graph, source, target, weight, _zero_heuristic)


class NodeLocator:
    """Snaps arbitrary coordinates to the nearest graph node.

    Graph nodes are projected once into local meters and held in the project's own
    quadtree, so a snap is a nearest neighbour query rather than a scan of every node.
    """

    def __init__(self, graph: nx.MultiDiGraph, margin: float = 50.0) -> None:
        latitudes = [data["y"] for _, data in graph.nodes(data=True)]
        longitudes = [data["x"] for _, data in graph.nodes(data=True)]
        if not latitudes:
            raise ValueError("Cannot build a locator for a graph with no nodes")

        self.center_lat = (min(latitudes) + max(latitudes)) / 2
        self.center_lon = (min(longitudes) + max(longitudes)) / 2

        projected = [
            project_to_local(data["y"], data["x"], self.center_lat, self.center_lon)
            for _, data in graph.nodes(data=True)
        ]
        self._tree = Quadtree(bounds_from_points(projected, margin=margin))
        for node_id, (x, y) in zip(graph.nodes(), projected, strict=True):
            self._tree.insert(node_id, x, y)

    def __len__(self) -> int:
        """Number of indexed graph nodes.

        Time complexity: O(1)
        """
        return len(self._tree)

    def snap(self, lat: float, lon: float) -> NodeId:
        """Nearest graph node to the given coordinates.

        Time complexity: O(log V) average.
        """
        x, y = project_to_local(lat, lon, self.center_lat, self.center_lon)
        found = self._tree.nearest(x, y)
        # The constructor rejects a graph with no nodes, so the tree is never empty.
        assert found is not None
        return int(found.id)
