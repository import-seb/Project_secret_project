"""Cached public geographic sources. Every geometry comes from a source object."""

import json
import re

import requests

from gridlock.paths import RAW_DATA_DIR
from gridlock.geolocation import normalize_state, osm_geometry
from gridlock.location_discovery import fetch_all_power

GIS_DIR = RAW_DATA_DIR / "public_gis"
NETL_URL = "https://arcgis.netl.doe.gov/server/rest/services/Hosted/Energy_Transition_Atlas_493d6/FeatureServer/18"
CENSUS_URL = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer/0/query"
DOMINION_INDEX = "https://www.dominionenergy.com/about/delivering-energy/electric-projects/power-line-projects"


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


def fetch_osm_assets(state):
    """Use the bulk statewide downloads, keeping tags needed to check identity."""
    state = normalize_state(state)
    fetch_all_power(state)
    features = []
    folder = RAW_DATA_DIR / "osm" / state
    for filename in ["power_substations_overpass.json", "power_lines_overpass.json",
                     "power_plants_switchyards_overpass.json"]:
        data = json.loads((folder / filename).read_text(encoding="utf-8"))
        for element in data["elements"]:
            tags = element.get("tags", {})
            power = tags.get("power", "")
            geometry = osm_geometry(element, "line" if power == "line" else "substation")
            names = []
            for key in ["name", "official_name", "alt_name", "old_name", "short_name", "ref"]:
                names.extend([n.strip() for n in tags.get(key, "").split(";") if n.strip()])
            features.append({
                "state": state, "asset_type": "line" if power == "line" else "station",
                "feature_id": f"{element['type']}/{element['id']}",
                "feature_name": tags.get("name", tags.get("ref", "")),
                "names": names, "geometry": geometry, "tags": tags,
                "voltage_kv": [float(v) / 1000 for v in re.findall(r"\d+(?:\.\d+)?", tags.get("voltage", ""))],
                "source": "overpass", "source_url": f"https://www.openstreetmap.org/{element['type']}/{element['id']}",
                "source_file": str(folder / filename),
            })
    return features


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


def fetch_official_maps():
    """Read the official utility project index in bulk and save linked map files.

    These files are map evidence, not georeferenced line matches. This source is
    Dominion-specific; statewide OSM and NETL downloads remain operator-neutral.
    """
    GIS_DIR.mkdir(parents=True, exist_ok=True)
    path = GIS_DIR / "dominion_project_index.html"
    if not path.exists():
        response = requests.get(DOMINION_INDEX, timeout=90)
        response.raise_for_status()
        path.write_bytes(response.content)
    text = path.read_text(encoding="utf-8").split("document.projectListings =", 1)[1].lstrip()
    index = json.JSONDecoder().raw_decode(text)[0]
    records = []
    folder = GIS_DIR / "dominion_maps"
    folder.mkdir(exist_ok=True)
    for region in index["Regions"]:
        # This index spells out state names; it is not used to choose OSM areas.
        if "South Carolina" not in region["RegionTitle"]:
            continue
        for project in region["Projects"]:
            url = (project.get("ProjectPageUrl") or "").replace("http:", "https:")
            if not url:
                continue
            stem = url.rsplit("/", 1)[-1].lower()
            page = folder / (stem + ".html")
            if not page.exists():
                response = requests.get(url, timeout=90)
                response.raise_for_status()
                page.write_bytes(response.content)
            html = page.read_text(encoding="utf-8")
            links = re.findall(r'(?:src|href)="([^"]+)"', html)
            maps = []
            for link in dict.fromkeys(links):
                link = link.replace("&amp;", "&")
                lower = link.lower()
                if not link.startswith("https://") or "power-line-projects/" not in lower:
                    continue
                if not any(word in lower for word in ["map", "route", "study-aerial"]):
                    continue
                filename = link.split("?")[0].rsplit("/", 1)[-1]
                if not filename.lower().endswith((".pdf", ".jpg", ".png")):
                    continue
                output = folder / (stem + "_" + filename)
                if not output.exists():
                    response = requests.get(link, timeout=180)
                    response.raise_for_status()
                    with output.open("xb") as file:
                        file.write(response.content)
                maps.append({"url": link, "path": str(output)})
            records.append({"title": project["ProjectTitle"], "purpose": project["Purpose"],
                            "url": url, "page": str(page), "maps": maps})
    (folder / "map_index.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    return records
