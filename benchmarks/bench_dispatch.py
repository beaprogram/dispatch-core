"""Dispatch strategies at the spec baseline and across a courier load sweep.

Two things are measured. The baseline is the documented default configuration, run
once per seed. The sweep varies courier count to find where the strategies actually
diverge, because a difference that only appears under load is worth knowing about
and a difference smaller than seed to seed noise is not a difference at all.

Both strategies see identical generated inputs for a given seed, so any difference
between them comes from the assignment decision and nothing else.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import networkx as nx

from benchmarks.common import Environment, write_csv
from dispatch_core.dispatcher import NEAREST, TOPK_ETA, Dispatcher
from dispatch_core.graph import load_graph
from dispatch_core.sim import (
    DEFAULT_ARRIVAL_RATE_PER_MINUTE,
    generate_couriers,
    generate_orders,
)

BASELINE_COURIERS = 200
ORDER_COUNT = 1_000
TOP_K = 5
SEEDS = (42, 43, 44, 45, 46)
SWEEP_COURIERS = (50, 100, 200, 400)
STRATEGIES = (NEAREST, TOPK_ETA)
FULL_GRAPH = Path(__file__).parent.parent / "data" / "cache" / "halifax_5000m.graphml"
FIXTURE_GRAPH = Path(__file__).parent.parent / "data" / "fixtures" / "downtown_small.graphml"


def one_run(
    graph: nx.MultiDiGraph, graph_name: str, couriers: int, strategy: str, seed: int
) -> dict[str, Any]:
    """Run one simulation and return its metrics as a CSV row.

    Orders and courier placements are generated from the seed before the strategy is
    chosen, so both strategies at a given seed see byte identical inputs.
    """
    orders = generate_orders(graph, ORDER_COUNT, seed)
    courier_nodes = generate_couriers(graph, couriers, seed)

    inter_arrival = orders[-1].created_at / (len(orders) - 1) if len(orders) > 1 else 0.0

    dispatcher = Dispatcher(graph, strategy=strategy, top_k=TOP_K)
    started = time.perf_counter()
    result = dispatcher.run(orders, courier_nodes, seed=seed)
    wall_seconds = time.perf_counter() - started

    return {
        "graph": graph_name,
        "couriers": couriers,
        "orders": ORDER_COUNT,
        "strategy": strategy,
        "seed": seed,
        "arrival_rate_per_minute": DEFAULT_ARRIVAL_RATE_PER_MINUTE,
        "mean_interarrival_s": round(inter_arrival, 3),
        "mean_wait_s": round(result.mean_wait_seconds, 2),
        "p95_wait_s": round(result.p95_wait_seconds, 2),
        "mean_delivery_s": round(result.mean_delivery_seconds, 2),
        "p95_delivery_s": round(result.p95_delivery_seconds, 2),
        "on_time_pct": round(result.on_time_percent, 3),
        "p50_decision_latency_ms": round(result.p50_decision_latency_ms, 5),
        "p95_decision_latency_ms": round(result.p95_decision_latency_ms, 5),
        "cache_hit_rate": round(result.cache_hit_rate, 5),
        "cache_hits": result.cache_hits,
        "cache_misses": result.cache_misses,
        "disagreement_rate": round(result.disagreement_rate, 5),
        "disagreements": len(result.disagreements),
        "mean_eta_saved_s": round(result.mean_eta_saved_seconds, 3),
        "rejected_s_per_m": round(result.mean_rejected_seconds_per_meter, 5),
        "chosen_s_per_m": round(result.mean_chosen_seconds_per_meter, 5),
        "wall_s": round(wall_seconds, 3),
    }


def run(seeds: tuple[int, ...] = SEEDS) -> list[dict[str, Any]]:
    """Run the baseline and the courier sweep for both strategies."""
    graph_path = FULL_GRAPH if FULL_GRAPH.exists() else FIXTURE_GRAPH
    graph_name = "full_5000m" if FULL_GRAPH.exists() else "fixture_800m"
    graph = load_graph(str(graph_path))

    print(f"  graph: {graph_name}, {graph.number_of_nodes()} nodes")
    print(f"  seeds: {seeds}, couriers swept over {SWEEP_COURIERS}")

    rows: list[dict[str, Any]] = []
    for couriers in SWEEP_COURIERS:
        for strategy in STRATEGIES:
            for seed in seeds:
                row = one_run(graph, graph_name, couriers, strategy, seed)
                row["is_baseline"] = couriers == BASELINE_COURIERS
                rows.append(row)
            recent = rows[-len(seeds) :]
            mean_wait = sum(r["mean_wait_s"] for r in recent) / len(recent)
            on_time = sum(r["on_time_pct"] for r in recent) / len(recent)
            print(
                f"  couriers={couriers:>4} {strategy:>9}: "
                f"mean wait {mean_wait:8.1f} s, on time {on_time:6.2f} %"
            )

    return rows


def main() -> None:
    """Run the benchmark and write its CSV."""
    print("bench_dispatch: strategy comparison at baseline and under a load sweep")
    environment = Environment.capture()
    started = time.perf_counter()
    rows = run()
    elapsed = time.perf_counter() - started
    path = write_csv("bench_dispatch.csv", rows, environment)
    print(f"wrote {path} in {elapsed:.1f} s")


if __name__ == "__main__":
    main()
