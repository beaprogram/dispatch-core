"""A*, this repo's Dijkstra, and networkx on the same seeded node pairs.

networkx appears here only as an independent reference implementation. The point is
to show that the hand written search lands in the same place on cost while doing a
different amount of work, not to claim it beats a mature library.
"""

from __future__ import annotations

import random
import statistics
from pathlib import Path
from typing import Any

import networkx as nx

from benchmarks.common import Environment, time_repeats, write_csv
from dispatch_core.graph import load_graph
from dispatch_core.routing import LENGTH, TRAVEL_TIME, astar, dijkstra

PAIR_COUNT = 200
SEED = 42
WEIGHTS = (LENGTH, TRAVEL_TIME)
FULL_GRAPH = Path(__file__).parent.parent / "data" / "cache" / "halifax_5000m.graphml"
FIXTURE_GRAPH = Path(__file__).parent.parent / "data" / "fixtures" / "downtown_small.graphml"


def seeded_pairs(graph: nx.MultiDiGraph, count: int, seed: int) -> list[tuple[int, int]]:
    """Reproducible source and target pairs."""
    rng = random.Random(seed)
    nodes = sorted(graph.nodes())
    pairs: list[tuple[int, int]] = []
    while len(pairs) < count:
        source, target = rng.choice(nodes), rng.choice(nodes)
        if source != target:
            pairs.append((source, target))
    return pairs


def measure(graph: nx.MultiDiGraph, graph_name: str, weight: str) -> list[dict[str, Any]]:
    """Time all three implementations on the same pairs for one weight."""
    pairs = seeded_pairs(graph, PAIR_COUNT, SEED)

    astar_expansions = [astar(graph, s, t, weight).nodes_expanded for s, t in pairs]
    dijkstra_expansions = [dijkstra(graph, s, t, weight).nodes_expanded for s, t in pairs]

    astar_timing = time_repeats(lambda: [astar(graph, s, t, weight) for s, t in pairs])
    dijkstra_timing = time_repeats(lambda: [dijkstra(graph, s, t, weight) for s, t in pairs])
    networkx_timing = time_repeats(
        lambda: [nx.shortest_path_length(graph, s, t, weight=weight) for s, t in pairs]
    )

    rows = []
    for name, timing, expansions in (
        ("astar", astar_timing, astar_expansions),
        ("dijkstra", dijkstra_timing, dijkstra_expansions),
        ("networkx_dijkstra", networkx_timing, None),
    ):
        rows.append(
            {
                "graph": graph_name,
                "weight": weight,
                "algorithm": name,
                "pairs": PAIR_COUNT,
                "median_ms": round(timing.median_ms, 4),
                "p95_ms": round(timing.p95_ms, 4),
                "median_ms_per_pair": round(timing.median_ms / PAIR_COUNT, 5),
                "median_nodes_expanded": (
                    round(statistics.median(expansions), 1) if expansions else ""
                ),
                "mean_nodes_expanded": (
                    round(statistics.mean(expansions), 1) if expansions else ""
                ),
            }
        )
        print(
            f"  {graph_name:>8} {weight:>11} {name:>18}: "
            f"{timing.median_ms:9.2f} ms median"
            + (f", {statistics.mean(expansions):8.1f} mean expanded" if expansions else "")
        )

    return rows


def run() -> list[dict[str, Any]]:
    """Measure on the full graph, falling back to the fixture when it is absent."""
    rows: list[dict[str, Any]] = []
    graph_path = FULL_GRAPH if FULL_GRAPH.exists() else FIXTURE_GRAPH
    graph_name = "full_5000m" if FULL_GRAPH.exists() else "fixture_800m"
    graph = load_graph(str(graph_path))

    print(
        f"  graph: {graph_name}, {graph.number_of_nodes()} nodes, {graph.number_of_edges()} edges"
    )
    for weight in WEIGHTS:
        rows.extend(measure(graph, graph_name, weight))
    return rows


def main() -> None:
    """Run the benchmark and write its CSV."""
    print("bench_routing: A* against Dijkstra against networkx")
    environment = Environment.capture()
    rows = run()
    path = write_csv("bench_routing.csv", rows, environment)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
