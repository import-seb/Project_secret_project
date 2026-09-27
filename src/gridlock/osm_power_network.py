"""
Reusable OpenStreetMap transmission/subtransmission network ingestion and routing.

Purpose
-------
Pull South Carolina power=line + power=minor_line ways once, cache them,
build a graph from the REAL OSM node IDs, and route existing assets through
the appropriate voltage view.

Important rules
---------------
1. Two lines are electrically connected only when they share an OSM node.
   A visual crossing is NOT automatically a junction.
2. Unknown-voltage ways are excluded from trusted routing by default.
3. A route that requires unknown-voltage edges should be treated as
   review_required rather than silently accepted.
4. Existing assets use traced OSM geometry. This module does not invent
   straight-line transmission routes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import heapq
import json
import math
from pathlib import Path
import re
import time
from typing import Any, Iterable

import pandas as pd
import requests


DEFAULT_OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

DEFAULT_USER_AGENT = "GridLock-Hackathon/OSM-power-network"


def build_overpass_query(state_iso: str = "US-SC") -> str:
    """Build one statewide query for line + minor_line ways and their member nodes."""
    return f"""
[out:json][timeout:180];

area["ISO3166-2"="{state_iso}"][admin_level=4]->.stateArea;

(
  way["power"="line"](area.stateArea);
  way["power"="minor_line"](area.stateArea);
);

out body;
>;
out skel qt;
""".strip()


def download_osm_power_network(
    cache_path: str | Path,
    *,
    state_iso: str = "US-SC",
    refresh: bool = False,
    overpass_urls: Iterable[str] | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = 240,
) -> Path:
    """Download and cache the statewide OSM line network."""
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    if cache_path.exists() and not refresh:
        return cache_path

    query = build_overpass_query(state_iso)
    urls = list(overpass_urls or DEFAULT_OVERPASS_URLS)
    last_error: Exception | None = None

    for url in urls:
        try:
            response = requests.post(
                url,
                data={"data": query},
                headers={"User-Agent": user_agent},
                timeout=timeout,
            )
            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, dict) or "elements" not in payload:
                raise ValueError("Overpass response did not contain an elements list.")
            if payload.get("remark"):
                raise ValueError(f"Incomplete Overpass response: {payload['remark']}")

            cache_path.write_text(json.dumps(payload), encoding="utf-8")
            return cache_path
        except Exception as exc:
            last_error = exc
            time.sleep(1.0)

    raise RuntimeError(f"All Overpass endpoints failed. Last error: {last_error}")


def parse_voltage_tags(value: Any) -> list[float]:
    """Parse OSM voltage values to kV. Missing voltage becomes []."""
    if value is None or pd.isna(value):
        return []

    numbers = re.findall(r"\d+(?:\.\d+)?", str(value))
    parsed: list[float] = []

    for token in numbers:
        number = float(token)
        if number > 1000:
            number /= 1000.0
        if number not in parsed:
            parsed.append(number)

    return parsed


def _clean_text(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    value = str(value).strip()
    return value or None


def load_osm_power_network(raw_path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse cached Overpass JSON into one way table and one node table."""
    raw_path = Path(raw_path)
    payload = json.loads(raw_path.read_text(encoding="utf-8"))

    nodes: dict[int, dict[str, Any]] = {}
    ways: list[dict[str, Any]] = []

    for element in payload.get("elements", []):
        element_type = element.get("type")

        if element_type == "node":
            node_id = int(element["id"])
            nodes[node_id] = {
                "node_id": node_id,
                "lat": float(element["lat"]),
                "lon": float(element["lon"]),
            }
            continue

        if element_type != "way":
            continue

        tags = element.get("tags") or {}
        power = tags.get("power")

        if power not in {"line", "minor_line"}:
            continue

        ways.append({
            "way_id": int(element["id"]),
            "power": power,
            "name": _clean_text(tags.get("name")),
            "operator": _clean_text(tags.get("operator")),
            "owner": _clean_text(tags.get("owner")),
            "ref": _clean_text(tags.get("ref")),
            "circuits": _clean_text(tags.get("circuits")),
            "cables": _clean_text(tags.get("cables")),
            "voltage_raw": _clean_text(tags.get("voltage")),
            "voltages_kv": parse_voltage_tags(tags.get("voltage")),
            "node_ids": [int(x) for x in element.get("nodes", [])],
            "tags": tags,
        })

    ways_df = pd.DataFrame(ways)
    nodes_df = pd.DataFrame(nodes.values())

    if not ways_df.empty:
        ways_df = ways_df.sort_values("way_id").reset_index(drop=True)
    if not nodes_df.empty:
        nodes_df = nodes_df.sort_values("node_id").reset_index(drop=True)

    return ways_df, nodes_df


