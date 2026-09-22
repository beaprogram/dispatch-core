"""Event-driven dispatcher over a discrete-event simulation.

Time is in seconds of simulated time throughout. The only wall clock measurement is
the dispatch decision latency, which is what a real operator would feel.

The simulation advances by popping an event heap, so nothing polls and no time step
is ever simulated where nothing happens.
"""

from __future__ import annotations

import itertools
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from heapq import heappop, heappush

import networkx as nx

from dispatch_core.geo import project_to_local
from dispatch_core.quadtree import Quadtree, bounds_from_points
from dispatch_core.routing import NodeId, astar

PICKUP_SERVICE_SECONDS = 120.0
DROPOFF_SERVICE_SECONDS = 60.0
DEADLINE_SECONDS = 45 * 60.0
DEFAULT_TOP_K = 5
DEFAULT_CACHE_SIZE = 100_000

NEAREST = "nearest"
TOPK_ETA = "topk-eta"
STRATEGIES = (NEAREST, TOPK_ETA)


class EventKind(Enum):
    """The two things that can happen in this simulation."""

    ORDER_CREATED = "ORDER_CREATED"
    COURIER_FREE = "COURIER_FREE"


@dataclass(frozen=True, slots=True)
class Order:
    """A delivery request."""

    id: int
    created_at: float
    pickup: NodeId
    dropoff: NodeId

    @property
    def deadline(self) -> float:
        """The time by which delivery counts as on time."""
        return self.created_at + DEADLINE_SECONDS


@dataclass(frozen=True, slots=True)
class Event:
    """A scheduled occurrence. Only the fields its kind needs are set."""

    kind: EventKind
    order_id: int | None = None
    courier_id: int | None = None
    node: NodeId | None = None


@dataclass(slots=True)
class Assignment:
    """The record of one order being given to one courier."""

    order_id: int
    courier_id: int
    created_at: float
    assigned_at: float
    delivered_at: float
    deadline: float

    @property
    def wait_seconds(self) -> float:
        """Time from the order being created to a courier being assigned."""
        return self.assigned_at - self.created_at

    @property
    def delivery_seconds(self) -> float:
        """Total time from the order being created to it being delivered."""
        return self.delivered_at - self.created_at

    @property
    def on_time(self) -> bool:
        """Whether delivery met the deadline."""
        return self.delivered_at <= self.deadline


class RouteCache:
    """A least recently used cache of travel times, keyed by node pair.

    Written explicitly rather than with functools.lru_cache so the hit rate is a
    first class metric rather than something recovered from cache_info.
    """

    def __init__(self, maxsize: int = DEFAULT_CACHE_SIZE) -> None:
        if maxsize < 1:
            raise ValueError(f"maxsize must be at least 1, got {maxsize}")
        self.maxsize = maxsize
        self.hits = 0
        self.misses = 0
        self._entries: OrderedDict[tuple[NodeId, NodeId], float] = OrderedDict()

    def __len__(self) -> int:
        """Number of cached entries.

        Time complexity: O(1)
        """
        return len(self._entries)

    @property
    def hit_rate(self) -> float:
        """Fraction of lookups served from cache, 0.0 when nothing was looked up."""
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def get_or_compute(self, source: NodeId, target: NodeId, compute: Callable[[], float]) -> float:
        """Return the cached travel time, computing and storing it on a miss.

        Time complexity: O(1) plus the cost of compute on a miss.
        """
        key = (source, target)
        cached = self._entries.get(key)
        if cached is not None:
            self.hits += 1
            self._entries.move_to_end(key)
            return cached

        self.misses += 1
        value = compute()
        self._entries[key] = value
        if len(self._entries) > self.maxsize:
            self._entries.popitem(last=False)
        return value


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank percentile of values, 0.0 for an empty list.

    Nearest-rank is used rather than interpolation so every reported number is an
    observation that actually occurred.

    Time complexity: O(n log n)
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


@dataclass(slots=True)
class SimulationResult:
    """Metrics from one completed simulation run."""

    assignments: list[Assignment]
    strategy: str
    orders: int
    couriers: int
    seed: int
    cache_hits: int
    cache_misses: int
    decision_latencies_ns: list[int] = field(default_factory=list)

    @property
    def mean_wait_seconds(self) -> float:
        """Mean time from order creation to assignment."""
        waits = [a.wait_seconds for a in self.assignments]
        return sum(waits) / len(waits) if waits else 0.0

    @property
    def p95_wait_seconds(self) -> float:
        """95th percentile time from order creation to assignment."""
        return percentile([a.wait_seconds for a in self.assignments], 0.95)

    @property
    def mean_delivery_seconds(self) -> float:
        """Mean time from order creation to delivery."""
        times = [a.delivery_seconds for a in self.assignments]
        return sum(times) / len(times) if times else 0.0

    @property
    def p95_delivery_seconds(self) -> float:
        """95th percentile time from order creation to delivery."""
        return percentile([a.delivery_seconds for a in self.assignments], 0.95)

    @property
    def on_time_percent(self) -> float:
        """Percentage of deliveries that met their deadline."""
        if not self.assignments:
            return 0.0
        return 100.0 * sum(a.on_time for a in self.assignments) / len(self.assignments)

    @property
    def p50_decision_latency_ms(self) -> float:
        """Median wall clock time to choose a courier, in milliseconds."""
        return percentile([float(n) for n in self.decision_latencies_ns], 0.50) / 1e6

    @property
    def p95_decision_latency_ms(self) -> float:
        """95th percentile wall clock time to choose a courier, in milliseconds."""
        return percentile([float(n) for n in self.decision_latencies_ns], 0.95) / 1e6

    @property
    def cache_hit_rate(self) -> float:
        """Fraction of travel time lookups served from cache."""
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total else 0.0


