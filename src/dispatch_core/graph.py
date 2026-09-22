"""Graph loading and manipulation: load/save, connected components, travel times."""

import networkx as nx
import osmnx as ox


def load_graph(path: str) -> nx.MultiDiGraph:
    """Load a road graph from a GraphML file on disk.

    Reads from disk only, never the network. GraphML stores every attribute as a
    string, so this converts length, travel_time, and speed_kph back to floats.

    Time complexity: O(V + E)
    """
    return ox.load_graphml(path)


def save_graph(graph: nx.MultiDiGraph, path: str) -> None:
    """Save a road graph to a GraphML file.

    Time complexity: O(V + E)
    """
    ox.save_graphml(graph, path)


def largest_strongly_connected_component(graph: nx.MultiDiGraph) -> nx.MultiDiGraph:
    """Return the largest strongly connected component as a new graph.

    One-way streets make the road network directed, so a weakly connected node can
    be unreachable. Restricting to the largest SCC guarantees every node routes to
    every other node.

    Time complexity: O(V + E)
    """
    components = list(nx.strongly_connected_components(graph))
    if not components:
        return graph.copy()
    return graph.subgraph(max(components, key=len)).copy()


def validate_graph(graph: nx.MultiDiGraph) -> tuple[bool, str]:
    """Check that every edge has a positive length and travel_time.

    Returns (is_valid, message).

    Time complexity: O(E)
    """
    for u, v, key, data in graph.edges(keys=True, data=True):
        if "length" not in data:
            return False, f"Edge ({u}, {v}, {key}) missing 'length' attribute"
        if "travel_time" not in data:
            return False, f"Edge ({u}, {v}, {key}) missing 'travel_time' attribute"
        if float(data["length"]) <= 0:
            return False, f"Edge ({u}, {v}, {key}) has non-positive 'length'"
        if float(data["travel_time"]) <= 0:
            return False, f"Edge ({u}, {v}, {key}) has non-positive 'travel_time'"
    return True, "Graph is valid"


def graph_stats(graph: nx.MultiDiGraph) -> tuple[int, int]:
    """Return (num_nodes, num_edges).

    Time complexity: O(1)
    """
    return graph.number_of_nodes(), graph.number_of_edges()
