"""Tests for the quadtree.

Correctness is checked against bruteforce.py, which is written independently and
scans linearly. Distances are compared rather than ids, because points equidistant
from the query are all valid answers and the two implementations may break the tie
differently.
"""

import math
import random

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dispatch_core.bruteforce import (
    brute_k_nearest,
    brute_nearest,
    brute_within_radius,
    distance,
)
from dispatch_core.quadtree import (
    BoundingBox,
    Point,
    Quadtree,
    SearchStats,
    bounds_from_points,
)

COORDINATE_LIMIT = 1_000.0
TEST_BOUNDS = BoundingBox(
    -COORDINATE_LIMIT - 1, -COORDINATE_LIMIT - 1, COORDINATE_LIMIT + 1, COORDINATE_LIMIT + 1
)

coordinates = st.floats(
    min_value=-COORDINATE_LIMIT,
    max_value=COORDINATE_LIMIT,
    allow_nan=False,
    allow_infinity=False,
)
coordinate_pairs = st.lists(st.tuples(coordinates, coordinates), min_size=0, max_size=200)


def build_tree(pairs: list[tuple[float, float]], **kwargs: int) -> tuple[Quadtree, list[Point]]:
    """Build a tree and the matching point list for the brute force oracle."""
    tree = Quadtree(TEST_BOUNDS, **kwargs)
    points = []
    for i, (x, y) in enumerate(pairs):
        tree.insert(i, x, y)
        points.append(Point(i, x, y))
    return tree, points


# Bounding box geometry


def test_box_is_half_open_on_maximum_edges() -> None:
    """The minimum edge is inside the box, the maximum edge is not."""
    box = BoundingBox(0.0, 0.0, 10.0, 10.0)
    assert box.contains(0.0, 0.0)
    assert not box.contains(10.0, 5.0)
    assert not box.contains(5.0, 10.0)
    assert not box.contains(10.0, 10.0)


def test_quadrants_tile_the_parent_exactly() -> None:
    """Every point of the parent belongs to exactly one child."""
    box = BoundingBox(0.0, 0.0, 8.0, 8.0)
    children = box.quadrants()

    for x in [0.0, 1.5, 3.999, 4.0, 4.001, 7.999]:
        for y in [0.0, 1.5, 3.999, 4.0, 4.001, 7.999]:
            owners = [i for i, child in enumerate(children) if child.contains(x, y)]
            assert len(owners) == 1, f"({x}, {y}) belongs to {len(owners)} children"
            assert owners[0] == box.quadrant_index(x, y)


def test_points_exactly_on_the_split_line_go_to_one_child() -> None:
    """A point on an internal boundary lands in the upper child, never both."""
    box = BoundingBox(0.0, 0.0, 8.0, 8.0)
    children = box.quadrants()

    on_vertical_split = [(4.0, y) for y in (0.0, 2.0, 4.0, 7.5)]
    on_horizontal_split = [(x, 4.0) for x in (0.0, 2.0, 4.0, 7.5)]

    for x, y in on_vertical_split + on_horizontal_split:
        owners = [i for i, child in enumerate(children) if child.contains(x, y)]
        assert len(owners) == 1, f"boundary point ({x}, {y}) belongs to {len(owners)} children"


def test_min_distance_squared_is_zero_inside() -> None:
    """A query inside the box has a zero lower bound."""
    box = BoundingBox(0.0, 0.0, 10.0, 10.0)
    assert box.min_distance_squared(5.0, 5.0) == 0.0


def test_min_distance_squared_outside() -> None:
    """The lower bound is the squared distance to the closest edge or corner."""
    box = BoundingBox(0.0, 0.0, 10.0, 10.0)
    assert box.min_distance_squared(13.0, 5.0) == pytest.approx(9.0)
    assert box.min_distance_squared(13.0, 14.0) == pytest.approx(25.0)


