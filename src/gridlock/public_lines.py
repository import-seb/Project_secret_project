"""The statewide public line fallback from the original DESC asset workflow."""
import json
import heapq
import requests
from gridlock.paths import RAW_DATA_DIR
from gridlock.asset_sources import normalize_state
from gridlock.geolocation import distance_km, geometry_points

GIS_DIR = RAW_DATA_DIR / "public_gis"
NETL_URL = "https://arcgis.netl.doe.gov/server/rest/services/Hosted/Energy_Transition_Atlas_493d6/FeatureServer/18"
CENSUS_URL = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0/query"

def download_json(url, path, params=None):
    """Reuse a saved response; never replace an original download."""
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        response = requests.get(url, params=params, timeout=180)
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise ValueError(data["error"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as file:
            file.write(response.content)
    if "error" in data:
        raise ValueError(data["error"])
    return data


def fetch_public_lines(state):
    """Download all NETL line features intersecting this state's Census bounds.

    The rectangle can include neighboring-state features. It is a search area,
    not a claim about ownership or a match. Source endpoints/voltage still matter.
    ArcGIS sends at most 2,000 rows, so request successive pages until complete.
    """
    state = normalize_state(state)
    bounds = download_json(CENSUS_URL, GIS_DIR / f"{state}_boundary_census.json", {
        "where": f"STUSAB='{state}'", "returnExtentOnly": "true", "outSR": 4326, "f": "json",
    })["extent"]
    if not bounds or "xmin" not in bounds:
        raise ValueError("No Census boundary found for " + state)
    download_json(NETL_URL, GIS_DIR / "netl_transmission_metadata.json", {"f": "json"})
    params = {
        "where": "1=1", "geometry": json.dumps(bounds), "geometryType": "esriGeometryEnvelope",
        "inSR": 4326, "outSR": 4326, "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*", "returnGeometry": "true", "orderByFields": "objectid_1",
        "resultRecordCount": 2000, "resultOffset": 0, "f": "json",
    }
    features = []
    while True:
        path = GIS_DIR / state / f"netl_lines_{params['resultOffset']}.json"
        data = download_json(NETL_URL + "/query", path, params)
        if "features" not in data:
            raise ValueError("Line response has no features")
        for feature in data["features"]:
            tags = feature["attributes"]
            paths = feature.get("geometry", {}).get("paths", [])
            # Use source vertices as supplied; do not connect endpoints ourselves.
            geometry = {"type": "MultiLineString", "coordinates": paths} if paths else None
            name = f"{tags.get('sub_1', '')} - {tags.get('sub_2', '')}"
            voltage = tags.get("voltage")
            features.append({
                "state": state, "asset_type": "line", "feature_id": "netl/" + str(tags["objectid_1"]),
                "feature_name": name, "names": [name], "geometry": geometry,
                "voltage_kv": [voltage] if voltage and voltage > 0 else [], "tags": tags,
                "source": "public_gis", "source_url": NETL_URL + "/" + str(tags["objectid_1"]),
                "source_file": str(path),
            })
        if not data.get("exceededTransferLimit"):
            break
        if not data["features"]:
            raise ValueError("Incomplete paginated line response")
        params["resultOffset"] += len(data["features"])
    return features


def trace_public_path(asset, start, end, lines):
    """Notebook 07's endpoint-connected GIS fallback, retaining source vertices.

    Rounded GIS endpoints (~100 m) are only candidate connectivity, not proven
    electrical connections. Source features may extend beyond project work limits.
    """
    voltage = float(asset["voltage_kv"])
    network = []
    for line in lines:
        if voltage not in line["voltage_kv"] or not line["geometry"]:
            continue
        # Disconnected parts of one source feature must not become a junction.
        for part in line["geometry"]["coordinates"]:
            if len(part) >= 2:
                network.append(dict(line, geometry={"type": "MultiLineString", "coordinates": [part]}))
    touching = {}
    endpoints = {}
    lengths = {}
    starts, ends = [], []
    for i, line in enumerate(network):
        parts = line["geometry"]["coordinates"]
        endpoints[i] = set()
        lengths[i] = sum(distance_km(a, b) for part in parts for a, b in zip(part, part[1:]))
        for part in parts:
            if len(part) < 2:
                continue
            for point in [part[0], part[-1]]:
                node = (round(point[0], 3), round(point[1], 3))
                endpoints[i].add(node)
                touching.setdefault(node, []).append(i)
        points = geometry_points(line["geometry"])
        if not points:
            continue
        for anchor, found in [(start, starts), (end, ends)]:
            distance = min(distance_km(anchor["coordinates"], p) for p in points)
            if distance <= 2:
                found.append((distance, i))
    starts, ends = sorted(starts)[:5], sorted(ends)[:5]
    if not starts or not ends:
        return None
    end_ids = {i for _, i in ends}
    queue = [(lengths[i], [i]) for _, i in starts]
    heapq.heapify(queue)
    best = {}
    while queue:
        distance, path = heapq.heappop(queue)
        current = path[-1]
        if distance > 400 or len(path) > 40:
            continue
        if distance >= best.get(current, float("inf")):
            continue
        best[current] = distance
        if current in end_ids:
            chosen = [network[i] for i in path]
            # Never draw the rounded endpoint connections as new line geometry.
            return {
                "geometry": {"type": "MultiLineString", "coordinates": [
                    part for line in chosen for part in line["geometry"]["coordinates"]]},
                "route_method": "public_gis_endpoint_graph", "distance_km": distance,
                "source": "public_gis", "source_url": NETL_URL,
                "segment_feature_ids": [line["feature_id"] for line in chosen],
                "source_tags": [line["tags"] for line in chosen],
                "route_file": ";".join(dict.fromkeys(line["source_file"] for line in chosen)),
                "start_snap_km": next(d for d, i in starts if i == path[0]),
                "end_snap_km": next(d for d, i in ends if i == path[-1]),
            }
        for node in endpoints[current]:
            for neighbor in touching[node]:
                if neighbor not in path:
                    heapq.heappush(queue, (distance + lengths[neighbor], path + [neighbor]))
    return None


