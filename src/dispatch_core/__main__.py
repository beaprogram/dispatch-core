"""Command line entry point for Dispatch Core."""

from __future__ import annotations

import argparse
import sys
import time

from dispatch_core.dispatcher import (
    DEFAULT_TOP_K,
    NEAREST,
    STRATEGIES,
    Dispatcher,
    SimulationResult,
)
from dispatch_core.graph import load_graph
from dispatch_core.sim import (
    DEFAULT_ARRIVAL_RATE_PER_MINUTE,
    generate_couriers,
    generate_orders,
)


def format_result(result: SimulationResult, wall_seconds: float) -> str:
    """Render a simulation result as an aligned plain text report."""
    lines = [
        "",
        f"Dispatch Core simulation: {result.strategy}",
        f"  orders {result.orders}, couriers {result.couriers}, seed {result.seed}",
        "",
        f"  assigned orders            {len(result.assignments)}",
        f"  mean wait                  {result.mean_wait_seconds:10.1f} s",
        f"  p95 wait                   {result.p95_wait_seconds:10.1f} s",
        f"  mean delivery              {result.mean_delivery_seconds:10.1f} s",
        f"  p95 delivery               {result.p95_delivery_seconds:10.1f} s",
        f"  on time                    {result.on_time_percent:10.2f} %",
        f"  p50 decision latency       {result.p50_decision_latency_ms:10.4f} ms",
        f"  p95 decision latency       {result.p95_decision_latency_ms:10.4f} ms",
        f"  route cache hit rate       {result.cache_hit_rate * 100:10.2f} %",
        f"  route cache hits/misses    {result.cache_hits} / {result.cache_misses}",
        "",
        f"  wall clock                 {wall_seconds:10.2f} s",
        "",
    ]
    return "\n".join(lines)


def run_simulate(args: argparse.Namespace) -> int:
    """Run one simulation and print its metrics."""
    graph = load_graph(args.graph)
    orders = generate_orders(graph, args.orders, args.seed, args.arrival_rate)
    couriers = generate_couriers(graph, args.couriers, args.seed)

    dispatcher = Dispatcher(graph, strategy=args.strategy, top_k=args.k)

    started = time.perf_counter()
    result = dispatcher.run(orders, couriers, seed=args.seed)
    wall_seconds = time.perf_counter() - started

    print(format_result(result, wall_seconds))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser."""
    parser = argparse.ArgumentParser(
        prog="dispatch_core",
        description="Delivery dispatch simulation over a road graph.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    simulate = subparsers.add_parser("simulate", help="Run a dispatch simulation")
    simulate.add_argument("--graph", required=True, help="Path to a GraphML road network")
    simulate.add_argument("--couriers", type=int, default=200, help="Number of couriers")
    simulate.add_argument("--orders", type=int, default=1000, help="Number of orders")
    simulate.add_argument("--seed", type=int, default=42, help="Random seed")
    simulate.add_argument(
        "--strategy", choices=STRATEGIES, default=NEAREST, help="Assignment strategy"
    )
    simulate.add_argument(
        "--k", type=int, default=DEFAULT_TOP_K, help="Candidates considered by topk-eta"
    )
    simulate.add_argument(
        "--arrival-rate",
        type=float,
        default=DEFAULT_ARRIVAL_RATE_PER_MINUTE,
        dest="arrival_rate",
        help="Mean order arrivals per minute",
    )
    simulate.set_defaults(handler=run_simulate)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = build_parser().parse_args(argv)
    handler = args.handler
    result: int = handler(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
