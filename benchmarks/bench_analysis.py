"""Measurements that support specific claims in the README.

These are not performance benchmarks. Each one exists so that a factual claim made in
the documentation is reproducible by running this file, rather than resting on a
script that was run once and thrown away.
"""

from __future__ import annotations

import math
import random
from pathlib import Path
from typing import Any

from benchmarks.common import Environment, write_csv
from dispatch_core.dispatcher import Dispatcher
from dispatch_core.geo import haversine, project_to_local
from dispatch_core.graph import load_graph
from dispatch_core.quadtree import BoundingBox, Quadtree, SearchStats
from dispatch_core.routing import max_edge_speed_mps
from dispatch_core.sim import generate_couriers, generate_orders

CENTER_LAT = 44.6476
CENTER_LON = -63.5806
PROJECTION_GRID_STEPS = 40
PROJECTION_RADIUS_M = 5_000
RING_SIZE = 8_000
RING_RADIUS_M = 1_000.0
HARBOUR_MERIDIAN = -63.560
SEED = 42
FULL_GRAPH = Path(__file__).parent.parent / "data" / "cache" / "halifax_5000m.graphml"
FIXTURE_GRAPH = Path(__file__).parent.parent / "data" / "fixtures" / "downtown_small.graphml"


def measure_projection_error() -> list[dict[str, Any]]:
    """Worst relative error between projected Euclidean distance and haversine.

    Supports the claim that working in flat local metres does not change which
    courier is nearest at this scale.
    """
    worst = 0.0
    offset = PROJECTION_RADIUS_M / 111_320
    for i in range(-PROJECTION_GRID_STEPS, PROJECTION_GRID_STEPS + 1):
        for j in range(-PROJECTION_GRID_STEPS, PROJECTION_GRID_STEPS + 1):
            lat = CENTER_LAT + offset * i / PROJECTION_GRID_STEPS
            lon = CENTER_LON + offset * j / PROJECTION_GRID_STEPS
            true_distance = haversine(CENTER_LAT, CENTER_LON, lat, lon)
            if true_distance < 1.0:
                continue
            x, y = project_to_local(lat, lon, CENTER_LAT, CENTER_LON)
            worst = max(worst, abs(math.hypot(x, y) - true_distance) / true_distance)

    grid = 2 * PROJECTION_GRID_STEPS + 1
    return [
        {
            "measurement": "projection_worst_relative_error_pct",
            "value": round(worst * 100, 4),
            "detail": f"{grid} by {grid} grid over a {PROJECTION_RADIUS_M} m radius",
        }
    ]


def measure_ring_pathology() -> list[dict[str, Any]]:
    """Distance checks for a query at the centre of a ring of equidistant points.

    Supports the claim about the second quadtree worst case.
    """
    bounds = BoundingBox(-2_000.0, -2_000.0, 2_000.0, 2_000.0)
    tree = Quadtree(bounds)
    for i in range(RING_SIZE):
        angle = 2 * math.pi * i / RING_SIZE
        tree.insert(i, RING_RADIUS_M * math.cos(angle), RING_RADIUS_M * math.sin(angle))

    stats = SearchStats()
    tree.nearest(0.0, 0.0, stats=stats)
    return [
        {
            "measurement": "ring_points_checked",
            "value": stats.points_checked,
            "detail": f"one nearest query at the centre of a {RING_SIZE} point ring",
        },
        {
            "measurement": "ring_size",
            "value": RING_SIZE,
            "detail": "total points in the ring",
        },
    ]


def measure_work_budget() -> list[dict[str, Any]]:
    """Mean distance checks per nearest query on 10,000 uniform seeded points.

    Supports the claim that the quadtree does far fewer distance checks than a scan,
    and matches the budget asserted by tests/test_quadtree.py.
    """
    count, queries = 10_000, 200
    limit = 1_000.0
    rng = random.Random(20240922)
    tree = Quadtree(BoundingBox(-limit - 1, -limit - 1, limit + 1, limit + 1))
    span = limit - 1
    for i in range(count):
        tree.insert(i, rng.uniform(-span, span), rng.uniform(-span, span))

    stats = SearchStats()
    for _ in range(queries):
        tree.nearest(rng.uniform(-span, span), rng.uniform(-span, span), stats=stats)

    mean_checked = stats.points_checked / queries
    return [
        {
            "measurement": "mean_distance_checks_per_query",
            "value": round(mean_checked, 2),
            "detail": f"{count} uniform seeded points, {queries} queries",
        },
        {
            "measurement": "distance_checks_vs_scan_factor",
            "value": round(count / mean_checked),
            "detail": "how many fewer distance checks than a linear scan",
        },
    ]


