"""State-based public infrastructure readers; reuse raw downloads before requesting data."""

import json
import re
import requests
from gridlock.paths import RAW_DATA_DIR

OSM_DIR = RAW_DATA_DIR / "osm"
OVERPASS_URL = "https://overpass.kumi.systems/api/interpreter"
HIFLD_URL = "https://services.arcgis.com/HQ0xoN0EzDPBOEci/ArcGIS/rest/services/Substations/FeatureServer/0/query"

def normalize_state(state):
    """Require a two-letter code supplied by the project data; no default state."""
    state = str(state).strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", state):
        raise ValueError("Expected a two-letter state code, got: " + repr(state))
    return state


def build_overpass_query(state, power):
    state = normalize_state(state)
    if power not in ["substation", "line"]:
        raise ValueError("Expected substation or line")
    return f'''[out:json][timeout:180];
area["ISO3166-2"="US-{state}"]["admin_level"="4"]->.search_state;
nwr["power"="{power}"](area.search_state);
out geom;'''


def fetch_osm(state, power, stem):
    """Download one statewide dataset, or reuse the saved original response."""
    state = normalize_state(state)
    state_dir = OSM_DIR / state
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / (stem + "_overpass.json")
    query = build_overpass_query(state, power)
    query_path = state_dir / (stem + "_query.overpassql")
    if query_path.exists() and query_path.read_text(encoding="utf-8") != query:
        raise ValueError("Saved query differs. Use a new cache name for a different query.")
    query_path.write_text(query, encoding="utf-8")
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        response = requests.post(
            OVERPASS_URL, data={"data": query}, timeout=240,
            headers={"User-Agent": "GridLock-learning-project/0.1"},
        )
        # Keep failed responses for diagnosis, but never use them as valid data.
        if not response.ok:
            (state_dir / (stem + "_error.txt")).write_text(response.text, encoding="utf-8")
        response.raise_for_status()
        data = response.json()
        if data.get("remark") or "elements" not in data:
            (state_dir / (stem + "_error.txt")).write_text(response.text, encoding="utf-8")
            raise ValueError("Incomplete Overpass response: " + str(data.get("remark")))
        with path.open("xb") as file:
            file.write(response.content)
    if data.get("remark") or not isinstance(data.get("elements"), list):
        raise ValueError("Cached Overpass response is incomplete")
    return data


def fetch_osm_substations(state):
    return fetch_osm(state, "substation", "power_substations")


def fetch_osm_lines(state):
    return fetch_osm(state, "line", "power_lines")


def osm_geometry(element, power):
    """Keep OSM coordinates, in GeoJSON longitude/latitude order; no centroids."""
    if element["type"] == "node":
        if "lon" in element and "lat" in element:
            return {"type": "Point", "coordinates": [element["lon"], element["lat"]]}
        return None
    if element["type"] == "way":
        points = element.get("geometry", [])
        if len(points) < 2 or any("lon" not in p or "lat" not in p for p in points):
            return None
        coordinates = [[p["lon"], p["lat"]] for p in points]
        if power == "substation" and len(coordinates) >= 4 and coordinates[0] == coordinates[-1]:
            return {"type": "Polygon", "coordinates": [coordinates]}
        return {"type": "LineString", "coordinates": coordinates}
    if element["type"] == "relation":
        # Preserve member shapes rather than guessing how to assemble multipolygons.
        geometries = []
        for member in element.get("members", []):
            geometry = osm_geometry(member, "line")
            if geometry:
                geometries.append(geometry)
        if geometries:
            return {"type": "GeometryCollection", "geometries": geometries}
    return None


