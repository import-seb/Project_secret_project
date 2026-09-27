"""Asset requirements and source-object matches, without invented project routes."""

import json
import re
import time

import pandas as pd
import requests

from gridlock.paths import DATA_DIR, RAW_DATA_DIR, PROCESSED_DATA_DIR
from gridlock.location_discovery import normalize_endpoint, extract_endpoint_names, name_matches
from gridlock.geolocation import normalize_state

SCOPE_PATH = DATA_DIR / "reference" / "dominion_asset_scope.txt"
SNAPSHOT_PATH = DATA_DIR / "reference" / "dominion_scope_source.json"
REVIEW_PATH = PROCESSED_DATA_DIR / "geo" / "dominion_asset_review.csv"


def extract_actions(project_name):
    """Preserve all keywords in title order; do not classify description actions."""
    return list(dict.fromkeys(re.findall(r"\b(replace|construct|rebuild|upgrade)\b", project_name.lower())))


def get_action_type(project_name):
    actions = extract_actions(project_name)
    for action in actions:
        if action in ["replace", "rebuild", "upgrade"]:
            return action
    return "construct" if "construct" in actions else "other"


def extract_identifiers(project):
    """Collect clues, not proof that every mentioned place is a required asset."""
    text = project["project_name"] + ". " + project.get("description", "")
    voltages = []
    for group in re.findall(r"\b(\d+(?:\.\d+)?(?:\s*[/\-]\s*\d+(?:\.\d+)?)*)\s*kV\b", text, re.I):
        voltages.extend(re.findall(r"\d+(?:\.\d+)?", group))
    identifiers = re.findall(r"\bDESCSQ\s*#\s*\d+|\b(?:line|circuit)\s*(?:no\.?\s*)?#\s*[\w-]+", text, re.I)
    circuits = re.findall(r"\bkV\s*(?:Line\s*)?#\s*(\d+)|\band\s*#\s*(\d+)|&\s*#\s*(\d+)", text, re.I)
    circuit_numbers = [number for group in circuits for number in group if number]
    description = project.get("description", "")
    types = []
    if re.search(r"\blines?\b|reconductor|ACSR|conductor", description, re.I):
        types.append("line")
    if re.search(r"\b(?:substation|station|breaker|relay|transformer)s?\b", description, re.I):
        types.append("station")
    # Keep the words following 'at' for review without treating arbitrary prose as a location.
    at_clauses = re.findall(r"\bat\s+([^.;]+)", description, re.I)
    return {"name_clues": extract_endpoint_names(project), "line_identifiers": list(dict.fromkeys(identifiers)),
            "voltage_clues_kv": list(dict.fromkeys(voltages)), "circuit_clues": list(dict.fromkeys(circuit_numbers)),
            "description_asset_types": types, "at_clauses": at_clauses}


def prepare_projects(projects):
    projects = projects.copy()
    projects["state"] = projects["state"].apply(normalize_state)
    projects["action_type"] = projects["project_name"].apply(get_action_type)
    projects["detected_actions"] = projects["project_name"].apply(lambda name: ";".join(extract_actions(name)))
    # SCRTP repeats project_id 6809 M on pages 19 and 48. Keep these distinct.
    projects["project_key"] = projects["state"] + ":" + projects["source_file"] + ":" + projects["source_page"] + ":" + projects["project_id"]
    if projects["project_key"].duplicated().any():
        raise ValueError("Project keys are not unique; check source_file and source_page")
    return projects