@given(coordinate_pairs, coordinates, coordinates)
def test_box_lower_bound_never_exceeds_any_contained_point(
    pairs: list[tuple[float, float]], qx: float, qy: float
) -> None:
    """The pruning bound must never overestimate, or the search would skip answers."""
    if not pairs:
        return
    box = bounds_from_points(pairs, margin=1.0)
    bound = box.min_distance_squared(qx, qy)
    for x, y in pairs:
        assert bound <= (x - qx) ** 2 + (y - qy) ** 2 + 1e-9


# Bounds enforcement


def test_insert_outside_bounds_raises() -> None:
    """A point outside the root box is rejected rather than silently dropped."""
    tree = Quadtree(BoundingBox(0.0, 0.0, 10.0, 10.0))
    with pytest.raises(ValueError, match="outside tree bounds"):
        tree.insert(1, 20.0, 5.0)
    assert len(tree) == 0


def test_insert_exactly_on_maximum_edge_raises() -> None:
    """The root box is half open, so its maximum edge is outside."""
    tree = Quadtree(BoundingBox(0.0, 0.0, 10.0, 10.0))
    with pytest.raises(ValueError, match="outside tree bounds"):
        tree.insert(1, 10.0, 5.0)


def test_bounds_from_points_leaves_room_on_the_maximum_edge() -> None:
    """The margin is what makes the extreme point insertable despite half open bounds."""
    pairs = [(0.0, 0.0), (10.0, 10.0)]
    bounds = bounds_from_points(pairs, margin=1.0)
    tree = Quadtree(bounds)
    for i, (x, y) in enumerate(pairs):
        tree.insert(i, x, y)
    assert len(tree) == 2


def test_bounds_from_points_rejects_empty() -> None:
    """There is no meaningful box for zero points."""
    with pytest.raises(ValueError, match="empty"):
        bounds_from_points([])


def test_duplicate_id_raises() -> None:
    """Duplicate coordinates are fine, duplicate ids are not."""
    tree = Quadtree(TEST_BOUNDS)
    tree.insert(1, 0.0, 0.0)
    with pytest.raises(ValueError, match="already exists"):
        tree.insert(1, 5.0, 5.0)


def test_invalid_capacity_raises() -> None:
    """A leaf must be able to hold at least one point."""
    with pytest.raises(ValueError, match="capacity"):
        Quadtree(TEST_BOUNDS, capacity=0)


# Empty tree


def test_empty_tree_searches() -> None:
    """Every search on an empty tree returns an empty answer, not an error."""
    tree = Quadtree(TEST_BOUNDS)
    assert len(tree) == 0
    assert tree.nearest(0.0, 0.0) is None
    assert tree.k_nearest(0.0, 0.0, 5) == []
    assert tree.within_radius(0.0, 0.0, 100.0) == []


# Insert, remove, move


def test_remove_unknown_id_returns_false() -> None:
    """Removing an id that was never inserted is a no-op."""
    tree = Quadtree(TEST_BOUNDS)
    tree.insert(1, 0.0, 0.0)
    assert tree.remove(999) is False
    assert len(tree) == 1


def test_remove_twice_returns_false_the_second_time() -> None:
    """A removed id is gone, so a repeat removal reports nothing to do."""
    tree = Quadtree(TEST_BOUNDS)
    tree.insert(1, 0.0, 0.0)
    assert tree.remove(1) is True
    assert tree.remove(1) is False
    assert len(tree) == 0


def test_move_updates_position() -> None:
    """A moved point is found at its new location and no longer at the old one."""
    tree = Quadtree(TEST_BOUNDS)
    tree.insert(1, 0.0, 0.0)
    tree.insert(2, 500.0, 500.0)

    assert tree.move(1, 490.0, 490.0) is True

    assert len(tree) == 2
    assert tree.within_radius(0.0, 0.0, 1.0) == [], "nothing should remain at the old position"

    at_new_position = tree.within_radius(490.0, 490.0, 0.5)
    assert [p.id for p in at_new_position] == [1]