class Dispatcher:
    """Assigns orders to couriers over a discrete-event simulation.

    Free couriers live in the quadtree and are removed on assignment, so a nearest
    query only ever returns a courier that can actually take the job. A courier is
    reinserted at its dropoff when its COURIER_FREE event fires.

    Orders that arrive with no free courier wait in a heap keyed by
    (deadline, created_at, id), so the most urgent order is served first when
    capacity frees up.
    """

    def __init__(
        self,
        graph: nx.MultiDiGraph,
        strategy: str = NEAREST,
        top_k: int = DEFAULT_TOP_K,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        if strategy not in STRATEGIES:
            raise ValueError(f"strategy must be one of {STRATEGIES}, got {strategy!r}")
        if top_k < 1:
            raise ValueError(f"top_k must be at least 1, got {top_k}")

        self.graph = graph
        self.strategy = strategy
        self.top_k = top_k
        self.cache = RouteCache(cache_size)
        self._courier_nodes: dict[int, NodeId] = {}

        latitudes = [data["y"] for _, data in graph.nodes(data=True)]
        longitudes = [data["x"] for _, data in graph.nodes(data=True)]
        self._center_lat = (min(latitudes) + max(latitudes)) / 2
        self._center_lon = (min(longitudes) + max(longitudes)) / 2
        self._projected = {
            node: project_to_local(
                float(data["y"]), float(data["x"]), self._center_lat, self._center_lon
            )
            for node, data in graph.nodes(data=True)
        }
        self._bounds = bounds_from_points(list(self._projected.values()), margin=50.0)

    def travel_time(self, source: NodeId, target: NodeId) -> float:
        """Cached A* travel time in seconds between two nodes.

        Time complexity: O(1) on a hit, one A* search on a miss.
        """
        if source == target:
            return 0.0
        return self.cache.get_or_compute(
            source, target, lambda: astar(self.graph, source, target, weight="travel_time").cost
        )

    def _choose_courier(self, free: Quadtree, pickup: NodeId) -> int:
        """Pick a free courier for this pickup according to the strategy.

        nearest takes the straight-line closest courier, one quadtree query and no
        routing. topk-eta takes the k closest by straight line and then routes each
        one, trading k searches for a choice made on real road time.
        """
        x, y = self._projected[pickup]

        if self.strategy == NEAREST:
            found = free.nearest(x, y)
            assert found is not None
            return int(found.id)

        candidates = free.k_nearest(x, y, self.top_k)
        assert candidates
        best_id = int(candidates[0].id)
        best_eta = math.inf
        for candidate in candidates:
            courier_node = self._courier_nodes[int(candidate.id)]
            eta = self.travel_time(courier_node, pickup)
            if eta < best_eta:
                best_eta = eta
                best_id = int(candidate.id)
        return best_id

    def run(
        self, orders: list[Order], courier_start_nodes: dict[int, NodeId], seed: int
    ) -> SimulationResult:
        """Run the simulation to completion and return its metrics.

        Every order is assigned exactly once. The loop ends when the event heap is
        empty, which cannot happen while orders are still pending, because every
        assignment schedules the COURIER_FREE that will drain the next one.

        Time complexity: O(orders * (quadtree query + routing)) with routing cached.
        """
        self._courier_nodes = dict(courier_start_nodes)

        free = Quadtree(self._bounds)
        for courier_id, node in courier_start_nodes.items():
            x, y = self._projected[node]
            free.insert(courier_id, x, y)

        orders_by_id = {order.id: order for order in orders}
        sequence = itertools.count()
        events: list[tuple[float, int, Event]] = []
        for order in orders:
            heappush(
                events,
                (
                    order.created_at,
                    next(sequence),
                    Event(EventKind.ORDER_CREATED, order_id=order.id),
                ),
            )

        pending: list[tuple[float, float, int]] = []
        assignments: list[Assignment] = []
        latencies: list[int] = []

        while events:
            now, _, event = heappop(events)

            if event.kind == EventKind.ORDER_CREATED:
                assert event.order_id is not None
                order = orders_by_id[event.order_id]
                heappush(pending, (order.deadline, order.created_at, order.id))
            else:
                assert event.courier_id is not None and event.node is not None
                self._courier_nodes[event.courier_id] = event.node
                x, y = self._projected[event.node]
                free.insert(event.courier_id, x, y)

            while pending and len(free) > 0:
                _, _, order_id = heappop(pending)
                order = orders_by_id[order_id]

                started_ns = time.perf_counter_ns()
                courier_id = self._choose_courier(free, order.pickup)
                latencies.append(time.perf_counter_ns() - started_ns)

                free.remove(courier_id)
                courier_node = self._courier_nodes[courier_id]

                to_pickup = self.travel_time(courier_node, order.pickup)
                to_dropoff = self.travel_time(order.pickup, order.dropoff)
                delivered_at = (
                    now + to_pickup + PICKUP_SERVICE_SECONDS + to_dropoff + DROPOFF_SERVICE_SECONDS
                )

                assignments.append(
                    Assignment(
                        order_id=order.id,
                        courier_id=courier_id,
                        created_at=order.created_at,
                        assigned_at=now,
                        delivered_at=delivered_at,
                        deadline=order.deadline,
                    )
                )

                heappush(
                    events,
                    (
                        delivered_at,
                        next(sequence),
                        Event(
                            EventKind.COURIER_FREE,
                            courier_id=courier_id,
                            node=order.dropoff,
                        ),
                    ),
                )

        return SimulationResult(
            assignments=assignments,
            strategy=self.strategy,
            orders=len(orders),
            couriers=len(courier_start_nodes),
            seed=seed,
            cache_hits=self.cache.hits,
            cache_misses=self.cache.misses,
            decision_latencies_ns=latencies,
        )