def read_asset_requirements(projects):
    """Use checked descriptions for this source; changed/new descriptions need review.

    The plain-text checklist is data, not a hardcoded state in the search code.
    It avoids pretending that a simple regex can resolve every scope sentence.
    """
    scope_rows = []
    for line in SCOPE_PATH.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            fields = line.split("|")
            if len(fields) != 10:
                raise ValueError("Expected ten fields in scope row: " + line)
            scope_rows.append(fields)
    scope = pd.DataFrame(scope_rows, columns=["source_page", "asset_type", "asset_name", "endpoint_a", "endpoint_b",
                                             "voltage_kv", "circuit", "asset_identifier", "asset_stage", "scope_notes"])
    snapshots = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    requirements = []
    for project in projects.to_dict("records"):
        known = next((p for p in snapshots if all(p[k] == project[k] for k in
                     ["state", "source_file", "source_page", "project_id", "project_name", "description"])), None)
        clues = extract_identifiers(project)
        if known:
            assets = scope[scope["source_page"] == project["source_page"]].to_dict("records")
        else:
            assets = [{"asset_type": "scope_review_needed", "asset_name": project["project_name"],
                       "endpoint_a": "", "endpoint_b": "", "voltage_kv": "", "circuit": "",
                       "asset_identifier": ";".join(clues["line_identifiers"]), "asset_stage": "uncertain",
                       "scope_notes": "New or changed source description: list every required asset before counting complete coverage."}]
        if not assets:
            raise ValueError("No scope rows for source page " + project["source_page"])
        for number, asset in enumerate(assets, 1):
            row = dict(project)
            row.update(asset)
            row["asset_key"] = project["project_key"] + ":" + str(number)
            row["extracted_identifiers"] = json.dumps(clues)
            requirements.append(row)
    return requirements


def identity_names(asset):
    if asset["asset_type"] == "line":
        return [n for n in [asset["endpoint_a"], asset["endpoint_b"]] if n]
    return [asset["asset_name"]]


def normalize_asset_name(name):
    # Keep numbered facilities such as Orangeburg #1 and queue IDs such as
    # DESCSQ #1151 distinct. Voltage/circuit checking is separate from names.
    name = re.sub(r"#\s*(\d+)", r" number\1 ", name)
    return normalize_endpoint(name)


def match_basis(asset, feature):
    """Simple exact/token checks. A matching name is a candidate, not proof."""
    if asset["asset_type"] != feature["asset_type"] or asset["state"] != feature["state"]:
        return ""
    names = feature["names"]
    if asset["asset_identifier"] and any(asset["asset_identifier"].lower() in n.lower() for n in names):
        return "identifier"
    if asset["asset_type"] == "station":
        if any(name_matches(asset["asset_name"], name) for name in names):
            return "exact_station_name"
        if any(name_matches(asset["asset_name"], name, partial=True) for name in names):
            return "partial_station_name"
        return ""
    endpoints = identity_names(asset)
    matched = [endpoint for endpoint in endpoints if any(
        " " + normalize_asset_name(endpoint) + " " in " " + normalize_asset_name(name) + " " for name in names)]
    if len(endpoints) == 2 and len(matched) == 2:
        return "both_endpoint_names"
    if matched:
        return "one_endpoint_only"
    return ""


def assess_candidate(asset, feature, basis, exact_station_count):
    """Use explicit checks, not a confidence score. Preserve rejection reasons."""
    wanted = [float(v) for v in asset["voltage_kv"].split(";") if v]
    voltage = feature["voltage_kv"]
    if not feature["geometry"]:
        return "candidate", "Source object has no usable geometry."
    if wanted and voltage and not set(wanted).intersection(voltage):
        return "candidate", "Voltage conflicts; no asset match confirmed."
    if asset["asset_stage"] != "existing":
        return "candidate", "Existing object does not establish the proposed/undecided asset's route or site."
    if feature["tags"].get("power") == "plant":
        return "candidate", "Plant footprint is not the required substation."
    if asset["asset_type"] == "station" and basis == "exact_station_name" and exact_station_count == 1:
        if wanted and not voltage:
            return "candidate", "Exact name but no source voltage to verify facility identity."
        return "located", "Unique exact station name, matching asset type and state; source voltage agrees when specified."
    if asset["asset_type"] == "line":
        if basis == "one_endpoint_only":
            return "candidate", "Only one endpoint agrees; actual line identity and work segment remain unresolved."
        return "candidate", "Named source line found; verify circuit, full referenced segment, and scope before marking located."
    return "candidate", "Partial or ambiguous station name requires review."


