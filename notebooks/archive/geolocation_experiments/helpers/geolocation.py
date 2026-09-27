"""Bulk OSM downloads by project state and reviewable location matching."""

import json
import re

import pandas as pd
import requests

from gridlock.paths import RAW_DATA_DIR, PROCESSED_DATA_DIR

OSM_DIR = RAW_DATA_DIR / "osm"
GEO_DIR = PROCESSED_DATA_DIR / "geo"
OVERPASS_URL = "https://overpass.private.coffee/api/interpreter"


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


def fetch_power_infrastructure(state):
    """Two bulk queries for this state, including all operators."""
    substations = convert_to_geojson(fetch_osm_substations(state), "power_substations", state)
    lines = convert_to_geojson(fetch_osm_lines(state), "power_lines", state)
    return substations + lines


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


def convert_to_geojson(data, stem, state):
    """Save all returned features, including unnamed ones or null geometries."""
    state = normalize_state(state)
    features = []
    for element in data["elements"]:
        tags = element.get("tags", {})
        geometry = osm_geometry(element, tags.get("power"))
        feature_id = element["type"] + "/" + str(element["id"])
        features.append({
            "type": "Feature", "id": feature_id, "geometry": geometry,
            "properties": {
                "state": state,
                "osm_feature_id": feature_id,
                "osm_feature_type": element["type"],
                "osm_feature_name": tags.get("name", ""),
                "osm_url": "https://www.openstreetmap.org/" + feature_id,
                "power": tags.get("power", ""),
                "operator": tags.get("operator", ""),
                "voltage": tags.get("voltage", ""),
                "tags": tags,
                "geometry_note": "relation member shapes, not an assembled area" if element["type"] == "relation" else "original OSM geometry",
            },
        })
    result = {"type": "FeatureCollection", "features": features,
              "attribution": "© OpenStreetMap contributors, ODbL 1.0",
              "source": OVERPASS_URL, "osm_base": data.get("osm3s", {}).get("timestamp_osm_base")}
    state_dir = OSM_DIR / state
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / (stem + ".geojson")
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return features


