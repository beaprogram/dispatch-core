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

`make_heuristic` estimates remaining travel time as `haversine(node, target) /
max_edge_speed_mps(graph)`. Dividing by the fastest edge in the whole graph is what
makes it admissible: no route can average more than the fastest road on it, so the
estimate can never overshoot. But the same choice is what makes it loose. The
divisor is set by the single fastest road anywhere in the graph, while most of a
downtown network is far slower, so on slow city streets the estimate is a small
fraction of the real time and gives the search almost no guidance.

How loose depends entirely on the spread of speeds, not on graph size:

| graph | median speed | max speed | max/median | edges at max speed |
| --- | --- | --- | --- | --- |
| fixture, 800 m | 46.8 kph | 50 kph | 1.07x | 155 of 605, 25.62 percent |
| full, 5000 m | 41.2 kph | 90 kph | 2.19x | 1 of 8915, 0.01 percent |

On the full graph a single 90 kph edge out of 8,915 sets the divisor for every
estimate in the graph, inflating it by roughly 2.2x against the median street.

The cost, as mean nodes expanded over 200 seeded source and target pairs, where
"reduction" is `1 - (A* mean / Dijkstra mean)` and both algorithms run the same
`_search` code differing only in heuristic:

| graph | weight | A* mean | Dijkstra mean | reduction |
| --- | --- | --- | --- | --- |
| fixture, 800 m | length | 37.0 | 122.7 | 69.9 percent |
| fixture, 800 m | travel_time | 43.6 | 122.1 | 64.3 percent |
| full, 5000 m | length | 537.1 | 1623.6 | 66.9 percent |
| full, 5000 m | travel_time | 1075.6 | 1630.4 | 34.0 percent |

The length heuristic holds up across both graphs because it has no divisor: the
straight line distance is a tight bound on road distance regardless of speed limits.
Only travel_time degrades, and it degrades exactly where the speed spread widens.

This is the admissibility against informedness tradeoff. A heuristic must stay below
the true cost to guarantee correctness, and the cheapest way to guarantee that is a
global bound, which is also the weakest one.

Roadmap fix, not implemented here: ALT, meaning A* with landmarks and the triangle
inequality. Pick a small set of landmark nodes, precompute the exact shortest travel
time from every node to each landmark and back, then bound the remaining cost with
`|d(node, L) - d(target, L)|` maximised over landmarks. That bound is admissible by
the triangle inequality and uses real road times rather than a straight line divided
by a global speed cap, so it stays tight on slow streets. The cost is a
precomputation pass and O(landmarks) storage per node.

## The default arrival rate has to create contention or the queue is never tested (CP4)

The first default arrival rate was an arbitrary 12 orders per minute. On the full
graph with 200 couriers that produced mean wait 0.0 s and p95 wait 0.0 s: a courier
was always free, so no order ever entered the pending heap and the priority queue,
one of the core data structures here, was never exercised at all. Every metric still
looked fine, which is what made it easy to miss.

Little's law explains it. With a mean delivery of roughly 12 minutes, 200 couriers
saturate at about 200 / 12, near 16.7 orders per minute, so 12 per minute leaves
real slack. Measured sweep on the full graph, 1000 orders, seed 42, nearest:

| orders/min | mean wait | p95 wait | mean delivery | on time |
| --- | --- | --- | --- | --- |
| 12 | 0.0 s | 0.0 s | 723.2 s | 100.0 percent |
| 18 | 403.6 s | 1201.1 s | 1404.6 s | 97.5 percent |
| 24 | 801.2 s | 2022.8 s | 1825.5 s | 80.5 percent |
| 30 | 1013.5 s | 2522.2 s | 2034.1 s | 70.1 percent |
| 40 | 1217.2 s | 2877.8 s | 2244.0 s | 62.7 percent |
| 60 | 1446.4 s | 3369.9 s | 2475.2 s | 56.1 percent |

The default is now 18 per minute, just past saturation, where the queue is genuinely
used and on time percentage is neither a trivial 100 nor a collapse.

## The route cache only helps one of the two strategies (CP4)

Measured on the default run, full graph, 1000 orders, 200 couriers, seed 42:

| strategy | cache hits | cache misses | hit rate |
| --- | --- | --- | --- |
| nearest | 0 | 1994 | 0.00 percent |
| topk-eta | 1019 | 3067 | 24.94 percent |

`nearest` issues exactly two routes per order, courier to pickup and pickup to
dropoff, and both endpoints are drawn uniformly from 3505 nodes, so a repeated node
pair is close to impossible and the cache never hits. `topk-eta` routes k of 5
candidates per decision, and candidate couriers recur across nearby pickups, which is
where the repeats come from.

So the cache hit rate should be reported per strategy, never as a single headline
number for the system. A cache that does nothing for the default strategy is worth
stating plainly rather than hiding behind an average across both.