def search_named_osm_objects(asset):
    """One cached, rate-limited Nominatim query per missing asset name/endpoint pair.

    Only retain typed power objects inside the requested state. No town centers.
    """
    names = identity_names(asset)
    if not names or asset["asset_type"] not in ["line", "station"]:
        return []
    search_name = " - ".join(names)
    kind = "transmission line" if asset["asset_type"] == "line" else "substation"
    query = f"{search_name} {kind}, {asset['state']}, United States"
    stem = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
    folder = RAW_DATA_DIR / "osm" / asset["state"] / "nominatim_assets"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (stem + ".json")
    if path.exists():
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved["query"] != query:
            raise ValueError("Nominatim cache name collision")
        results = saved["results"]
    else:
        time.sleep(1.1)
        response = requests.get("https://nominatim.openstreetmap.org/search", params={
            "q": query, "format": "jsonv2", "countrycodes": "us", "addressdetails": 1,
            "namedetails": 1, "extratags": 1, "polygon_geojson": 1, "limit": 10,
        }, headers={"User-Agent": "GridLock-learning-project/0.1 (one-time asset research)"}, timeout=60)
        response.raise_for_status()
        results = response.json()
        if not isinstance(results, list):
            raise ValueError("Invalid Nominatim response")
        with path.open("x", encoding="utf-8") as file:
            json.dump({"query": query, "results": results}, file, indent=2)
    features = []
    for result in results:
        if result.get("address", {}).get("ISO3166-2-lvl4") != "US-" + asset["state"]:
            continue
        tags = result.get("extratags", {}).copy()
        power = tags.get("power", result.get("type", "") if result.get("category", result.get("class")) == "power" else "")
        if power not in ["line", "substation", "switchyard", "plant"]:
            continue
        geometry = result.get("geojson")
        if power == "line" and (not geometry or geometry.get("type") not in ["LineString", "MultiLineString"]):
            continue
        tags["power"] = power
        name = result.get("name", "")
        features.append({"state": asset["state"], "asset_type": "line" if power == "line" else "station",
                         "feature_id": f"{result['osm_type']}/{result['osm_id']}", "feature_name": name,
                         "names": [name] + list(result.get("namedetails", {}).values()), "geometry": geometry,
                         "voltage_kv": [float(v) / 1000 for v in re.findall(r"\d+(?:\.\d+)?", tags.get("voltage", ""))],
                         "tags": tags, "source": "nominatim", "source_file": str(path),
                         "source_url": f"https://www.openstreetmap.org/{result['osm_type']}/{result['osm_id']}"})
    return features


def find_project_matches(requirements, features):
    rows = []
    for asset in requirements:
        candidates = [(feature, match_basis(asset, feature)) for feature in features]
        candidates = [(feature, basis) for feature, basis in candidates if basis]
        # Keep all matching objects/segments. Do not pick one winner or merge geometry.
        exact_count = len({f["feature_id"] for f, basis in candidates if basis == "exact_station_name"
                          and f["tags"].get("power") in ["substation", "switchyard"]})
        seen = set()
        for feature, basis in candidates:
            if feature["feature_id"] in seen:
                continue
            seen.add(feature["feature_id"])
            status, reason = assess_candidate(asset, feature, basis, exact_count)
            row = {key: asset[key] for key in ["project_key", "project_id", "project_name", "state", "source_page",
                   "action_type", "detected_actions", "asset_key", "asset_type", "asset_name", "asset_identifier",
                   "voltage_kv", "circuit", "endpoint_a", "endpoint_b", "asset_stage", "scope_notes", "description", "extracted_identifiers"]}
            row.update({"matched_feature_name": feature["feature_name"], "feature_id": feature["feature_id"],
                        "geometry": json.dumps(feature["geometry"]) if feature["geometry"] else "",
                        "source": feature["source"], "source_url": feature["source_url"], "source_file": feature["source_file"],
                        "source_tags": json.dumps(feature["tags"]), "match_basis": basis, "asset_status": status, "review_reason": reason})
            rows.append(row)
        if not candidates:
            row = {key: asset[key] for key in ["project_key", "project_id", "project_name", "state", "source_page",
                   "action_type", "detected_actions", "asset_key", "asset_type", "asset_name", "asset_identifier",
                   "voltage_kv", "circuit", "endpoint_a", "endpoint_b", "asset_stage", "scope_notes", "description", "extracted_identifiers"]}
            row.update({"matched_feature_name": "", "feature_id": "", "geometry": "", "source": "", "source_url": "",
                        "source_file": "", "source_tags": "", "match_basis": "", "asset_status": "unresolved",
                        "review_reason": "No matching geographic source object found."})
            rows.append(row)
    return pd.DataFrame(rows)


