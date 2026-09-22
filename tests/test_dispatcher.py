"""Tests for the dispatcher, its cache, and the simulation invariants.

These run against the committed fixture so they stay offline and fast.
"""

import math
import random
from pathlib import Path

import networkx as nx
import pytest

from dispatch_core.dispatcher import (
    DEADLINE_SECONDS,
    DROPOFF_SERVICE_SECONDS,
    NEAREST,
    PICKUP_SERVICE_SECONDS,
    TOPK_ETA,
    Dispatcher,
    Order,
    RouteCache,
    percentile,
)
from dispatch_core.graph import load_graph
from dispatch_core.quadtree import Quadtree
from dispatch_core.routing import astar
from dispatch_core.sim import generate_couriers, generate_orders

FIXTURE_PATH = Path(__file__).parent.parent / "data" / "fixtures" / "downtown_small.graphml"
SEED = 42
ORDER_COUNT = 120
COURIER_COUNT = 12
STRATEGIES = [NEAREST, TOPK_ETA]


@pytest.fixture(scope="module")
def graph() -> nx.MultiDiGraph:
    """The committed fixture graph, loaded once."""
    return load_graph(str(FIXTURE_PATH))


def run(
    graph: nx.MultiDiGraph, strategy: str = NEAREST, seed: int = SEED, orders: int = ORDER_COUNT
):  # type: ignore[no-untyped-def]
    """Run a small simulation and return its result."""
    generated = generate_orders(graph, orders, seed)
    couriers = generate_couriers(graph, COURIER_COUNT, seed)
    return Dispatcher(graph, strategy=strategy).run(generated, couriers, seed=seed)


# Determinism


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_same_seed_gives_identical_results(graph: nx.MultiDiGraph, strategy: str) -> None:
    """A seed fully determines the run, because the dispatcher makes no random choices."""
    first = run(graph, strategy)
    second = run(graph, strategy)

    assert first.assignments == second.assignments
    assert first.cache_hits == second.cache_hits
    assert first.cache_misses == second.cache_misses
    assert first.on_time_percent == second.on_time_percent
    assert first.mean_wait_seconds == second.mean_wait_seconds


def test_different_seeds_give_different_results(graph: nx.MultiDiGraph) -> None:
    """The seed genuinely varies the run, so determinism is not just a frozen constant."""
    assert run(graph, NEAREST, seed=1).assignments != run(graph, NEAREST, seed=2).assignments


def test_generators_are_deterministic(graph: nx.MultiDiGraph) -> None:
    """Order and courier generation repeat exactly for a given seed."""
    assert generate_orders(graph, 50, SEED) == generate_orders(graph, 50, SEED)
    assert generate_couriers(graph, 10, SEED) == generate_couriers(graph, 10, SEED)


# Core simulation invariants


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_every_order_assigned_exactly_once(graph: nx.MultiDiGraph, strategy: str) -> None:
    """No order is dropped and none is assigned twice."""
    result = run(graph, strategy)
    assigned_ids = [a.order_id for a in result.assignments]

    assert len(assigned_ids) == ORDER_COUNT
    assert len(set(assigned_ids)) == ORDER_COUNT
    assert set(assigned_ids) == set(range(ORDER_COUNT))


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_no_courier_holds_two_orders_at_once(graph: nx.MultiDiGraph, strategy: str) -> None:
    """A courier's assignments never overlap in time."""
    result = run(graph, strategy)

    by_courier: dict[int, list[tuple[float, float]]] = {}
    for assignment in result.assignments:
        by_courier.setdefault(assignment.courier_id, []).append(
            (assignment.assigned_at, assignment.delivered_at)
        )

    for courier_id, intervals in by_courier.items():
        intervals.sort()
        for (_, earlier_end), (later_start, _) in zip(intervals, intervals[1:], strict=False):
            assert later_start >= earlier_end, (
                f"courier {courier_id} was given an order at {later_start} "
                f"while still busy until {earlier_end}"
            )


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_assigned_at_is_never_before_created_at(graph: nx.MultiDiGraph, strategy: str) -> None:
    """No order is assigned before it exists."""
    for assignment in run(graph, strategy).assignments:
        assert assignment.assigned_at >= assignment.created_at
        assert assignment.wait_seconds >= 0.0


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_delivery_follows_assignment(graph: nx.MultiDiGraph, strategy: str) -> None:
    """Delivery is always after assignment by at least the two service times."""
    minimum = PICKUP_SERVICE_SECONDS + DROPOFF_SERVICE_SECONDS
    for assignment in run(graph, strategy).assignments:
        assert assignment.delivered_at >= assignment.assigned_at + minimum


