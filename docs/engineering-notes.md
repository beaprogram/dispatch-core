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
