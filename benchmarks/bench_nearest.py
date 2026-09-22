"""Quadtree against a linear scan for nearest and k nearest queries.

Two point distributions are measured because they stress the structure very
differently. Uniform points spread evenly and split cleanly. Clustered points, drawn
from real road nodes with jitter, leave large empty regions and deep dense leaves,
which is what actual courier positions look like.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from benchmarks.common import Environment, time_repeats, write_csv
from dispatch_core.bruteforce import brute_k_nearest, brute_nearest
from dispatch_core.geo import project_to_local
from dispatch_core.graph import load_graph
from dispatch_core.quadtree import BoundingBox, Point, Quadtree, SearchStats, bounds_from_points

SIZES = (1_000, 10_000, 100_000)
QUERY_COUNT = 1_000
K = 5
SEED = 42
JITTER_METERS = 25.0
FULL_GRAPH = Path(__file__).parent.parent / "data" / "cache" / "halifax_5000m.graphml"
FIXTURE_GRAPH = Path(__file__).parent.parent / "data" / "fixtures" / "downtown_small.graphml"


def uniform_points(count: int, seed: int) -> tuple[list[tuple[float, float]], BoundingBox]:
    """Points spread uniformly over a 10 km square."""
    rng = random.Random(seed)
    half = 5_000.0
    coordinates = [(rng.uniform(-half, half), rng.uniform(-half, half)) for _ in range(count)]
    return coordinates, BoundingBox(-half - 1, -half - 1, half + 1, half + 1)


def clustered_points(count: int, seed: int) -> tuple[list[tuple[float, float]], BoundingBox]:
    """Points sampled from real road nodes with small jitter.

    This is the realistic case: couriers sit on streets, so the points inherit the
    road network's density variation instead of filling space evenly.
    """
    graph_path = FULL_GRAPH if FULL_GRAPH.exists() else FIXTURE_GRAPH
    graph = load_graph(str(graph_path))

    latitudes = [data["y"] for _, data in graph.nodes(data=True)]
    longitudes = [data["x"] for _, data in graph.nodes(data=True)]
    center_lat = (min(latitudes) + max(latitudes)) / 2
    center_lon = (min(longitudes) + max(longitudes)) / 2

    projected = [
        project_to_local(float(data["y"]), float(data["x"]), center_lat, center_lon)
        for _, data in graph.nodes(data=True)
    ]

    rng = random.Random(seed)
    coordinates = [
        (
            base[0] + rng.gauss(0.0, JITTER_METERS),
            base[1] + rng.gauss(0.0, JITTER_METERS),
        )
        for base in (rng.choice(projected) for _ in range(count))
    ]
    return coordinates, bounds_from_points(coordinates, margin=200.0)


DISTRIBUTIONS = {"uniform": uniform_points, "clustered": clustered_points}


def measure(
    distribution_name: str, size: int, coordinates: list[tuple[float, float]], bounds: BoundingBox
) -> dict[str, Any]:
    """Measure build and query cost for one distribution at one size."""
    points = [Point(i, x, y) for i, (x, y) in enumerate(coordinates)]

    rng = random.Random(SEED + 1)
    queries = [
        (rng.uniform(bounds.min_x, bounds.max_x), rng.uniform(bounds.min_y, bounds.max_y))
        for _ in range(QUERY_COUNT)
    ]

    def build() -> Quadtree:
        tree = Quadtree(bounds)
        for index, (x, y) in enumerate(coordinates):
            tree.insert(index, x, y)
        return tree

    build_timing = time_repeats(build)
    tree = build()

    stats = SearchStats()
    for qx, qy in queries:
        tree.nearest(qx, qy, stats=stats)

    quadtree_nearest = time_repeats(lambda: [tree.nearest(qx, qy) for qx, qy in queries])
    quadtree_k = time_repeats(lambda: [tree.k_nearest(qx, qy, K) for qx, qy in queries])
    brute_nearest_timing = time_repeats(
        lambda: [brute_nearest(points, qx, qy) for qx, qy in queries]
    )
    brute_k_timing = time_repeats(
        lambda: [brute_k_nearest(points, qx, qy, K) for qx, qy in queries]
    )

    print(
        f"  {distribution_name:>9} n={size:>6}: "
        f"quadtree {quadtree_nearest.median_ms:8.2f} ms, "
        f"brute {brute_nearest_timing.median_ms:9.2f} ms, "
        f"checked {stats.points_checked / QUERY_COUNT:6.1f} pts"
    )

    return {
        "distribution": distribution_name,
        "n": size,
        "queries": QUERY_COUNT,
        "k": K,
        "build_median_ms": round(build_timing.median_ms, 4),
        "build_p95_ms": round(build_timing.p95_ms, 4),
        "quadtree_nearest_median_ms": round(quadtree_nearest.median_ms, 4),
        "quadtree_nearest_p95_ms": round(quadtree_nearest.p95_ms, 4),
        "brute_nearest_median_ms": round(brute_nearest_timing.median_ms, 4),
        "brute_nearest_p95_ms": round(brute_nearest_timing.p95_ms, 4),
        "quadtree_knearest_median_ms": round(quadtree_k.median_ms, 4),
        "quadtree_knearest_p95_ms": round(quadtree_k.p95_ms, 4),
        "brute_knearest_median_ms": round(brute_k_timing.median_ms, 4),
        "brute_knearest_p95_ms": round(brute_k_timing.p95_ms, 4),
        "mean_points_checked": round(stats.points_checked / QUERY_COUNT, 2),
        "mean_nodes_visited": round(stats.nodes_visited / QUERY_COUNT, 2),
    }


def run() -> list[dict[str, Any]]:
    """Measure every distribution at every size."""
    rows: list[dict[str, Any]] = []
    for distribution_name, generate in DISTRIBUTIONS.items():
        for size in SIZES:
            coordinates, bounds = generate(size, SEED)
            rows.append(measure(distribution_name, size, coordinates, bounds))
    return rows


def main() -> None:
    """Run the benchmark and write its CSV."""
    print("bench_nearest: quadtree against linear scan")
    environment = Environment.capture()
    rows = run()
    path = write_csv("bench_nearest.csv", rows, environment)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
