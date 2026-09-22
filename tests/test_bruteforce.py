"""Tests for the brute force baselines.

The baselines are the oracle the quadtree is checked against, so their own edge
case behaviour needs to match the quadtree's documented contract.
"""

import pytest

from dispatch_core.bruteforce import (
    brute_k_nearest,
    brute_nearest,
    brute_within_radius,
    distance,
)
from dispatch_core.quadtree import Point

POINTS = [Point(0, 0.0, 0.0), Point(1, 3.0, 4.0), Point(2, 10.0, 0.0)]


def test_brute_nearest_on_empty_list() -> None:
    """No points means no answer."""
    assert brute_nearest([], 0.0, 0.0) is None


def test_brute_nearest_finds_closest() -> None:
    """The closest point wins."""
    found = brute_nearest(POINTS, 9.0, 0.0)
    assert found is not None
    assert found.id == 2


def test_brute_k_nearest_with_k_zero() -> None:
    """Asking for nothing returns nothing, matching the quadtree."""
    assert brute_k_nearest(POINTS, 0.0, 0.0, 0) == []


def test_brute_k_nearest_with_negative_k_raises() -> None:
    """Negative k is rejected, matching the quadtree."""
    with pytest.raises(ValueError, match="k must be non-negative"):
        brute_k_nearest(POINTS, 0.0, 0.0, -1)


def test_brute_k_nearest_beyond_length_returns_all() -> None:
    """Over-asking returns every point, matching the quadtree."""
    assert len(brute_k_nearest(POINTS, 0.0, 0.0, 99)) == 3


def test_brute_within_radius_with_negative_radius_raises() -> None:
    """Negative radius is rejected, matching the quadtree."""
    with pytest.raises(ValueError, match="radius must be non-negative"):
        brute_within_radius(POINTS, 0.0, 0.0, -1.0)


def test_brute_within_radius_boundary_is_inclusive() -> None:
    """A point exactly at the radius counts as inside, matching the quadtree."""
    assert len(brute_within_radius(POINTS, 0.0, 0.0, 5.0)) == 2


def test_distance_is_euclidean() -> None:
    """The 3, 4, 5 triangle."""
    assert distance(Point(0, 3.0, 4.0), 0.0, 0.0) == pytest.approx(5.0)