def add_official_map_evidence(review, maps):
    """Attach related official pages/maps without counting them as GIS geometry."""
    links = json.loads((DATA_DIR / "reference" / "dominion_official_maps.json").read_text(encoding="utf-8"))
    snapshots = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    evidence = []
    for row in review.to_dict("records"):
        known = any(all(p[k] == row[k] for k in ["state", "source_page", "project_id", "project_name", "description"])
                    for p in snapshots)
        slugs = links.get(row["source_page"], []) if known else []
        selected = [m for m in maps if m["url"].rsplit("/", 1)[-1].lower() in slugs]
        evidence.append(json.dumps(selected) if selected else "")
    review = review.copy()
    review["official_map_evidence"] = evidence
    return review


def apply_verified_assets(review, verified_assets):
    """Record checked source objects after reviewing their actual identity/extent.

    Keys are (asset_key, feature_id); values explain the supporting evidence.
    This can confirm several source segments for the same required asset. The
    reviewer must verify their combined extent covers that asset's stated scope.
    It cannot create geometry or confirm an unresolved row without a source object.
    """
    review = review.copy()
    for (asset_key, feature_id), evidence in verified_assets.items():
        mask = (review["asset_key"] == asset_key) & (review["feature_id"] == feature_id)
        if mask.sum() != 1 or not evidence.strip():
            raise ValueError("Verification needs one source-object row and supporting evidence.")
        row = review.loc[mask].iloc[0]
        if not row["geometry"] or "Voltage conflicts" in row["review_reason"]:
            raise ValueError("Resolve missing geometry or voltage conflict before verification.")
        if row["asset_stage"] == "uncertain":
            raise ValueError("Resolve uncertain project scope before verifying the asset.")
        geometry = json.loads(row["geometry"])
        if row["asset_type"] == "line" and geometry["type"] not in ["LineString", "MultiLineString", "GeometryCollection"]:
            raise ValueError("An endpoint point cannot confirm an existing line.")
        review.loc[mask, "asset_status"] = "located"
        review.loc[mask, "review_reason"] = "Reviewed full asset scope: " + evidence
    return review


def summarize_coverage(projects, review):
    """Count requirements, never candidate rows or distinct project_id alone."""
    summaries = []
    for project in projects.to_dict("records"):
        rows = review[review["project_key"] == project["project_key"]]
        required = rows["asset_key"].nunique()
        located = rows.loc[rows["asset_status"] == "located", "asset_key"].nunique()
        status = "all" if required and located == required else "some" if located else "none"
        summaries.append({"project_key": project["project_key"], "action_type": project["action_type"],
                          "required_assets": required, "located_assets": located, "coverage": status})
    return pd.DataFrame(summaries)


def print_coverage(summary):
    # X means ALL referenced assets located, not just one endpoint or one station.
    for action in ["replace", "rebuild", "upgrade", "construct", "other"]:
        subset = summary[summary["action_type"] == action]
        found = int((subset["coverage"] == "all").sum())
        label = "real assets located" if action in ["replace", "rebuild", "upgrade"] else "locations/routes located" if action == "construct" else "located"
        print(f"{action.title()} projects: {found} / {len(subset)} {label}")
    for status in ["all", "some", "none"]:
        label = "no" if status == "none" else status
        print(f"Projects with {label} referenced assets located: {(summary['coverage'] == status).sum()}")


def save_results(review):
    REVIEW_PATH.parent.mkdir(parents=True, exist_ok=True)
    review.to_csv(REVIEW_PATH, index=False)
    return REVIEW_PATH
