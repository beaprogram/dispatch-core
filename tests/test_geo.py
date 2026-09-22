"""Tests for haversine distance and the local equirectangular projection."""

import math

from dispatch_core.geo import haversine, project_to_local, unproject_from_local

CENTER_LAT = 44.6476
CENTER_LON = -63.5806


def test_haversine_zero_for_same_point() -> None:
    """Distance from a point to itself is zero."""
    assert haversine(CENTER_LAT, CENTER_LON, CENTER_LAT, CENTER_LON) == 0.0


def test_haversine_is_symmetric() -> None:
    """Distance does not depend on argument order."""
    a = haversine(CENTER_LAT, CENTER_LON, 44.65, -63.59)
    b = haversine(44.65, -63.59, CENTER_LAT, CENTER_LON)
    assert a == b


def test_haversine_one_degree_of_latitude() -> None:
    """One degree of latitude is about 111 km on a sphere of this radius."""
    distance = haversine(0.0, 0.0, 1.0, 0.0)
    assert 111_000 < distance < 111_400


def test_projection_of_center_is_origin() -> None:
    """The center point projects to (0, 0)."""
    x, y = project_to_local(CENTER_LAT, CENTER_LON, CENTER_LAT, CENTER_LON)
    assert x == 0.0
    assert y == 0.0


def test_projection_round_trip() -> None:
    """Projecting then unprojecting returns the original coordinates."""
    for d_lat, d_lon in [(0.01, 0.01), (-0.02, 0.03), (0.04, -0.05)]:
        lat, lon = CENTER_LAT + d_lat, CENTER_LON + d_lon
        x, y = project_to_local(lat, lon, CENTER_LAT, CENTER_LON)
        back_lat, back_lon = unproject_from_local(x, y, CENTER_LAT, CENTER_LON)
        assert math.isclose(back_lat, lat, abs_tol=1e-9)
        assert math.isclose(back_lon, lon, abs_tol=1e-9)


def test_projection_error_within_five_km() -> None:
    """Euclidean distance in projected meters tracks haversine within 0.1 percent.

    This is the assumption that lets the quadtree work in flat local meters while
    routing heuristics use haversine. The bound is asserted, not estimated.
    """
    worst_relative_error = 0.0
    steps = 20
    max_offset_deg = 5000 / 111_320  # about 5 km expressed in degrees of latitude

    for i in range(-steps, steps + 1):
        for j in range(-steps, steps + 1):
            lat = CENTER_LAT + max_offset_deg * i / steps
            lon = CENTER_LON + max_offset_deg * j / steps

            true_distance = haversine(CENTER_LAT, CENTER_LON, lat, lon)
            if true_distance < 1.0:
                continue

            x, y = project_to_local(lat, lon, CENTER_LAT, CENTER_LON)
            projected_distance = math.hypot(x, y)
            relative_error = abs(projected_distance - true_distance) / true_distance
            worst_relative_error = max(worst_relative_error, relative_error)

    assert worst_relative_error < 0.001, (
        f"Worst relative projection error {worst_relative_error:.6f} exceeds 0.1 percent"
    )