def normalize_name(name):
    """Ignore capitalization, punctuation, voltage labels and infrastructure words."""
    name = str(name).lower()
    name = re.sub(r"\b\d+(?:\.\d+)?(?:\s*[-/]\s*\d+(?:\.\d+)?)*\s*kv\b", " ", name)
    name = re.sub(r"\bst[.]?(?=\s)", "saint", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    name = re.sub(r"\b(substation|sub|transmission|switching|station|line|lines)\b", " ", name)
    return " ".join(name.split())


def project_targets(project):
    """Extract title location hints before the first voltage, not guessed locations."""
    title = project["project_name"].split(":")[0]
    title = re.split(r"\b\d+(?:\.\d+)?(?:\s*[-/]\s*\d+(?:\.\d+)?)*\s*kv\b", title, maxsplit=1, flags=re.I)[0]
    targets = []
    for name in re.split(r"\s*[-–—�]\s*", title):
        name = normalize_name(name)
        if name and name not in targets:
            targets.append(name)
    return targets


def find_project_matches(project, features):
    """Return candidates with evidence. A confidence score is NOT confirmation."""
    state = normalize_state(project["state"])
    targets = project_targets(project)
    title = normalize_name(project["project_name"])
    description = normalize_name(project.get("description", ""))
    project_voltages = []
    for group in re.findall(r"(\d+(?:\.\d+)?(?:\s*[-/]\s*\d+(?:\.\d+)?)*)(?:\s*)kv", project["project_name"], re.I):
        project_voltages.extend(float(v) * 1000 for v in re.findall(r"\d+(?:\.\d+)?", group))
    matches = []
    for feature in features:
        properties = feature["properties"]
        # Also enforce isolation here if a caller accidentally mixes state caches.
        if properties.get("state") != state:
            continue
        aliases = []
        for tag in ["name", "alt_name", "official_name", "short_name"]:
            aliases.extend(properties["tags"].get(tag, "").split(";"))
        method = None
        matched_name = ""
        target = ""
        for alias in aliases:
            normalized = normalize_name(alias)
            if not normalized:
                continue
            if normalized in targets:
                target = normalized
                matched_name = alias
                method = "endpoint_match" if len(targets) == 2 else "normalized_name"
                if alias.strip().lower() == project["project_name"].strip().lower():
                    method = "exact_name"
                break
            if normalized == title:
                matched_name, method = alias, "normalized_name"
                break
            if len(normalized) >= 4 and (" " + normalized + " " in " " + title + " " or
                                         " " + normalized + " " in " " + description + " "):
                matched_name, method = alias, "manual_review_needed"
            elif method is None:
                words = set(normalized.split())
                for name in targets:
                    other = set(name.split())
                    if len(words & other) >= 2 and len(words & other) / len(words | other) >= 0.5:
                        matched_name, method = alias, "fuzzy_name"
        if method is None:
            continue
        osm_voltages = [float(v) for v in re.findall(r"\d+(?:\.\d+)?", properties["voltage"])]
        voltage_agrees = bool(set(project_voltages) & set(osm_voltages))
        voltage_conflicts = bool(project_voltages and osm_voltages and not voltage_agrees)
        confidence = "medium" if method in ["exact_name", "normalized_name", "endpoint_match"] else "low"
        utility = normalize_name(project.get("utility", ""))
        operator = normalize_name(properties["operator"])
        operator_agrees = bool(utility and operator and (utility in operator or operator in utility))
        # Utility aliases are independent of the state used to select infrastructure.
        if "dominion" in utility:
            operator_agrees = bool(re.search(r"dominion|sce&g|sceg|south carolina electric|scana|^desc$", properties["operator"], re.I))
        evidence = [state + " statewide bulk query", "name evidence: " + matched_name]
        evidence.append("operator agrees with project utility" if operator_agrees else "operator not corroborated; review required")
        if voltage_agrees:
            evidence.append("OSM voltage agrees with a project voltage")
        elif voltage_conflicts:
            evidence.append("VOLTAGE CONFLICT: review required")
            confidence = "low"
        else:
            evidence.append("voltage corroboration unavailable")
        if "�" in project["project_name"]:
            evidence.append("source title contains a PDF text replacement character")
        if not feature["geometry"]:
            evidence.append("OSM geometry unavailable")
            confidence = "low"
        matches.append({
            "state": state,
            "project_key": project["project_key"], "project_id": project["project_id"],
            "project_name": project["project_name"],
            "osm_feature_name": properties["osm_feature_name"],
            "osm_feature_id": properties["osm_feature_id"],
            "osm_feature_type": properties["osm_feature_type"],
            "osm_url": properties["osm_url"], "power": properties["power"],
            "operator": properties["operator"], "voltage": properties["voltage"],
            "geometry": feature["geometry"], "match_method": method,
            "match_confidence": confidence, "name_target": target,
            "match_evidence": "; ".join(evidence), "confirmed": False,
            "confirmation_note": "", "voltage_agrees": voltage_agrees,
            "operator_agrees": operator_agrees,
        })
    # Competing OSM objects for the same endpoint remain uncertain.
    for match in matches:
        if match["name_target"]:
            count = sum(m["name_target"] == match["name_target"] for m in matches)
            if count > 1:
                match["match_confidence"] = "low"
                match["match_evidence"] += "; multiple features match this endpoint name"
            elif (match["voltage_agrees"] and match["operator_agrees"] and match["geometry"] and
                  len(match["name_target"]) >= 5 and "�" not in project["project_name"] and
                  " " + match["name_target"] + " " in " " + description + " "):
                match["match_confidence"] = "high"
                match["match_evidence"] += "; unique title endpoint also named in description; still needs confirmation"
    return matches


def build_project_geometry(matches):
    """Use only explicitly reviewed matches; never treat name scores as proof."""
    confirmed = [m for m in matches if m["confirmed"]]
    points = []
    point_methods = []
    for match in confirmed:
        geometry = match["geometry"]
        if match["power"] != "substation" or not geometry:
            continue
        if geometry["type"] == "Point":
            point = geometry["coordinates"]
            method = "original_osm_node"
        elif geometry["type"] == "Polygon":
            # A real boundary vertex, not a centroid. The full polygon is kept in matches.
            point = geometry["coordinates"][0][0]
            method = "osm_substation_boundary_vertex_proxy"
        else:
            continue
        if point not in points:
            points.append(point)
            point_methods.append(method)
    if len(points) == 2:
        return {"type": "LineString", "coordinates": points}, "endpoint_straight_line", "; ".join(point_methods)
    if len(points) == 1:
        return {"type": "Point", "coordinates": points[0]}, "single_confirmed_point", point_methods[0]
    if len(points) > 2:
        return None, "unresolved", "more than two confirmed sites; project scope needs review"
    # An explicit line confirmation must cover the project scope, not just the name.
    lines = [m for m in confirmed if m["power"] == "line" and m["geometry"] and
             m["geometry"]["type"] in ["LineString", "MultiLineString"]]
    if len(lines) == 1:
        return lines[0]["geometry"], "confirmed_osm_line", "original OSM line geometry"
    return None, "unresolved", "no usable confirmed geometry"


def save_results(projects, all_matches, output_prefix="dominion"):
    """Write one row per source project plus a separate table of every candidate."""
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", output_prefix):
        raise ValueError("Use letters, numbers, underscores or hyphens for the output prefix")
    GEO_DIR.mkdir(parents=True, exist_ok=True)
    features = []
    rows = []
    for project in projects:
        properties = dict(project)
        properties["state"] = normalize_state(properties["state"])
        geometry = properties.pop("geometry")
        features.append({"type": "Feature", "id": project["project_key"],
                         "properties": properties, "geometry": geometry})
        row = dict(properties)
        row["geometry"] = json.dumps(geometry, allow_nan=False)
        for key in ["osm_feature_id", "osm_feature_name", "osm_feature_type", "match_method"]:
            row[key] = json.dumps(row[key], ensure_ascii=False)
        rows.append(row)
    collection = {"type": "FeatureCollection", "features": features,
                  "attribution": "© OpenStreetMap contributors, ODbL 1.0",
                  "geometry_note": "Endpoint straight lines are proxies, not transmission routes. No centroid distances were calculated."}
    (GEO_DIR / (output_prefix + "_projects_geo.geojson")).write_text(
        json.dumps(collection, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    df = pd.DataFrame(rows)
    df.to_csv(GEO_DIR / (output_prefix + "_projects_geo.csv"), index=False)
    df[df["geometry_method"] == "unresolved"].to_csv(
        GEO_DIR / (output_prefix + "_unresolved_projects.csv"), index=False)
    candidate_rows = []
    for match in all_matches:
        row = dict(match)
        row["state"] = normalize_state(row["state"])
        row["geometry"] = json.dumps(row["geometry"], allow_nan=False)
        candidate_rows.append(row)
    columns = list(all_matches[0]) if all_matches else [
        "state", "project_key", "project_id", "project_name", "osm_feature_name", "osm_feature_id",
        "osm_feature_type", "geometry", "match_method", "match_confidence", "confirmed"]
    pd.DataFrame(candidate_rows, columns=columns).to_csv(
        GEO_DIR / (output_prefix + "_osm_match_candidates.csv"), index=False)
    return df
