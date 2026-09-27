"""Project locations from public facilities and preserved routes; no route reconstruction."""

import json
import math
import re
import pandas as pd
from gridlock.paths import PROCESSED_DATA_DIR
from gridlock.asset_sources import normalize_state, load_infrastructure

# These are parsing words, not a list of project-specific locations.
ELECTRICAL_WORDS = """substation substations station stations sub transmission distribution
line lines loop rebuild rebuilding construct constructing construction tap tie ties fold
in spdc spsc acsr acss b1272 b795 conductor conductors circuit circuits terminal terminals
upgrade upgrades upgrading convert converting new existing replace replacing replacement
move modify add adding install installing installation extend expand portion portions
section sections phase phases project projects customer supporting self steel structures
structure pole poles wood wooden laminated bus bank autobank transformer transformers
reactor reactors relay relays panel panels switch switches switching house equipment
single double supporting string restring insulated insulation breaker breakers goab
gpc desc descsq dep scdot us78 cip hut mva tbd approx approximately miles mile length
total standard end life required service need date streetlight and from to between at
the for with into off on of will be is are as both by this that includes include
roadway widening funded funded impact medium countywide no work northbound southbound
verify modification modifications energy duke progress sw sta large angles dead ends
polls series laminated supporting hardening system estimated str strs current future
because replace room plant bay bays feeder feeders underbuild underbuilt notes
set pull most we there due designed bec point across scope ibis interconnection way frame osmose crossing
""".split()
ELECTRICAL_WORDS.remove("house")  # 'Red House Road' is a real place phrase.


ELECTRICAL_WORDS += "primary capacitor capacitors statcom system reconductor reconductoring protection modernization improvements installation cc grid bank banks low side series dual stage panel panels control needs project loop upgrade rating ratings".split()


def normalize_name(name):
    name = str(name).lower().replace("’", "'")
    name = re.sub(r"\b\d+(?:\.\d+)?(?:\s*[-/]\s*\d+(?:\.\d+)?)*\s*kv\b", " ", name)
    name = re.sub(r"\bst[.]?(?=\s)", "saint", name)
    name = re.sub(r"\bst[.]?$", "street", name)
    name = re.sub(r"\bjct\b", "junction", name)
    name = re.sub(r"\brd\b", "road", name)
    name = re.sub(r"\bn\.\s*", "north ", name)
    name = re.sub(r"\b(substation|substations|station|sub|switchyard|switching|transmission|distribution|line|lines|primary)\b", " ", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return " ".join(name.split())


def extract_endpoint_names(project):
    """Read capitalized place phrases from both source fields; discard electrical text."""
    names = []
    for field in ["project_name", "description"]:
        text = str(project.get(field, ""))
        text = text.replace("–", "-").replace("—", "-").replace("�", "-")
        text = re.sub(r"\b(St|Jct|Rd)\.", r"\1", text)
        text = re.sub(r"\bN\.\s*", "North ", text)
        text = re.sub(r"\bswitch(?:ing)?\s+house\b", " | ", text, flags=re.I)
        text = re.sub(r"\b\d+(?:\.\d+)?(?:\s*[-/]\s*\d+(?:\.\d+)?)*\s*kv\b", " | ", text, flags=re.I)
        text = re.sub(r"\b(?:" + "|".join(ELECTRICAL_WORDS) + r")\b", " | ", text, flags=re.I)
        # Names in these source documents are capitalized, including VCS1 and PSA.
        for phrase in re.findall(r"\b[A-Z][A-Za-z0-9']*(?:[ \t]+[A-Z][A-Za-z0-9']*)*", text):
            name = normalize_name(phrase)
            if name and len(name) > 1 and name not in ["road", "river", "marsh", "house"] and name not in names:
                names.append(name)
    # Do not count a shortened road name twice within a project.
    return [name for name in names if not (
        len(name.split()) >= 2 and name + " road" in names)]


def extract_actions(project_name):
    return list(dict.fromkeys(re.findall(r"\b(replace|construct|rebuild|upgrade)\b", str(project_name).lower())))


def get_action_type(project_name):
    actions = extract_actions(project_name)
    for action in actions:
        if action in ["replace", "rebuild", "upgrade"]:
            return action
    return "construct" if "construct" in actions else "other"


def distance_km(a, b):
    """Haversine distance from notebooks 07/09; points are [longitude, latitude]."""
    lon1, lat1, lon2, lat2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    value = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0088 * 2 * math.asin(min(1, math.sqrt(value)))


def geometry_points(geometry):
    if not geometry:
        return []
    kind = geometry["type"]
    coordinates = geometry.get("coordinates", [])
    if kind == "Point":
        return [coordinates]
    if kind in ["LineString", "MultiPoint"]:
        return coordinates
    if kind in ["MultiLineString", "Polygon"]:
        return [p for part in coordinates for p in part]
    if kind == "MultiPolygon":
        return [p for polygon in coordinates for ring in polygon for p in ring]
    if kind == "GeometryCollection":
        return [p for g in geometry["geometries"] for p in geometry_points(g)]
    return []


def geometry_center(geometry):
    """A labeled map-center approximation; does not replace the source geometry."""
    points = geometry_points(geometry)
    if not points:
        return None
    return [(min(p[0] for p in points) + max(p[0] for p in points)) / 2,
            (min(p[1] for p in points) + max(p[1] for p in points)) / 2]


def project_midpoint(a, b):
    """Coordinate midpoint for regional project screening, never a route."""
    return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]


