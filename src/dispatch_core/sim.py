"""Seeded generators for orders and couriers.

Every random draw comes from a single seeded generator, and the dispatcher itself
makes no random choices, so a seed fully determines a run's metrics.
"""

from __future__ import annotations

import random

import networkx as nx

from dispatch_core.dispatcher import Order
from dispatch_core.routing import NodeId

# Chosen so the default run is actually contended. By Little's law, 200 couriers
# with a mean delivery of about 12 minutes saturate at roughly 16.7 orders/minute.
# Measured on the full graph: 12/min leaves mean wait at 0.0 s and never exercises
# the pending queue, while 18/min gives mean wait 403.6 s and 97.5 percent on time.
DEFAULT_ARRIVAL_RATE_PER_MINUTE = 18.0


def generate_orders(
    graph: nx.MultiDiGraph,
    count: int,
    seed: int,
    arrival_rate_per_minute: float = DEFAULT_ARRIVAL_RATE_PER_MINUTE,
) -> list[Order]:
    """Generate orders with Poisson arrivals and random pickup and dropoff nodes.

    Poisson arrivals mean the gaps between orders are exponentially distributed, so
    orders cluster the way real demand does rather than arriving on a fixed cadence.

    Time complexity: O(count)
    """
    if count < 0:
        raise ValueError(f"count must be non-negative, got {count}")
    if arrival_rate_per_minute <= 0:
        raise ValueError(f"arrival_rate_per_minute must be positive, got {arrival_rate_per_minute}")

    rng = random.Random(seed)
    nodes = sorted(graph.nodes())
    if len(nodes) < 2:
        raise ValueError("Graph needs at least two nodes to generate orders")

    rate_per_second = arrival_rate_per_minute / 60.0
    orders = []
    clock = 0.0
    for order_id in range(count):
        clock += rng.expovariate(rate_per_second)
        pickup = rng.choice(nodes)
        dropoff = rng.choice(nodes)
        while dropoff == pickup:
            dropoff = rng.choice(nodes)
        orders.append(Order(id=order_id, created_at=clock, pickup=pickup, dropoff=dropoff))

    return orders


def generate_couriers(graph: nx.MultiDiGraph, count: int, seed: int) -> dict[int, NodeId]:
    """Place couriers at random graph nodes, returning courier id to start node.

    Uses a generator seeded differently from orders so that changing the courier
    count does not shift the order stream.

    Time complexity: O(count)
    """
    if count < 1:
        raise ValueError(f"count must be at least 1, got {count}")

    rng = random.Random(seed + 1)
    nodes = sorted(graph.nodes())
    return {courier_id: rng.choice(nodes) for courier_id in range(count)}