def test_deadline_is_created_at_plus_forty_five_minutes() -> None:
    """The deadline rule is 45 minutes from creation."""
    order = Order(id=0, created_at=100.0, pickup=1, dropoff=2)
    assert order.deadline == 100.0 + DEADLINE_SECONDS
    assert DEADLINE_SECONDS == 2700.0


def test_pending_orders_pop_in_deadline_order(graph: nx.MultiDiGraph) -> None:
    """The pending heap is ordered by deadline, so the most urgent order goes first.

    Run with one courier so that orders genuinely queue, then confirm that among
    orders which were already waiting, the one with the earliest deadline was taken
    next. Because every order here shares the same 45 minute window, deadline order
    is creation order.
    """
    orders = generate_orders(graph, 40, SEED)
    couriers = generate_couriers(graph, 1, SEED)
    result = Dispatcher(graph, strategy=NEAREST).run(orders, couriers, seed=SEED)

    by_id = {order.id: order for order in orders}
    ordered = sorted(result.assignments, key=lambda a: a.assigned_at)

    assert any(a.wait_seconds > 0 for a in ordered), "expected queueing with one courier"

    for earlier, later in zip(ordered, ordered[1:], strict=False):
        if by_id[later.order_id].created_at <= earlier.assigned_at:
            assert by_id[earlier.order_id].deadline <= by_id[later.order_id].deadline, (
                f"order {later.order_id} was waiting with an earlier deadline than "
                f"order {earlier.order_id}, which was served first"
            )


def test_a_single_courier_still_serves_every_order(graph: nx.MultiDiGraph) -> None:
    """The queue drains completely even at minimum capacity."""
    orders = generate_orders(graph, 30, SEED)
    couriers = generate_couriers(graph, 1, SEED)
    result = Dispatcher(graph, strategy=NEAREST).run(orders, couriers, seed=SEED)

    assert len(result.assignments) == 30
    assert len({a.courier_id for a in result.assignments}) == 1


# Strategy behaviour


def test_topk_eta_picks_the_lowest_routed_eta_among_candidates(
    graph: nx.MultiDiGraph,
) -> None:
    """The chosen courier has the lowest road travel time of the k straight line candidates.

    Checks the decision itself rather than that a run completed, by rebuilding the
    candidate set the dispatcher saw and routing every one of them independently.
    """
    top_k = 5
    dispatcher = Dispatcher(graph, strategy=TOPK_ETA, top_k=top_k)
    courier_nodes = generate_couriers(graph, COURIER_COUNT, SEED)

    free = Quadtree(dispatcher._bounds)
    for courier_id, node in courier_nodes.items():
        x, y = dispatcher._projected[node]
        free.insert(courier_id, x, y)
    dispatcher._courier_nodes = dict(courier_nodes)

    rng = random.Random(SEED)
    checked = 0
    for pickup in rng.sample(sorted(graph.nodes()), 30):
        chosen = dispatcher._choose_courier(free, pickup)

        x, y = dispatcher._projected[pickup]
        candidates = [int(p.id) for p in free.k_nearest(x, y, top_k)]
        assert chosen in candidates, "chose a courier outside the candidate set"

        etas = {
            candidate: astar(graph, courier_nodes[candidate], pickup, "travel_time").cost
            if courier_nodes[candidate] != pickup
            else 0.0
            for candidate in candidates
        }
        assert etas[chosen] == pytest.approx(min(etas.values()))
        checked += 1

    assert checked == 30


