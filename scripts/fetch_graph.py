"""Download drivable road network from OpenStreetMap via osmnx."""

import argparse
import sys
import time
from pathlib import Path

import networkx as nx
import osmnx as ox

# Halifax downtown center
CENTER_LAT = 44.6476
CENTER_LON = -63.5806

# Default radii in meters
DEFAULT_FIXTURE_RADIUS_M = 800
DEFAULT_BENCHMARK_RADIUS_M = 5000


def _download(center_lat: float, center_lon: float, radius_m: int) -> nx.MultiDiGraph:
    """Download the drivable network around a point from Overpass."""
    return ox.graph_from_point(
        (center_lat, center_lon),
        dist=radius_m,
        network_type="drive",
        simplify=True,
        truncate_by_edge=True,
    )


def fetch_graph(
    output_path: str,
    radius_m: int,
    center_lat: float = CENTER_LAT,
    center_lon: float = CENTER_LON,
    retry_on_fail: bool = True,
) -> None:
    """Download and save the drivable road network around a center point.

    Keeps the largest strongly connected component, because one-way streets make
    the road network directed and weakly connected nodes would be unroutable.

    Arguments:
        output_path: Path to save the GraphML file
        radius_m: Search radius in meters
        center_lat, center_lon: Center of the search area
        retry_on_fail: Retry once after 60 seconds if the download fails
    """
    output_path_obj = Path(output_path)
    output_path_obj.parent.mkdir(parents=True, exist_ok=True)

    print(f"Fetching graph within {radius_m} m of ({center_lat}, {center_lon})...")

    try:
        graph = _download(center_lat, center_lon, radius_m)
    except Exception as first_error:
        if not retry_on_fail:
            print(f"Error downloading graph: {first_error}")
            sys.exit(1)
        print(f"Download failed: {first_error}")
        print("Retrying once in 60 seconds...")
        time.sleep(60)
        try:
            graph = _download(center_lat, center_lon, radius_m)
        except Exception as retry_error:
            print(f"Retry failed: {retry_error}")
            print("Stopping. Overpass is unavailable or rate limiting.")
            sys.exit(1)

    raw_nodes, raw_edges = len(graph.nodes()), len(graph.edges())
    print(f"Downloaded: {raw_nodes} nodes, {raw_edges} edges")

    graph = ox.add_edge_speeds(graph)
    graph = ox.add_edge_travel_times(graph)

    largest = max(nx.strongly_connected_components(graph), key=len)
    graph = graph.subgraph(largest).copy()

    ox.save_graphml(graph, output_path)

    num_nodes = len(graph.nodes())
    num_edges = len(graph.edges())
    size_mb = output_path_obj.stat().st_size / (1024 * 1024)
    print(f"Largest strongly connected component: {num_nodes} nodes, {num_edges} edges")
    print(f"Dropped in SCC filter: {raw_nodes - num_nodes} nodes, {raw_edges - num_edges} edges")
    print(f"Saved: {output_path} ({size_mb:.2f} MB)")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Download and save drivable road network")
    parser.add_argument(
        "-o",
        "--output",
        default="road_network.graphml",
        help="Output GraphML file (default: road_network.graphml)",
    )
    parser.add_argument(
        "-r",
        "--radius",
        type=int,
        default=DEFAULT_BENCHMARK_RADIUS_M,
        help=f"Radius in meters (default: {DEFAULT_BENCHMARK_RADIUS_M})",
    )
    parser.add_argument(
        "--lat",
        type=float,
        default=CENTER_LAT,
        help=f"Center latitude (default: {CENTER_LAT})",
    )
    parser.add_argument(
        "--lon",
        type=float,
        default=CENTER_LON,
        help=f"Center longitude (default: {CENTER_LON})",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="Do not retry if Overpass rate-limits",
    )

    args = parser.parse_args()
    fetch_graph(
        args.output,
        args.radius,
        args.lat,
        args.lon,
        retry_on_fail=not args.no_retry,
    )


if __name__ == "__main__":
    main()