def flag_primary_source_requirements(project):
    text = " ".join(str(project.get(k, "")) for k in ["project_name", "description"])
    reasons = []
    if re.search(r"\b(?:structures?|strs?)\.?\s*#?\s*\d+", text, re.I):
        reasons.append("numbered structures")
    if re.search(r"\bDESCSQ\s*#?\s*\d+|\bGOAB\b", text, re.I):
        reasons.append("internal engineering identifier or switch location")
    for name in ["frogmore transmission", "cae industrial park"]:
        if name in text.lower():
            reasons.append("internal facility name: " + name)
    if re.search(r"\btap point\b", text, re.I):
        reasons.append("internal tap point")
    return "; ".join(reasons)


def load_endpoint_evidence(utility):
    """Reuse named public facilities from 07, not guessed points or line-terminal clusters."""
    if utility != "DESC":
        return []
    path = PROCESSED_DATA_DIR / "dominion/reference/dominion_endpoint_registry.csv"
    if not path.exists():
        return []
    result = []
    for row in pd.read_csv(path, dtype=str, keep_default_na=False).to_dict("records"):
        if (row["found"].lower() != "true" or row["name_match"] != "exact"
                or row["source"] not in ["osm", "hifld_substation", "review_named_candidate"]):
            continue
        if flag_primary_source_requirements({"project_name": row["endpoint_name"]}):
            continue
        result.append({"name": row["resolved_name"], "names": [row["endpoint_name"], row["resolved_name"]],
                       "state": row["state"], "geometry": {"type": "Point", "coordinates": [float(row["longitude"]), float(row["latitude"])]},
                       "feature_id": row["feature_id"], "kind": row["feature_type"], "voltage": "",
                       "source": "saved_endpoint_registry", "source_file": str(path),
                       "source_url": "https://www.openstreetmap.org/" + row["feature_id"] if row["feature_id"].startswith(("way/", "node/", "relation/")) else "",
                       "review_required": True})
    return result


def find_project_locations(project, features):
    """Exact normalized facility names only; distant duplicate names stay ambiguous."""
    title_names = extract_endpoint_names({"project_name": project["project_name"]})
    all_names = extract_endpoint_names(project)
    text = " " + normalize_name(project["project_name"] + " " + project.get("description", "")) + " "
    raw_text = (project["project_name"] + " " + project.get("description", "")).lower()
    blocked_names = [normalize_name(name) for name in ["Frogmore Transmission", "CAE Industrial Park"] if name.lower() in raw_text]
    groups = {}
    for feature in features:
        if feature["state"] != project["state"]:
            continue
        for name in feature["names"]:
            key = normalize_name(name)
            if (not key or key in blocked_names or " " + key + " goab " in text
                    or flag_primary_source_requirements({"project_name": name})):
                continue
            # Multiword published names can be read directly from prose. Single
            # words need an extracted name to avoid matching ordinary prose.
            if key in all_names or (len(key.split()) >= 2 and " " + key + " " in text):
                groups.setdefault(key, []).append(feature)
    locations = []
    ambiguous = []
    for key, candidates in groups.items():
        centers = [geometry_center(c["geometry"]) for c in candidates]
        if any(p is None for p in centers):
            continue
        if any(distance_km(centers[0], p) > 2 for p in centers[1:]):
            ambiguous.append(key)
            continue
        # Prefer published OSM facility geometry; retain alternative evidence.
        candidates.sort(key=lambda c: (c["source"] != "osm", c["kind"] != "substation"))
        feature = candidates[0]
        center = geometry_center(feature["geometry"])
        if any(distance_km(center, p["coordinates"]) < 0.05 for p in locations):
            continue
        locations.append({"endpoint_name": key, "feature_name": feature["name"], "coordinates": center,
                          "feature_id": feature["feature_id"], "source": feature["source"],
                          "source_url": feature["source_url"], "source_file": feature["source_file"],
                          "point_method": "published_point" if feature["geometry"]["type"] == "Point" else "facility_bounds_center",
                          "review_required": feature["review_required"], "in_title": key in title_names})
    locations.sort(key=lambda p: (not p["in_title"], title_names.index(p["endpoint_name"]) if p["endpoint_name"] in title_names else 999))
    return locations, ambiguous


