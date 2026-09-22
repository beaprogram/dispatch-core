# Engineering notes

Short factual records of bugs worth remembering and decisions that are not obvious
from the code. Numbers here come from a test or benchmark run in this repo.

## Equirectangular projection was off by a factor of about 57 (CP1)

`project_to_local` multiplied raw degree offsets by the earth radius without first
converting them to radians, so every projected distance came back roughly 180/pi
times too large. The round-trip test did not catch it because `unproject_from_local`
divided by the same wrong scale, so projecting and unprojecting still returned the
original coordinates exactly. The bug only surfaced once
`test_projection_error_within_five_km` compared projected Euclidean distance against
an independent oracle, haversine, which reported a worst relative error of 56.3
before the fix and 0.0138 percent after it.

Takeaway: a round trip through a function and its own inverse verifies that the two
agree, not that either is correct. Correctness needs an oracle computed a different
way.

## osmnx "drive" already excludes private access (CP1)

An early version of `scripts/fetch_graph.py` passed
`custom_filter='["access"!~"private"]'` alongside `network_type="drive"`. That was
redundant and harmful: osmnx interpolates `settings.default_access`, which is exactly
`'["access"!~"private"]'` (settings.py:141), into its drive filter inside
`_overpass._get_network_filter`. Supplying `custom_filter` replaces the whole filter
including the `["highway"]` requirement, so untagged ways came back, `add_edge_speeds`
could not infer a speed for them, and `add_edge_travel_times` raised
`ValueError: Edge 'length' and 'speed_kph' values must be non-null`.

## What points_checked does and does not measure (CP2)

`SearchStats.points_checked` counts distance computations only. It does not count
nodes visited, heap pushes and pops, or any other per-query overhead, so it is a
measure of pruning effectiveness rather than of speed. Results should be phrased as
"fewer distance checks than a linear scan", not as "less work" or as a speedup.
Measured on 10,000 uniform seeded points, a nearest query checked a mean of 8.74
points, which is 1,144 times fewer distance checks than a linear scan. Actual
runtime speedup is a separate question and is measured in CP5, where the constant
factors this counter ignores do show up.

## A correctness test cannot catch a quadtree that scans (CP2)

Every property test compares the quadtree's answer against `bruteforce.py`, so an
implementation that ignored its own structure and checked every point would pass all
of them. Replacing `BoundingBox.min_distance_squared` with a constant zero disables
pruning completely, and under that mutation the four brute force comparison tests
still passed while only the work budget test failed, at 10000.0 < 500.0. Asserting
the answer and asserting the work done are different tests, and the second one is
the only one that constrains the data structure actually being used.

## Two shapes that defeat quadtree pruning (CP2)

Coincident points cannot be separated by splitting, so they pile into a single leaf
at `max_depth` and are scanned in full. That also makes `remove` worst case
O(max_depth + m) rather than O(max_depth): the descent is bounded, but the target
leaf is filtered in full, and m reaches n when every point shares a coordinate.
Verified at n of 200, 800, and 3200, where the deepest leaf held exactly n points.

A query at the center of a ring of equidistant points defeats pruning for a
different reason. Every box holding ring points also extends toward the query, so
its lower bound is below the best distance found and no box can be discarded.
Measured on a ring of 8,000 points, one nearest query checked 7,998 of them, 100
percent of n.

## Heuristic consistency holds exactly, no scaling needed (CP3)

The concern was that osmnx might round lengths or travel times enough to break
h(u) <= w(u, v) + h(v) by a small margin. It does not. `add_edge_travel_times`
computes `length / speed` with no rounding, and osmnx's `EARTH_RADIUS_M` is 6371009
against the 6371008.8 used in `geo.py`, so osmnx edge lengths are if anything
fractionally larger than this repo's haversine, which is the safe direction.

Measured worst slack, `w(u, v) + h(v) - h(u)` over every edge for 25 sampled
targets, where negative would be a violation:

| graph | length | travel_time |
| --- | --- | --- |
| fixture, 800 m | +1.160e-07 | +5.015e-08 |
| full, 5000 m | +3.091e-07 | +4.631e-02 |

All positive, so no scale factor was applied. The condition reduces to
`haversine(u, v) <= length(u, v)` on every edge, which held on all 605 fixture
edges with zero violations, because a road cannot be shorter than the straight line
between its endpoints.

## The travel_time heuristic gets weaker as the graph gets faster (CP3)

Dividing haversine by the single fastest edge speed in the graph keeps the estimate
admissible, but how useful it is depends on the spread of speeds. Mean nodes
expanded over 200 seeded pairs:

| graph | max speed | length reduction | travel_time reduction |
| --- | --- | --- | --- |
| fixture, 800 m | 50 kph | 69.9 percent | 64.3 percent |
| full, 5000 m | 90 kph | 66.9 percent | 34.0 percent |

The full graph contains a 90 kph road, so every estimate is divided by 25 m/s even
though most downtown streets run far slower. The estimate stays valid but becomes
very optimistic, the frontier flattens toward Dijkstra, and the reduction halves.
The length heuristic is unaffected because it has no such divisor. This is the
admissibility against informedness tradeoff, and it is why a tighter bound such as a
per-region speed cap would help on a graph with mixed road classes.