def voltage_summary(ways_df: pd.DataFrame) -> pd.DataFrame:
    """Count OSM ways by parsed voltage. Multi-voltage ways count in each class."""
    counts: dict[str, int] = defaultdict(int)

    for values in ways_df["voltages_kv"]:
        if not values:
            counts["unknown"] += 1
            continue

        for voltage in values:
            label = str(int(voltage)) if float(voltage).is_integer() else str(voltage)
            counts[label] += 1

    rows = [{"voltage_kv": key, "ways": value} for key, value in counts.items()]

    def sort_key(row):
        if row["voltage_kv"] == "unknown":
            return (1, float("inf"))
        return (0, float(row["voltage_kv"]))

    return pd.DataFrame(sorted(rows, key=sort_key))


def haversine_km(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371.0088
    lon1, lat1, lon2, lat2 = map(math.radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class OSMVoltageGraph:
    voltage_kv: float
    include_unknown_voltage: bool
    adjacency: dict[int, list[dict[str, Any]]]
    nodes: dict[int, dict[str, float]]
    edge_count: int
    way_ids: set[int]

    @property
    def node_count(self) -> int:
        return len(self.adjacency)


def _way_usable_for_voltage(
    voltages_kv: list[float],
    target_voltage_kv: float,
    *,
    include_unknown_voltage: bool,
    tolerance_kv: float = 0.5,
) -> bool:
    if not voltages_kv:
        return include_unknown_voltage
    return any(abs(float(v) - float(target_voltage_kv)) <= tolerance_kv for v in voltages_kv)


def build_voltage_graph(
    ways_df: pd.DataFrame,
    nodes_df: pd.DataFrame,
    voltage_kv: float,
    *,
    include_unknown_voltage: bool = False,
) -> OSMVoltageGraph:
    """Build a graph from consecutive OSM node IDs. Visual crossings do not connect."""
    node_lookup = {
        int(row.node_id): {"lon": float(row.lon), "lat": float(row.lat)}
        for row in nodes_df.itertuples(index=False)
    }

    adjacency: dict[int, list[dict[str, Any]]] = defaultdict(list)
    way_ids: set[int] = set()
    edge_count = 0

    for row in ways_df.itertuples(index=False):
        voltages = list(row.voltages_kv)

        if not _way_usable_for_voltage(
            voltages,
            voltage_kv,
            include_unknown_voltage=include_unknown_voltage,
        ):
            continue

        node_ids = list(row.node_ids)

        for u, v in zip(node_ids, node_ids[1:]):
            if u not in node_lookup or v not in node_lookup:
                continue

            u_coord = node_lookup[u]
            v_coord = node_lookup[v]
            length_km = haversine_km(
                u_coord["lon"], u_coord["lat"],
                v_coord["lon"], v_coord["lat"],
            )
            unknown_voltage = not bool(voltages)

            edge = {
                "u": u,
                "v": v,
                "length_km": length_km,
                "way_id": int(row.way_id),
                "power": row.power,
                "name": row.name,
                "operator": row.operator,
                "owner": row.owner,
                "ref": row.ref,
                "circuits": row.circuits,
                "cables": row.cables,
                "voltages_kv": voltages,
                "unknown_voltage": unknown_voltage,
            }

            reverse_edge = dict(edge)
            reverse_edge["u"] = v
            reverse_edge["v"] = u

            adjacency[u].append(edge)
            adjacency[v].append(reverse_edge)
            way_ids.add(int(row.way_id))
            edge_count += 1

    graph_nodes = {node_id: node_lookup[node_id] for node_id in adjacency}

    return OSMVoltageGraph(
        voltage_kv=float(voltage_kv),
        include_unknown_voltage=include_unknown_voltage,
        adjacency=dict(adjacency),
        nodes=graph_nodes,
        edge_count=edge_count,
        way_ids=way_ids,
    )


def graph_summary(graph: OSMVoltageGraph) -> dict[str, Any]:
    return {
        "voltage_kv": graph.voltage_kv,
        "include_unknown_voltage": graph.include_unknown_voltage,
        "nodes": graph.node_count,
        "edges": graph.edge_count,
        "ways": len(graph.way_ids),
    }


def snap_point_to_graph(
    graph: OSMVoltageGraph,
    lon: float,
    lat: float,
    *,
    max_km: float = 2.0,
) -> dict[str, Any] | None:
    """Find the nearest graph node to a resolved endpoint coordinate."""
    best: dict[str, Any] | None = None

    for node_id, coord in graph.nodes.items():
        distance_km = haversine_km(lon, lat, coord["lon"], coord["lat"])
        if best is None or distance_km < best["distance_km"]:
            best = {
                "node_id": int(node_id),
                "distance_km": distance_km,
                "lon": coord["lon"],
                "lat": coord["lat"],
            }

    if best is None or best["distance_km"] > max_km:
        return None
    return best


def shortest_path(
    graph: OSMVoltageGraph,
    start_node: int,
    end_node: int,
    *,
    max_route_km: float = 500.0,
    max_hops: int = 20000,
) -> dict[str, Any] | None:
    """Dijkstra shortest path using segment length in kilometers."""
    start_node = int(start_node)
    end_node = int(end_node)
    queue: list[tuple[float, int, int]] = [(0.0, 0, start_node)]
    best_distance: dict[int, float] = {start_node: 0.0}
    previous: dict[int, tuple[int, dict[str, Any]]] = {}

    while queue:
        distance_km, hops, node = heapq.heappop(queue)

        if distance_km != best_distance.get(node):
            continue
        if node == end_node:
            break
        if distance_km > max_route_km or hops >= max_hops:
            continue

        for edge in graph.adjacency.get(node, []):
            neighbor = int(edge["v"])
            candidate = distance_km + float(edge["length_km"])

            if candidate > max_route_km:
                continue

            if candidate < best_distance.get(neighbor, float("inf")):
                best_distance[neighbor] = candidate
                previous[neighbor] = (node, edge)
                heapq.heappush(queue, (candidate, hops + 1, neighbor))

    if end_node not in best_distance:
        return None

    node_path = [end_node]
    edge_path: list[dict[str, Any]] = []
    current = end_node

    while current != start_node:
        prior_node, edge = previous[current]
        edge_path.append(edge)
        node_path.append(prior_node)
        current = prior_node

    node_path.reverse()
    edge_path.reverse()

    return {
        "distance_km": best_distance[end_node],
        "node_path": node_path,
        "edges": edge_path,
        "hops": len(edge_path),
    }


def route_geometry(graph: OSMVoltageGraph, node_path: list[int]) -> dict[str, Any]:
    coordinates = [
        [graph.nodes[int(node_id)]["lon"], graph.nodes[int(node_id)]["lat"]]
        for node_id in node_path
    ]
    return {"type": "LineString", "coordinates": coordinates}


def route_between_points(
    graph: OSMVoltageGraph,
    start_lon: float,
    start_lat: float,
    end_lon: float,
    end_lat: float,
    *,
    snap_max_km: float = 2.0,
    max_route_km: float = 500.0,
) -> dict[str, Any] | None:
    """Snap two resolved endpoints to the graph and route through traced OSM geometry."""
    start_snap = snap_point_to_graph(graph, start_lon, start_lat, max_km=snap_max_km)
    end_snap = snap_point_to_graph(graph, end_lon, end_lat, max_km=snap_max_km)

    if start_snap is None or end_snap is None:
        return None

    path = shortest_path(
        graph,
        start_snap["node_id"],
        end_snap["node_id"],
        max_route_km=max_route_km,
    )
    if path is None:
        return None

    edges = path["edges"]
    way_ids = list(dict.fromkeys(int(edge["way_id"]) for edge in edges))
    names = list(dict.fromkeys(edge["name"] for edge in edges if edge.get("name")))
    operators = list(dict.fromkeys(edge["operator"] for edge in edges if edge.get("operator")))
    unknown_edge_count = sum(bool(edge.get("unknown_voltage")) for edge in edges)

    return {
        "route_status": "review_required" if unknown_edge_count else "resolved",
        "route_method": (
            "osm_voltage_graph_with_unknown_edges"
            if unknown_edge_count
            else "osm_voltage_graph"
        ),
        "voltage_kv": graph.voltage_kv,
        "distance_km": path["distance_km"],
        "hops": path["hops"],
        "start_snap_km": start_snap["distance_km"],
        "end_snap_km": end_snap["distance_km"],
        "start_osm_node": start_snap["node_id"],
        "end_osm_node": end_snap["node_id"],
        "osm_way_ids": way_ids,
        "way_names": names,
        "operators": operators,
        "unknown_voltage_edge_count": unknown_edge_count,
        "geometry": route_geometry(graph, path["node_path"]),
        "source": "OpenStreetMap",
        "attribution": "© OpenStreetMap contributors",
        "license": "ODbL",
    }


def degree_table(graph: OSMVoltageGraph) -> pd.DataFrame:
    """Return graph degree data for inspecting unlabeled tap/junction candidates."""
    rows = []

    for node_id, edges in graph.adjacency.items():
        coord = graph.nodes[node_id]
        rows.append({
            "node_id": node_id,
            "degree": len(edges),
            "lon": coord["lon"],
            "lat": coord["lat"],
            "way_ids": sorted({int(edge["way_id"]) for edge in edges}),
        })

    if not rows:
        return pd.DataFrame(columns=["node_id", "degree", "lon", "lat", "way_ids"])

    return (
        pd.DataFrame(rows)
        .sort_values(["degree", "node_id"], ascending=[False, True])
        .reset_index(drop=True)
    )