def test_move_unknown_id_returns_false() -> None:
    """Moving an unknown id reports failure rather than inserting."""
    tree = Quadtree(TEST_BOUNDS)
    assert tree.move(42, 1.0, 1.0) is False
    assert len(tree) == 0


def test_move_out_of_bounds_leaves_tree_untouched() -> None:
    """Bounds are validated before the removal, so a rejected move is atomic."""
    tree = Quadtree(BoundingBox(0.0, 0.0, 10.0, 10.0))
    tree.insert(1, 5.0, 5.0)

    with pytest.raises(ValueError, match="outside tree bounds"):
        tree.move(1, 99.0, 99.0)

    assert len(tree) == 1
    found = tree.nearest(5.0, 5.0)
    assert found is not None
    assert (found.x, found.y) == (5.0, 5.0)


def test_removal_collapses_internal_nodes_back_to_a_leaf() -> None:
    """Shrinking below capacity must undo the split, or the tree only ever grows."""
    tree = Quadtree(TEST_BOUNDS, capacity=4)
    for i in range(40):
        tree.insert(i, float(i * 10), float(i * 10))
    assert not tree._root.is_leaf, "40 points over capacity 4 should have split the root"

    for i in range(37):
        tree.remove(i)

    assert tree._root.is_leaf, "root should collapse once its subtree fits in one leaf"
    assert len(tree) == 3


@given(coordinate_pairs)
def test_len_tracks_inserts_and_removes(pairs: list[tuple[float, float]]) -> None:
    """The reported size matches the number of live ids at every step."""
    tree, _ = build_tree(pairs)
    assert len(tree) == len(pairs)

    for i in range(0, len(pairs), 2):
        assert tree.remove(i) is True

    assert len(tree) == len(pairs) - len(range(0, len(pairs), 2))


@given(coordinate_pairs, coordinates, coordinates)
def test_search_after_moves_matches_brute_force(
    pairs: list[tuple[float, float]], qx: float, qy: float
) -> None:
    """A tree mutated by moves answers the same as a scan of the final positions."""
    if not pairs:
        return

    tree, _ = build_tree(pairs)
    moved = []
    for i, (x, y) in enumerate(pairs):
        if i % 3 == 0:
            new_x, new_y = -x, -y
            tree.move(i, new_x, new_y)
            moved.append(Point(i, new_x, new_y))
        else:
            moved.append(Point(i, x, y))

    found = tree.nearest(qx, qy)
    expected = brute_nearest(moved, qx, qy)
    assert found is not None and expected is not None
    assert distance(found, qx, qy) == pytest.approx(distance(expected, qx, qy))


# Duplicates and depth


def test_thousand_identical_points() -> None:
    """Identical coordinates cannot be separated by splitting, so max_depth caps it."""
    tree = Quadtree(TEST_BOUNDS, capacity=4, max_depth=8)
    for i in range(1000):
        tree.insert(i, 42.0, 42.0)

    assert len(tree) == 1000

    found = tree.nearest(42.0, 42.0)
    assert found is not None
    assert distance(found, 42.0, 42.0) == 0.0

    assert len(tree.k_nearest(42.0, 42.0, 10)) == 10
    assert len(tree.within_radius(42.0, 42.0, 1.0)) == 1000

    for i in range(1000):
        assert tree.remove(i) is True
    assert len(tree) == 0


def test_duplicate_coordinates_with_distinct_ids() -> None:
    """Several points may share a position; all are stored and retrievable."""
    tree = Quadtree(TEST_BOUNDS, capacity=2)
    for i in range(10):
        tree.insert(i, 1.0, 1.0)
    tree.insert(99, 900.0, 900.0)

    assert len(tree) == 11
    within = tree.within_radius(1.0, 1.0, 0.5)
    assert len(within) == 10
    assert {p.id for p in within} == set(range(10))


# k_nearest edge cases


