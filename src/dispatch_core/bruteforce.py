"""Linear scan nearest neighbour baselines.

These exist to be obviously correct rather than fast. The quadtree is checked
against them in property tests, and benchmarked against them in CP5. Keeping the
implementation dumb is the point: an oracle that shares logic with the thing it
checks proves nothing.
"""

import math

from dispatch_core.quadtree import Point


def brute_nearest(points: list[Point], x: float, y: float) -> Point | None:
    """Nearest point to x, y by linear scan, or None if points is empty.

    Time complexity: O(n)
    """
    best: Point | None = None
    best_distance_squared = float("inf")

    for point in points:
        distance_squared = (point.x - x) ** 2 + (point.y - y) ** 2
        if distance_squared < best_distance_squared:
            best_distance_squared = distance_squared
            best = point

    return best


def brute_k_nearest(points: list[Point], x: float, y: float, k: int) -> list[Point]:
    """The k nearest points to x, y by full sort, nearest first.

    Returns every point when k exceeds len(points). Raises ValueError for negative k.

    Time complexity: O(n log n)
    """
    if k < 0:
        raise ValueError(f"k must be non-negative, got {k}")
    if k == 0:
        return []

    ordered = sorted(points, key=lambda p: (p.x - x) ** 2 + (p.y - y) ** 2)
    return ordered[:k]


def brute_within_radius(points: list[Point], x: float, y: float, radius: float) -> list[Point]:
    """Every point within radius of x, y by linear scan, nearest first.

    Raises ValueError for a negative radius.

    Time complexity: O(n log n) because the result is sorted
    """
    if radius < 0:
        raise ValueError(f"radius must be non-negative, got {radius}")

    radius_squared = radius * radius
    inside = [p for p in points if (p.x - x) ** 2 + (p.y - y) ** 2 <= radius_squared]
    return sorted(inside, key=lambda p: (p.x - x) ** 2 + (p.y - y) ** 2)


def distance(point: Point, x: float, y: float) -> float:
    """Euclidean distance from point to x, y.

    Time complexity: O(1)
    """
    return math.hypot(point.x - x, point.y - y)