def fetch_hifld_substations(state):
    """Reuse notebook 07's cache; preserve raw pages for new state downloads."""
    state = normalize_state(state)
    folder = RAW_DATA_DIR / "public_gis" / "endpoint_points"
    path = folder / f"hifld_substations_{state}.geojson"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    folder.mkdir(parents=True, exist_ok=True)
    features = []
    offset = 0
    while True:
        page_path = folder / f"hifld_substations_{state}_{offset}.json"
        if page_path.exists():
            data = json.loads(page_path.read_text(encoding="utf-8"))
        else:
            response = requests.get(HIFLD_URL, params={
                "where": f"STATE = '{state}'", "outFields": "*", "outSR": 4326,
                "returnGeometry": "true", "f": "geojson", "resultOffset": offset,
                "resultRecordCount": 2000, "orderByFields": "FID",
            }, timeout=120, headers={"User-Agent": "GridLock-project-geolocation/1.0"})
            response.raise_for_status()
            data = response.json()
            if "features" not in data:
                raise ValueError("Invalid HIFLD response: " + str(data.get("error")))
            page_path.write_bytes(response.content)
        batch = data["features"]
        features.extend(batch)
        if not data.get("exceededTransferLimit") and len(batch) < 2000:
            break
        if not batch:
            raise ValueError("HIFLD returned an incomplete page")
        offset += len(batch)
    collection = {"type": "FeatureCollection", "features": features}
    with path.open("x", encoding="utf-8") as file:
        json.dump(collection, file)
    return collection


def load_infrastructure(states, download_missing=False):
    """Return named physical facilities, grouped by the source state, for all operators.

    Missing caches are reported; offline runs never silently imply a complete search.
    Line routing is deliberately not required to locate a project.
    """
    features = []
    for state in sorted(set(states)):
        state = normalize_state(state)
        osm_path = OSM_DIR / state / "power_substations_overpass.json"
        hifld_path = RAW_DATA_DIR / "public_gis" / "endpoint_points" / f"hifld_substations_{state}.geojson"
        if download_missing:
            if not osm_path.exists():
                fetch_osm_substations(state)
            if not hifld_path.exists():
                fetch_hifld_substations(state)
        if not osm_path.exists():
            print(f"{state}: no statewide OSM station cache; using other available sources.")
        if not hifld_path.exists():
            print(f"{state}: no HIFLD station cache; set download_missing=True to fetch it.")
        seen = set()
        # Include the named-facility cache from notebook 07 when present.
        for path in sorted((OSM_DIR / state).glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            for element in data.get("elements", []):
                tags = element.get("tags", {})
                kind = tags.get("power", "")
                if kind not in ["substation", "plant", "switchyard", "switch"]:
                    continue
                name = tags.get("name", "")
                feature_id = f"{element['type']}/{element['id']}"
                if not name or feature_id in seen:
                    continue
                geometry = osm_geometry(element, "substation")
                if not geometry:
                    continue
                seen.add(feature_id)
                names = [name]
                for key in ["alt_name", "official_name", "short_name"]:
                    names.extend(n.strip() for n in tags.get(key, "").split(";") if n.strip())
                features.append({"name": name, "names": names, "state": state,
                                 "geometry": geometry, "feature_id": feature_id, "kind": kind,
                                 "voltage": tags.get("voltage", ""), "source": "osm",
                                 "source_url": "https://www.openstreetmap.org/" + feature_id,
                                 "source_file": str(path), "review_required": kind != "substation"})
        if hifld_path.exists():
            data = json.loads(hifld_path.read_text(encoding="utf-8"))
            for feature in data["features"]:
                props = {k.lower(): v for k, v in feature["properties"].items()}
                name = props.get("name") or ""
                kind = str(props.get("type", "")).lower()
                if (str(props.get("state", "")).upper() != state or kind != "substation"
                        or not name or name.upper().startswith(("UNKNOWN", "NOT AVAILABLE"))):
                    continue
                geometry = feature.get("geometry")
                if not geometry or geometry["type"] != "Point":
                    continue
                features.append({"name": name, "names": [name], "state": state, "geometry": geometry,
                                 "feature_id": "hifld/" + str(props.get("id", feature.get("id"))),
                                 "kind": kind, "voltage": "", "source": "hifld_substation",
                                 "source_url": HIFLD_URL, "source_file": str(hifld_path),
                                 "review_required": True})
    return features