def test_nearest_picks_the_closest_courier_by_straight_line(graph: nx.MultiDiGraph) -> None:
    """The nearest strategy chooses by projected distance, not by road time."""
    dispatcher = Dispatcher(graph, strategy=NEAREST)
    courier_nodes = generate_couriers(graph, COURIER_COUNT, SEED)

    free = Quadtree(dispatcher._bounds)
    for courier_id, node in courier_nodes.items():
        x, y = dispatcher._projected[node]
        free.insert(courier_id, x, y)
    dispatcher._courier_nodes = dict(courier_nodes)

    rng = random.Random(SEED)
    for pickup in rng.sample(sorted(graph.nodes()), 30):
        chosen = dispatcher._choose_courier(free, pickup)
        x, y = dispatcher._projected[pickup]

        chosen_x, chosen_y = dispatcher._projected[courier_nodes[chosen]]
        best = min(
            math.hypot(dispatcher._projected[n][0] - x, dispatcher._projected[n][1] - y)
            for n in courier_nodes.values()
        )
        assert math.hypot(chosen_x - x, chosen_y - y) == pytest.approx(best)


def test_unknown_strategy_raises(graph: nx.MultiDiGraph) -> None:
    """An unsupported strategy is rejected at construction."""
    with pytest.raises(ValueError, match="strategy must be one of"):
        Dispatcher(graph, strategy="random")


def test_invalid_top_k_raises(graph: nx.MultiDiGraph) -> None:
    """topk-eta needs at least one candidate."""
    with pytest.raises(ValueError, match="top_k must be at least 1"):
        Dispatcher(graph, strategy=TOPK_ETA, top_k=0)


# Route cache


def test_cache_returns_the_stored_value_without_recomputing() -> None:
    """A second lookup of the same pair is a hit and does not call compute again."""
    cache = RouteCache(maxsize=10)
    calls = []

    def compute() -> float:
        calls.append(1)
        return 42.0

    assert cache.get_or_compute(1, 2, compute) == 42.0
    assert cache.get_or_compute(1, 2, compute) == 42.0
    assert len(calls) == 1
    assert cache.hits == 1
    assert cache.misses == 1
    assert cache.hit_rate == pytest.approx(0.5)


def test_cache_evicts_least_recently_used() -> None:
    """At capacity the oldest untouched entry is dropped, not the oldest inserted."""
    cache = RouteCache(maxsize=2)
    cache.get_or_compute(1, 2, lambda: 1.0)
    cache.get_or_compute(3, 4, lambda: 2.0)
    cache.get_or_compute(1, 2, lambda: 99.0)
    cache.get_or_compute(5, 6, lambda: 3.0)

    assert len(cache) == 2
    assert cache.get_or_compute(1, 2, lambda: 99.0) == 1.0, "recently used entry survived"
    assert cache.get_or_compute(3, 4, lambda: 7.0) == 7.0, "untouched entry was evicted"


def test_cache_hit_rate_is_zero_when_unused() -> None:
    """An untouched cache reports zero rather than dividing by zero."""
    assert RouteCache().hit_rate == 0.0


def test_cache_rejects_invalid_size() -> None:
    """A cache must be able to hold at least one entry."""
    with pytest.raises(ValueError, match="maxsize must be at least 1"):
        RouteCache(maxsize=0)


def test_cached_travel_time_matches_direct_routing(graph: nx.MultiDiGraph) -> None:
    """Caching must not change the answer."""
    dispatcher = Dispatcher(graph, strategy=NEAREST)
    rng = random.Random(SEED)
    nodes = sorted(graph.nodes())

    for _ in range(25):
        source, target = rng.choice(nodes), rng.choice(nodes)
        expected = 0.0 if source == target else astar(graph, source, target, "travel_time").cost
        assert dispatcher.travel_time(source, target) == pytest.approx(expected)
        assert dispatcher.travel_time(source, target) == pytest.approx(expected)


