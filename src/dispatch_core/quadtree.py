"""Dynamic point region quadtree with best-first nearest neighbour search.

Points live in flat local meters produced by geo.project_to_local, so distances
here are plain Euclidean. All internal comparisons use squared distance to avoid
a square root per candidate; square roots are taken only on the values returned.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from heapq import heappop, heappush, heapreplace

PointId = int | str

DEFAULT_CAPACITY = 8
DEFAULT_MAX_DEPTH = 16


@dataclass(frozen=True, slots=True)
class Point:
    """A point identified by id, positioned at x, y in local meters."""

    id: PointId
    x: float
    y: float


@dataclass(slots=True)
class SearchStats:
    """Counters filled in by an instrumented search.

    nodes_visited counts quadtree nodes popped from the search frontier.
    points_checked counts individual points whose distance was computed.
    Comparing points_checked against len(tree) shows how much the search pruned.
    """

    nodes_visited: int = 0
    points_checked: int = 0


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """An axis aligned box, half open on the maximum edges.

    A point belongs to the box when min_x <= x < max_x and min_y <= y < max_y.
    Half open intervals let the four child boxes tile the parent exactly, so a
    point on an internal boundary lands in exactly one child.
    """

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def contains(self, x: float, y: float) -> bool:
        """Return whether x, y lies in this half open box.

        Time complexity: O(1)
        """
        return self.min_x <= x < self.max_x and self.min_y <= y < self.max_y

    def min_distance_squared(self, x: float, y: float) -> float:
        """Squared distance from x, y to the nearest point of this box, 0 if inside.

        This is the lower bound that drives best-first pruning: no point inside the
        box can be closer to the query than this.

        Time complexity: O(1)
        """
        dx = max(self.min_x - x, 0.0, x - self.max_x)
        dy = max(self.min_y - y, 0.0, y - self.max_y)
        return dx * dx + dy * dy

    def quadrants(self) -> tuple[BoundingBox, BoundingBox, BoundingBox, BoundingBox]:
        """Split into four child boxes ordered SW, SE, NW, NE.

        The children tile this box exactly under the half open rule.

        Time complexity: O(1)
        """
        mid_x = (self.min_x + self.max_x) / 2
        mid_y = (self.min_y + self.max_y) / 2
        return (
            BoundingBox(self.min_x, self.min_y, mid_x, mid_y),
            BoundingBox(mid_x, self.min_y, self.max_x, mid_y),
            BoundingBox(self.min_x, mid_y, mid_x, self.max_y),
            BoundingBox(mid_x, mid_y, self.max_x, self.max_y),
        )

    def quadrant_index(self, x: float, y: float) -> int:
        """Index of the child quadrant containing x, y, matching quadrants() order.

        Uses the same midpoint expression as quadrants(), so routing and tiling
        cannot disagree.

        Time complexity: O(1)
        """
        mid_x = (self.min_x + self.max_x) / 2
        mid_y = (self.min_y + self.max_y) / 2
        return (2 if y >= mid_y else 0) + (1 if x >= mid_x else 0)


def bounds_from_points(coordinates: list[tuple[float, float]], margin: float = 1.0) -> BoundingBox:
    """Bounding box covering every coordinate, padded by margin on all sides.

    The margin matters because the box is half open: without it a point sitting
    exactly on the maximum edge would fall outside the root and be rejected.

    Time complexity: O(n)
    """
    if not coordinates:
        raise ValueError("Cannot build bounds from an empty coordinate list")
    if margin <= 0:
        raise ValueError(f"margin must be positive, got {margin}")

    xs = [x for x, _ in coordinates]
    ys = [y for _, y in coordinates]
    return BoundingBox(min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


@dataclass(slots=True)
class _Node:
    """A quadtree node: a leaf holding points, or an internal node with 4 children."""

    box: BoundingBox
    depth: int
    count: int = 0
    points: list[Point] | None = field(default_factory=list)
    children: list[_Node] | None = None

    @property
    def is_leaf(self) -> bool:
        """Whether this node stores points directly."""
        return self.children is None


class Quadtree:
    """A dynamic point region quadtree supporting insert, remove, move, and search.

    Leaves hold up to capacity points and split when they exceed it, down to
    max_depth. Beyond max_depth a leaf simply grows, which is what keeps a pile of
    identical coordinates from recursing forever.
    """

    def __init__(
        self,
        bounds: BoundingBox,
        capacity: int = DEFAULT_CAPACITY,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be at least 1, got {capacity}")
        if max_depth < 0:
            raise ValueError(f"max_depth must be non-negative, got {max_depth}")

        self.bounds = bounds
        self.capacity = capacity
        self.max_depth = max_depth
        self._root = _Node(box=bounds, depth=0)
        self._index: dict[PointId, Point] = {}
        self._tiebreak = itertools.count()

    def __len__(self) -> int:
        """Number of points stored.

        Time complexity: O(1)
        """
        return len(self._index)

    def __contains__(self, point_id: PointId) -> bool:
        """Whether point_id is stored.

        Time complexity: O(1)
        """
        return point_id in self._index

    def insert(self, point_id: PointId, x: float, y: float) -> None:
        """Insert a point.

        Raises ValueError if point_id already exists or x, y falls outside the root
        bounds. Duplicate coordinates are allowed, duplicate ids are not.

        Time complexity: O(depth) average, O(max_depth) worst case.
        """
        if point_id in self._index:
            raise ValueError(f"Point id {point_id!r} already exists")
        if not self.bounds.contains(x, y):
            raise ValueError(
                f"Point ({x}, {y}) lies outside tree bounds {self.bounds}. "
                "Bounds are half open on the maximum edges."
            )

        point = Point(point_id, x, y)
        self._index[point_id] = point
        self._insert_into(self._root, point)

    def _insert_into(self, node: _Node, point: Point) -> None:
        """Descend to the owning leaf, appending the point and splitting if needed."""
        node.count += 1
        if node.points is not None:
            node.points.append(point)
            if len(node.points) > self.capacity and node.depth < self.max_depth:
                self._subdivide(node)
            return

        assert node.children is not None
        self._insert_into(node.children[node.box.quadrant_index(point.x, point.y)], point)

    def _subdivide(self, node: _Node) -> None:
        """Turn a leaf into an internal node, pushing its points down one level."""
        assert node.points is not None
        boxes = node.box.quadrants()
        children = [_Node(box=box, depth=node.depth + 1) for box in boxes]

        for point in node.points:
            child = children[node.box.quadrant_index(point.x, point.y)]
            assert child.points is not None
            child.points.append(point)
            child.count += 1

        node.points = None
        node.children = children

        for child in children:
            assert child.points is not None
            if len(child.points) > self.capacity and child.depth < self.max_depth:
                self._subdivide(child)

    def remove(self, point_id: PointId) -> bool:
        """Remove a point by id.

        Returns True if it was present, False for an unknown id. Collapses internal
        nodes back into a leaf once their subtree holds capacity points or fewer.

        Time complexity: O(depth) average. Worst case O(max_depth + m), where m is
        the size of the target leaf: the descent is bounded by max_depth, but the
        leaf is then filtered in full. Coincident points pile up in one leaf at
        max_depth, where m reaches n.
        """
        point = self._index.pop(point_id, None)
        if point is None:
            return False

        self._remove_from(self._root, point)
        return True

    def _remove_from(self, node: _Node, point: Point) -> None:
        """Remove point from this subtree, collapsing internal nodes on the way up.

        remove() has already confirmed the id is stored and the point carries the
        coordinates it was indexed under, so the descent always reaches it.
        """
        node.count -= 1

        if node.points is not None:
            node.points = [held for held in node.points if held.id != point.id]
            return

        assert node.children is not None
        self._remove_from(node.children[node.box.quadrant_index(point.x, point.y)], point)
        if node.count <= self.capacity:
            self._collapse(node)

    def _collapse(self, node: _Node) -> None:
        """Replace an internal node's subtree with a single leaf holding its points."""
        gathered: list[Point] = []
        stack = [node]
        while stack:
            current = stack.pop()
            if current.points is not None:
                gathered.extend(current.points)
            else:
                assert current.children is not None
                stack.extend(current.children)

        node.children = None
        node.points = gathered

    def move(self, point_id: PointId, x: float, y: float) -> bool:
        """Move a point to a new position, as a remove then an insert.

        Returns True if the point existed. Bounds are checked before anything is
        removed, so a rejected move leaves the tree untouched.

        Time complexity: same as remove: O(depth) average, worst case
        O(max_depth + m) for a target leaf of m points.
        """
        if point_id not in self._index:
            return False
        if not self.bounds.contains(x, y):
            raise ValueError(
                f"Point ({x}, {y}) lies outside tree bounds {self.bounds}. "
                "Bounds are half open on the maximum edges."
            )

        self.remove(point_id)
        self.insert(point_id, x, y)
        return True

    def nearest(self, x: float, y: float, stats: SearchStats | None = None) -> Point | None:
        """Nearest stored point to x, y, or None if the tree is empty.

        Best-first search: a frontier heap ordered by each node's minimum possible
        distance to the query. Once the closest unexplored box is farther than the
        best point found so far, nothing left can improve on it and the search stops.

        Time complexity: O(log n) average for evenly spread points, O(n) worst case.

        Two shapes hit the worst case. Coincident points pile into one leaf at
        max_depth, which is then scanned in full. A query at the center of a ring of
        equidistant points defeats pruning differently: every box holding ring points
        straddles the query side of the ring, so its lower bound falls below the best
        distance and none can be discarded. Measured on a ring of 8,000 points, a
        single nearest query checked 7,998 of them.
        """
        if not self._index:
            return None

        best: Point | None = None
        best_distance_squared = math.inf

        frontier: list[tuple[float, int, _Node]] = [
            (self._root.box.min_distance_squared(x, y), next(self._tiebreak), self._root)
        ]

        while frontier:
            bound, _, node = heappop(frontier)
            if bound >= best_distance_squared:
                break

            if stats is not None:
                stats.nodes_visited += 1

            if node.points is not None:
                for point in node.points:
                    distance_squared = (point.x - x) ** 2 + (point.y - y) ** 2
                    if distance_squared < best_distance_squared:
                        best_distance_squared = distance_squared
                        best = point
                if stats is not None:
                    stats.points_checked += len(node.points)
                continue

            assert node.children is not None
            for child in node.children:
                if child.count == 0:
                    continue
                child_bound = child.box.min_distance_squared(x, y)
                if child_bound < best_distance_squared:
                    heappush(frontier, (child_bound, next(self._tiebreak), child))

        return best

    def k_nearest(
        self, x: float, y: float, k: int, stats: SearchStats | None = None
    ) -> list[Point]:
        """The k nearest stored points to x, y, sorted nearest first.

        Returns every point when k exceeds the number stored, and an empty list when
        k is 0. Raises ValueError for negative k.

        Same best-first strategy as nearest, but the pruning threshold is the kth
        best distance so far, held in a max-heap of size k.

        Time complexity: O(k log n) average for evenly spread points, O(n log k)
        worst case.
        """
        if k < 0:
            raise ValueError(f"k must be non-negative, got {k}")
        if k == 0 or not self._index:
            return []

        # Max-heap by negated squared distance, so results[0] is the current kth best.
        results: list[tuple[float, int, Point]] = []

        frontier: list[tuple[float, int, _Node]] = [
            (self._root.box.min_distance_squared(x, y), next(self._tiebreak), self._root)
        ]

        while frontier:
            bound, _, node = heappop(frontier)
            if len(results) == k and bound >= -results[0][0]:
                break

            if stats is not None:
                stats.nodes_visited += 1

            if node.points is not None:
                for point in node.points:
                    distance_squared = (point.x - x) ** 2 + (point.y - y) ** 2
                    if len(results) < k:
                        heappush(results, (-distance_squared, next(self._tiebreak), point))
                    elif distance_squared < -results[0][0]:
                        heapreplace(results, (-distance_squared, next(self._tiebreak), point))
                if stats is not None:
                    stats.points_checked += len(node.points)
                continue

            assert node.children is not None
            threshold = -results[0][0] if len(results) == k else math.inf
            for child in node.children:
                if child.count == 0:
                    continue
                child_bound = child.box.min_distance_squared(x, y)
                if child_bound < threshold:
                    heappush(frontier, (child_bound, next(self._tiebreak), child))

        return [point for negated, _, point in sorted(results, key=lambda r: -r[0])]

    def within_radius(self, x: float, y: float, radius: float) -> list[Point]:
        """Every stored point within radius of x, y, sorted nearest first.

        Raises ValueError for a negative radius.

        Time complexity: O(log n + m) average for m reported points, O(n) worst case.
        """
        if radius < 0:
            raise ValueError(f"radius must be non-negative, got {radius}")

        radius_squared = radius * radius
        found: list[tuple[float, Point]] = []
        stack: list[_Node] = [self._root]

        while stack:
            node = stack.pop()
            if node.count == 0 or node.box.min_distance_squared(x, y) > radius_squared:
                continue

            if node.points is not None:
                for point in node.points:
                    distance_squared = (point.x - x) ** 2 + (point.y - y) ** 2
                    if distance_squared <= radius_squared:
                        found.append((distance_squared, point))
            else:
                assert node.children is not None
                stack.extend(node.children)

        found.sort(key=lambda pair: pair[0])
        return [point for _, point in found]