def test_k_nearest_with_k_zero_returns_empty() -> None:
    """Asking for nothing returns nothing."""
    tree, _ = build_tree([(float(i), 0.0) for i in range(10)])
    assert tree.k_nearest(0.0, 0.0, 0) == []


def test_k_nearest_with_k_larger_than_size_returns_all_sorted() -> None:
    """Over-asking returns everything, still ordered nearest first."""
    pairs = [(float(i * 10), 0.0) for i in range(5)]
    tree, points = build_tree(pairs)

    found = tree.k_nearest(0.0, 0.0, 100)

    assert len(found) == 5
    distances = [distance(p, 0.0, 0.0) for p in found]
    assert distances == sorted(distances)
    assert {p.id for p in found} == {p.id for p in points}


def test_k_nearest_with_negative_k_raises() -> None:
    """A negative k is a caller bug, not an empty result."""
    tree, _ = build_tree([(0.0, 0.0)])
    with pytest.raises(ValueError, match="k must be non-negative"):
        tree.k_nearest(0.0, 0.0, -1)


def test_within_radius_with_negative_radius_raises() -> None:
    """A negative radius is a caller bug."""
    tree, _ = build_tree([(0.0, 0.0)])
    with pytest.raises(ValueError, match="radius must be non-negative"):
        tree.within_radius(0.0, 0.0, -5.0)


def test_within_radius_boundary_is_inclusive() -> None:
    """A point exactly at the radius counts as inside."""
    tree, _ = build_tree([(3.0, 4.0)])
    assert len(tree.within_radius(0.0, 0.0, 5.0)) == 1
    assert len(tree.within_radius(0.0, 0.0, 4.999)) == 0


# Property tests against the brute force oracle


@given(coordinate_pairs, coordinates, coordinates)
def test_nearest_matches_brute_force(
    pairs: list[tuple[float, float]], qx: float, qy: float
) -> None:
    """Quadtree nearest returns a point at the same distance as a linear scan."""
    tree, points = build_tree(pairs)
    found = tree.nearest(qx, qy)
    expected = brute_nearest(points, qx, qy)

    if expected is None:
        assert found is None
        return

    assert found is not None
    assert distance(found, qx, qy) == pytest.approx(distance(expected, qx, qy))


@given(coordinate_pairs, coordinates, coordinates, st.integers(min_value=1, max_value=20))
def test_k_nearest_matches_brute_force(
    pairs: list[tuple[float, float]], qx: float, qy: float, k: int
) -> None:
    """Quadtree k_nearest returns the same distance sequence as a full sort."""
    tree, points = build_tree(pairs)
    found = tree.k_nearest(
        qx,
        qy,
        k,
    )
    expected = brute_k_nearest(points, qx, qy, k)

    assert len(found) == len(expected)
    for got, want in zip(found, expected, strict=True):
        assert distance(got, qx, qy) == pytest.approx(distance(want, qx, qy))


@given(coordinate_pairs, coordinates, coordinates)
def test_k_nearest_results_are_sorted(
    pairs: list[tuple[float, float]], qx: float, qy: float
) -> None:
    """Results come back nearest first."""
    tree, _ = build_tree(pairs)
    found = tree.k_nearest(qx, qy, 10)
    distances = [distance(p, qx, qy) for p in found]
    assert distances == sorted(distances)


@given(
    coordinate_pairs,
    coordinates,
    coordinates,
    st.floats(min_value=0.0, max_value=3_000.0, allow_nan=False, allow_infinity=False),
)
def test_within_radius_matches_brute_force(
    pairs: list[tuple[float, float]], qx: float, qy: float, radius: float
) -> None:
    """Quadtree within_radius returns exactly the same set as a linear scan."""
    tree, points = build_tree(pairs)
    found = tree.within_radius(qx, qy, radius)
    expected = brute_within_radius(points, qx, qy, radius)

    assert {p.id for p in found} == {p.id for p in expected}