def test_travel_time_to_self_is_zero(graph: nx.MultiDiGraph) -> None:
    """A courier already at the pickup needs no travel time and no cache entry."""
    dispatcher = Dispatcher(graph, strategy=NEAREST)
    node = next(iter(graph.nodes()))
    assert dispatcher.travel_time(node, node) == 0.0
    assert len(dispatcher.cache) == 0


# Metrics


def test_percentile_uses_nearest_rank() -> None:
    """Every reported percentile is a value that actually occurred."""
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    assert percentile(values, 0.50) == 5.0
    assert percentile(values, 0.95) == 10.0
    assert percentile([], 0.95) == 0.0
    assert percentile([7.0], 0.95) == 7.0


def test_metrics_are_internally_consistent(graph: nx.MultiDiGraph) -> None:
    """Reported aggregates agree with the underlying assignment records."""
    result = run(graph, NEAREST)

    waits = [a.wait_seconds for a in result.assignments]
    assert result.mean_wait_seconds == pytest.approx(sum(waits) / len(waits))
    assert result.p95_wait_seconds >= result.mean_wait_seconds
    assert 0.0 <= result.on_time_percent <= 100.0
    assert 0.0 <= result.cache_hit_rate <= 1.0
    assert len(result.decision_latencies_ns) == len(result.assignments)


def test_on_time_matches_deadline_comparison(graph: nx.MultiDiGraph) -> None:
    """The on time percentage counts exactly the assignments that met their deadline."""
    result = run(graph, NEAREST)
    expected = (
        100.0
        * sum(a.delivered_at <= a.deadline for a in result.assignments)
        / len(result.assignments)
    )
    assert result.on_time_percent == pytest.approx(expected)


# Generators


def test_order_arrival_times_are_increasing(graph: nx.MultiDiGraph) -> None:
    """Poisson arrivals accumulate, so creation times are non decreasing."""
    orders = generate_orders(graph, 200, SEED)
    for earlier, later in zip(orders, orders[1:], strict=False):
        assert later.created_at >= earlier.created_at


def test_order_pickup_and_dropoff_differ(graph: nx.MultiDiGraph) -> None:
    """A delivery that starts and ends at the same node is not a delivery."""
    for order in generate_orders(graph, 200, SEED):
        assert order.pickup != order.dropoff


def test_generators_reject_invalid_counts(graph: nx.MultiDiGraph) -> None:
    """Counts are validated rather than silently producing an empty run."""
    with pytest.raises(ValueError, match="count must be non-negative"):
        generate_orders(graph, -1, SEED)
    with pytest.raises(ValueError, match="count must be at least 1"):
        generate_couriers(graph, 0, SEED)
    with pytest.raises(ValueError, match="arrival_rate_per_minute must be positive"):
        generate_orders(graph, 10, SEED, arrival_rate_per_minute=0.0)


def test_empty_run_reports_zeros_rather_than_dividing_by_zero(
    graph: nx.MultiDiGraph,
) -> None:
    """A run with no orders reports zeros for every aggregate."""
    result = Dispatcher(graph, strategy=NEAREST).run([], generate_couriers(graph, 2, SEED), SEED)

    assert result.assignments == []
    assert result.on_time_percent == 0.0
    assert result.mean_wait_seconds == 0.0
    assert result.p95_wait_seconds == 0.0
    assert result.mean_delivery_seconds == 0.0
    assert result.cache_hit_rate == 0.0


def test_order_generation_needs_two_distinct_nodes() -> None:
    """A single node graph cannot produce a pickup and a dropoff that differ."""
    tiny = nx.MultiDiGraph()
    tiny.add_node(1, x=-63.58, y=44.64)
    with pytest.raises(ValueError, match="at least two nodes"):
        generate_orders(tiny, 5, SEED)


def test_pickup_and_dropoff_collisions_are_resampled() -> None:
    """On a two node graph every draw collides half the time and must be retried."""
    two = nx.MultiDiGraph()
    two.add_node(1, x=-63.58, y=44.64)
    two.add_node(2, x=-63.57, y=44.64)

    orders = generate_orders(two, 50, SEED)
    assert len(orders) == 50
    for order in orders:
        assert order.pickup != order.dropoff
