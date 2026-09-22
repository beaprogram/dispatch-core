"""Geographic utilities: haversine, equirectangular projection to local meters."""

import math

EARTH_RADIUS_M = 6371008.8  # meters


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in meters between two lat/lon points.

    Time complexity: O(1)
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def project_to_local(
    lat: float, lon: float, center_lat: float, center_lon: float
) -> tuple[float, float]:
    """Project lat/lon to local x, y meters around a center point.

    Uses an equirectangular approximation, which is accurate for a small area
    around the center. Error against haversine is measured in tests.

    Time complexity: O(1)
    """
    center_lat_rad = math.radians(center_lat)
    x = math.radians(lon - center_lon) * EARTH_RADIUS_M * math.cos(center_lat_rad)
    y = math.radians(lat - center_lat) * EARTH_RADIUS_M
    return x, y


def unproject_from_local(
    x: float, y: float, center_lat: float, center_lon: float
) -> tuple[float, float]:
    """Unproject local x, y meters to lat/lon around a center point.

    Time complexity: O(1)
    """
    center_lat_rad = math.radians(center_lat)
    lon = center_lon + math.degrees(x / (EARTH_RADIUS_M * math.cos(center_lat_rad)))
    lat = center_lat + math.degrees(y / EARTH_RADIUS_M)
    return lat, lon