@given(coordinate_pairs, coordinates, coordinates)
def test_nearest_agrees_with_k_nearest_one(
    pairs: list[tuple[float, float]], qx: float, qy: float
) -> None:
    """The specialised nearest path and the general k path must not diverge."""
    tree, _ = build_tree(pairs)
    single = tree.nearest(qx, qy)
    from_k = tree.k_nearest(qx, qy, 1)

    if single is None:
        assert from_k == []
        return

    assert distance(single, qx, qy) == pytest.approx(distance(from_k[0], qx, qy))


# Instrumentation


def test_search_stats_are_reported() -> None:
    """Both searches fill in the counters when a stats object is passed."""
    tree, _ = build_tree([(float(i), float(i)) for i in range(200)])

    nearest_stats = SearchStats()
    tree.nearest(10.0, 10.0, stats=nearest_stats)
    assert nearest_stats.nodes_visited > 0
    assert nearest_stats.points_checked > 0

    k_stats = SearchStats()
    tree.k_nearest(10.0, 10.0, 5, stats=k_stats)
    assert k_stats.nodes_visited > 0
    assert k_stats.points_checked > 0


def test_nearest_prunes_most_of_the_tree() -> None:
    """The point of the structure: a query inspects far fewer points than exist."""
    count = 5_000
    step = 2 * COORDINATE_LIMIT / count
    pairs = [(-COORDINATE_LIMIT + i * step, math.sin(i) * 100) for i in range(count)]
    tree, _ = build_tree(pairs)

    stats = SearchStats()
    tree.nearest(0.0, 0.0, stats=stats)

    assert stats.points_checked < count / 10, (
        f"checked {stats.points_checked} of {count} points, pruning is not working"
    )


def test_stats_accumulate_across_calls() -> None:
    """A reused stats object sums, so a caller can total a batch of queries."""
    tree, _ = build_tree([(float(i), 0.0) for i in range(100)])

    stats = SearchStats()
    tree.nearest(1.0, 0.0, stats=stats)
    after_first = stats.points_checked
    tree.nearest(50.0, 0.0, stats=stats)

    assert stats.points_checked > after_first


def test_nearest_checks_under_five_percent_of_ten_thousand_points() -> None:
    """Guards against a quadtree that is correct but secretly scans everything.

    The brute force property tests compare answers, so an implementation that
    ignored its own structure and checked every point would still pass them all.
    This asserts the work done, not the answer: with 10,000 uniform points a
    nearest query must touch under 5 percent of them.
    """
    count = 10_000
    rng = random.Random(20240922)
    span = COORDINATE_LIMIT - 1

    tree = Quadtree(TEST_BOUNDS)
    for i in range(count):
        tree.insert(i, rng.uniform(-span, span), rng.uniform(-span, span))

    queries = 200
    stats = SearchStats()
    for _ in range(queries):
        tree.nearest(rng.uniform(-span, span), rng.uniform(-span, span), stats=stats)

    mean_points_checked = stats.points_checked / queries
    budget = count * 0.05

    assert mean_points_checked < budget, (
        f"mean points_checked {mean_points_checked:.1f} per query is not under "
        f"{budget:.0f} (5 percent of {count}); the search may be scanning"
    )


def test_invalid_max_depth_raises() -> None:
    """A negative max_depth has no meaning."""
    with pytest.raises(ValueError, match="max_depth"):
        Quadtree(TEST_BOUNDS, max_depth=-1)


def test_bounds_from_points_rejects_non_positive_margin() -> None:
    """A zero margin would leave the extreme point on the excluded maximum edge."""
    with pytest.raises(ValueError, match="margin must be positive"):
        bounds_from_points([(0.0, 0.0)], margin=0.0)


def test_contains_reports_membership() -> None:
    """Membership by id is a constant time lookup."""
    tree = Quadtree(TEST_BOUNDS)
    tree.insert("courier-1", 0.0, 0.0)
    assert "courier-1" in tree
    assert "courier-2" not in tree
    tree.remove("courier-1")
    assert "courier-1" not in tree