def measure_graph_speeds(graph_path: Path) -> list[dict[str, Any]]:
    """Speed spread, which is what makes the travel_time heuristic loose."""
    graph = load_graph(str(graph_path))
    speeds = sorted(float(d["speed_kph"]) for _, _, d in graph.edges(data=True))
    median = speeds[len(speeds) // 2]
    fastest = speeds[-1]
    at_max = sum(1 for s in speeds if s == fastest)

    return [
        {"measurement": "graph_nodes", "value": graph.number_of_nodes(), "detail": "full graph"},
        {"measurement": "graph_edges", "value": graph.number_of_edges(), "detail": "full graph"},
        {"measurement": "median_speed_kph", "value": round(median, 2), "detail": "over all edges"},
        {"measurement": "max_speed_kph", "value": round(fastest, 2), "detail": "over all edges"},
        {
            "measurement": "edges_at_max_speed",
            "value": at_max,
            "detail": f"of {len(speeds)} edges",
        },
        {
            "measurement": "max_over_median_speed",
            "value": round(fastest / median, 2),
            "detail": "how loose the travel_time divisor is",
        },
    ]


def measure_harbour_effect(graph_path: Path) -> list[dict[str, Any]]:
    """Whether topk-eta's overrides are water crossings or local road topology.

    Supports the README section reporting that the harbour hypothesis was wrong.
    """
    graph = load_graph(str(graph_path))
    coordinates = {n: (float(d["y"]), float(d["x"])) for n, d in graph.nodes(data=True)}
    captured: list[tuple[int, int, int, float, float, float, float]] = []

    class Recording(Dispatcher):
        """Dispatcher that records which courier each strategy would have chosen."""

        def _choose_courier(self, free: Quadtree, pickup: int) -> int:
            x, y = self._projected[pickup]
            candidates = free.k_nearest(x, y, self.top_k)
            etas = {
                int(c.id): self.travel_time(self._courier_nodes[int(c.id)], pickup)
                for c in candidates
            }
            chosen = min(candidates, key=lambda c: etas[int(c.id)])
            straight_nearest = candidates[0]
            if chosen.id != straight_nearest.id:
                captured.append(
                    (
                        pickup,
                        self._courier_nodes[int(straight_nearest.id)],
                        self._courier_nodes[int(chosen.id)],
                        etas[int(straight_nearest.id)],
                        etas[int(chosen.id)],
                        math.hypot(straight_nearest.x - x, straight_nearest.y - y),
                        math.hypot(chosen.x - x, chosen.y - y),
                    )
                )
            return int(chosen.id)

    orders = generate_orders(graph, 1_000, SEED)
    couriers = generate_couriers(graph, 200, SEED)
    Recording(graph, strategy="topk-eta", top_k=5).run(orders, couriers, SEED)

    def side(node: int) -> str:
        return "west" if coordinates[node][1] < HARBOUR_MERIDIAN else "east"

    crossing = [c for c in captured if side(c[0]) != side(c[1])]
    saved_crossing = sum(c[3] - c[4] for c in crossing)
    saved_total = sum(c[3] - c[4] for c in captured)
    worst = max(captured, key=lambda c: c[3] - c[4])

    # Independent check: do high detour node pairs tend to cross the harbour?
    rng = random.Random(7)
    nodes = sorted(graph.nodes())
    vmax = max_edge_speed_mps(graph)
    from dispatch_core.routing import astar

    sampled: list[tuple[float, int, int]] = []
    while len(sampled) < 1_200:
        u, v = rng.choice(nodes), rng.choice(nodes)
        if u == v:
            continue
        straight = haversine(*coordinates[u], *coordinates[v])
        if straight < 300:
            continue
        eta = astar(graph, u, v, "travel_time").cost
        sampled.append((eta / (straight / vmax), u, v))

    sampled.sort(reverse=True)
    top_crossing = sum(1 for _, u, v in sampled[:50] if side(u) != side(v))
    all_crossing = sum(1 for _, u, v in sampled if side(u) != side(v))

    return [
        {"measurement": "disagreements", "value": len(captured), "detail": "of 1000 decisions"},
        {
            "measurement": "disagreements_crossing_harbour",
            "value": len(crossing),
            "detail": f"meridian {HARBOUR_MERIDIAN}",
        },
        {
            "measurement": "crossing_share_of_eta_saved_pct",
            "value": round(100 * saved_crossing / saved_total, 2),
            "detail": "share of total travel time saved",
        },
        {
            "measurement": "worst_case_rejected_straight_m",
            "value": round(worst[5]),
            "detail": "largest single ETA saving, rejected courier",
        },
        {
            "measurement": "worst_case_rejected_eta_s",
            "value": round(worst[3]),
            "detail": "largest single ETA saving, rejected courier",
        },
        {
            "measurement": "worst_case_chosen_straight_m",
            "value": round(worst[6]),
            "detail": "largest single ETA saving, chosen courier",
        },
        {
            "measurement": "worst_case_chosen_eta_s",
            "value": round(worst[4]),
            "detail": "largest single ETA saving, chosen courier",
        },
        {
            "measurement": "top50_detour_pairs_crossing",
            "value": top_crossing,
            "detail": "of the 50 highest detour sampled pairs",
        },
        {
            "measurement": "all_sampled_pairs_crossing_pct",
            "value": round(100 * all_crossing / len(sampled), 2),
            "detail": f"of {len(sampled)} sampled pairs",
        },
    ]


def main() -> None:
    """Run every supporting measurement and write its CSV."""
    print("bench_analysis: measurements supporting README claims")
    graph_path = FULL_GRAPH if FULL_GRAPH.exists() else FIXTURE_GRAPH

    rows: list[dict[str, Any]] = []
    rows.extend(measure_projection_error())
    rows.extend(measure_ring_pathology())
    rows.extend(measure_work_budget())
    rows.extend(measure_graph_speeds(graph_path))
    rows.extend(measure_harbour_effect(graph_path))

    for row in rows:
        print(f"  {row['measurement']:>38}: {row['value']}")

    path = write_csv("bench_analysis.csv", rows, Environment.capture())
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