def geolocate_projects(projects, utility, infrastructure=None):
    """One output row per input project; retain original timing and provenance."""
    projects = projects.fillna("").copy()
    projects["state"] = projects["state"].apply(normalize_state)
    if infrastructure is None:
        infrastructure = load_infrastructure(projects["state"])
    features = list(infrastructure) + load_endpoint_evidence(utility)
    rows = []
    for project in projects.to_dict("records"):
        row = dict(project)
        row["utility"] = utility
        page = str(project.get("source_page", project.get("source_pdf_page", "")))
        row["project_key"] = f"{project['state']}:{project.get('source_file', '')}:{page}:{project['project_id']}"
        row["action_type"] = get_action_type(project["project_name"])
        row["detected_actions"] = ";".join(extract_actions(project["project_name"]))
        reason = flag_primary_source_requirements(project)
        locations, ambiguous = find_project_locations(project, features)
        row.update({"endpoint_a": locations[0]["endpoint_name"] if locations else "",
                    "endpoint_b": locations[1]["endpoint_name"] if len(locations) > 1 else "",
                    "longitude": None, "latitude": None, "geometry": None, "geometry_type": "",
                    "location_method": "unresolved", "point_location_method": "unresolved", "confidence": "low", "review_required": True,
                    "source": "", "location_source_url": "", "location_source_file": "",
                    "resolution_requirement": "requires_primary_source" if reason else "standard_public_gis",
                    "review_note": reason + ("; ambiguous facility names: " + ", ".join(ambiguous) if ambiguous else ""),
                    "endpoint_names": json.dumps(extract_endpoint_names(project)),
                    "matched_locations": json.dumps(locations), "route_evidence": "[]"})
        if locations:
            points = [p["coordinates"] for p in locations]
            if len(points) == 1:
                center, method = points[0], "single_endpoint"
            elif len(points) == 2:
                center, method = project_midpoint(*points), "endpoint_midpoint"
            else:
                center = geometry_center({"type": "MultiPoint", "coordinates": points})
                method = "multiple_facility_center"
            row.update({"longitude": center[0], "latitude": center[1], "geometry": {"type": "Point", "coordinates": center},
                        "geometry_type": "Point", "location_method": method,
                        "point_location_method": locations[0]["point_method"] if len(points) == 1 else method,
                        "confidence": "medium",
                        "source": ";".join(dict.fromkeys(p["source"] for p in locations)),
                        "location_source_url": ";".join(dict.fromkeys(p["source_url"] for p in locations if p["source_url"])),
                        "location_source_file": ";".join(dict.fromkeys(p["source_file"] for p in locations))})
            row["review_required"] = bool(reason or ambiguous or len(points) > 2 or any(p["review_required"] for p in locations))
            if len(points) == 1 and not row["review_required"] and locations[0]["in_title"]:
                row["confidence"] = "high"
        rows.append(row)
    result = pd.DataFrame(rows)
    if result["project_key"].duplicated().any():
        raise ValueError("Duplicate project keys: check source page and project ID")
    return result


def load_known_routes(utility):
    if utility != "DESC":
        return pd.DataFrame()
    folder = PROCESSED_DATA_DIR / "dominion/reference"
    frames = []
    for name in ["dominion_route_resolution_v3_tagged.csv", "dominion_osm_low_voltage_routes.csv"]:
        frame = pd.read_csv(folder / name, dtype=str, keep_default_na=False)
        frame["route_file"] = str(folder / name)
        if "project_key" not in frame:
            frame["project_key"] = frame["asset_key"].str.rsplit(":", n=1).str[0]
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).fillna("")


def apply_known_routes(projects, routes):
    """Use preserved public line vertices; no route solver is called here."""
    result = projects.copy()
    if routes.empty:
        return result
    for index, project in result.iterrows():
        saved = routes[routes["project_key"] == project["project_key"]]
        if "resolution_requirement" in saved and saved["resolution_requirement"].eq("requires_primary_source").any():
            result.at[index, "resolution_requirement"] = "requires_primary_source"
            result.at[index, "review_required"] = True
        matched = saved[saved["route_status"].isin(["resolved", "review_required"]) & saved["geometry"].ne("")]
        if matched.empty:
            continue
        geometries = [json.loads(value) for value in matched["geometry"]]
        if any(g["type"] not in ["LineString", "MultiLineString", "GeometryCollection"] for g in geometries):
            raise ValueError("Saved route contains a non-route geometry")
        geometry = geometries[0] if len(geometries) == 1 else {"type": "GeometryCollection", "geometries": geometries}
        center = geometry_center(geometry)
        evidence = matched.drop(columns="geometry").to_dict("records")
        result.at[index, "geometry"] = geometry
        result.at[index, "geometry_type"] = geometry["type"]
        result.at[index, "longitude"], result.at[index, "latitude"] = center
        result.at[index, "location_method"] = "saved_traced_route"
        result.at[index, "point_location_method"] = "route_bounds_center"
        result.at[index, "source"] = ";".join(dict.fromkeys(matched["route_method"]))
        result.at[index, "location_source_file"] = ";".join(dict.fromkeys(matched["route_file"]))
        result.at[index, "location_source_url"] = ""
        result.at[index, "route_evidence"] = json.dumps(evidence)
        result.at[index, "confidence"] = "medium"
        # The saved route is geographic evidence, not proof of every work limit.
        result.at[index, "review_required"] = True
        result.at[index, "review_note"] = (project["review_note"] + "; saved public route; exact work extent may differ").strip("; ")
        if not project["endpoint_a"]:
            result.at[index, "endpoint_a"] = matched.iloc[0]["endpoint_a"]
        if not project["endpoint_b"]:
            result.at[index, "endpoint_b"] = matched.iloc[0]["endpoint_b"]
    return result


def save_project_geolocation(projects, utility):
    folder_name = {"DESC": "dominion", "GPC": "georgia_power"}.get(utility, utility.lower())
    folder = PROCESSED_DATA_DIR / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (folder_name + "_projects_geo.csv")
    table = projects.copy()
    table["geometry"] = table["geometry"].apply(lambda g: json.dumps(g) if isinstance(g, dict) else "")
    table.to_csv(path, index=False)
    # Pandas converts missing values to null before JSON serialization.
    records = json.loads(projects.drop(columns="geometry").to_json(orient="records"))
    features = [{"type": "Feature", "geometry": geometry if isinstance(geometry, dict) else None, "properties": props}
                for geometry, props in zip(projects["geometry"], records)]
    path.with_suffix(".geojson").write_text(json.dumps({"type": "FeatureCollection", "features": features}, allow_nan=False), encoding="utf-8")
    return path


def find_overlap_candidates(desc, gpc, max_distance_km=25):
    """Nearest traced-route parts, falling back to points for projects without routes."""
    from gridlock.proximity import prepare_proximity, closest_positions
    if max_distance_km < 0:
        raise ValueError("Distance threshold must be nonnegative")
    columns = ["desc_project_key", "desc_project_id", "desc_project_name", "gpc_project_key", "gpc_project_id",
               "gpc_project_name", "distance_km", "desc_location_method", "gpc_location_method",
               "desc_in_service_date", "gpc_need_date", "desc_confidence", "gpc_confidence",
               "review_required", "distance_method", "timing_note",
               "desc_nearest_longitude", "desc_nearest_latitude", "gpc_nearest_longitude", "gpc_nearest_latitude"]
    rows = []
    desc_rows, gpc_rows, backward = prepare_proximity(desc, gpc)
    for a in desc_rows:
        for b in gpc_rows:
            distance, lon_a, lat_a, lon_b, lat_b = closest_positions(a, b, backward)
            if distance <= max_distance_km:
                rows.append([a["project_key"], a["project_id"], a["project_name"], b["project_key"], b["project_id"],
                             b["project_name"], distance, a["location_method"], b["location_method"],
                             a.get("in_service_date", ""), b.get("need_date", ""), a["confidence"], b["confidence"],
                             True, "nearest_route_or_point_local_meters", "Planning milestones do not establish concurrent construction.",
                             lon_a, lat_a, lon_b, lat_b])
    return pd.DataFrame(rows, columns=columns).sort_values("distance_km")


