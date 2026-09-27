"""Description-based asset requirements and public route candidates for any utility."""

import json
import re
import pandas as pd
from gridlock.paths import DATA_DIR, RAW_DATA_DIR
from gridlock.geolocation import (
    normalize_name, geometry_center, distance_km, load_endpoint_evidence,
    load_known_routes, flag_primary_source_requirements,
)
from gridlock.asset_sources import load_infrastructure
from gridlock.public_lines import fetch_public_lines, trace_public_path
from gridlock.osm_power_network import (
    download_osm_power_network, load_osm_power_network,
    build_voltage_graph, route_between_points,
)

SCOPE_PATH = DATA_DIR / "reference/geo/project_asset_scope.csv"


def read_asset_scope(projects, scope=None):
    """Use checked descriptions, as DESC did; changed/new text needs scope review."""
    if scope is None:
        scope = pd.read_csv(SCOPE_PATH, dtype=str, keep_default_na=False)
    if scope.asset_key.duplicated().any():
        raise ValueError("Asset keys must be unique; separate segments/circuits need separate keys")
    rows = []
    for project in projects.fillna("").to_dict("records"):
        saved = scope[scope.project_key.eq(project["project_key"])]
        fields = ["project_name", "description", "state", "utility"]
        valid = not saved.empty and all(saved[field].eq(project[field]).all() for field in fields)
        if valid:
            source_fields = ["source_file", "source_page", "source_pdf_page", "source_url",
                             "description_source_file", "description_pdf_page", "action_type", "detected_actions"]
            for asset in saved.to_dict("records"):
                asset.update({field: project.get(field, "") for field in source_fields})
                rows.append(asset)
        else:
            rows.append(dict(project, asset_key=project["project_key"] + ":scope_review",
                             asset_type="scope_review_needed", asset_name=project["project_name"],
                             endpoint_a="", endpoint_b="", voltage_kv="", circuit="",
                             asset_stage="uncertain", scope_role="work", scope_source="unchecked",
                             scope_notes="New or changed description: check its asset list before tracing."))
    return pd.DataFrame(rows).fillna("")


def find_asset_facility(name, state, features):
    """Exact facility identity only. A tap, junction or station number must agree."""
    if not name or flag_primary_source_requirements({"project_name": name}):
        return None
    key = normalize_name(name)
    candidates = [f for f in features if f["state"] == state
                  and any(normalize_name(n) == key for n in f["names"])]
    candidates = [f for f in candidates if geometry_center(f["geometry"]) is not None]
    if not candidates:
        return None
    centers = [geometry_center(f["geometry"]) for f in candidates]
    if any(distance_km(centers[0], p) > 2 for p in centers[1:]):
        return None  # Same name at different places is not a resolved anchor.
    candidates.sort(key=lambda f: (f["source"] != "osm", f["kind"] != "substation"))
    feature = candidates[0]
    return dict(feature, coordinates=geometry_center(feature["geometry"]))


def named_public_lines(asset, lines):
    """Notebook 07's direct endpoint-pair lookup, with voltage checked explicitly."""
    a, b = normalize_name(asset["endpoint_a"]), normalize_name(asset["endpoint_b"])
    if not a or not b or not str(asset["voltage_kv"]).replace(".", "", 1).isdigit():
        return []
    matches = []
    for line in lines:
        tags = line["tags"]
        x, y = normalize_name(tags.get("sub_1", "")), normalize_name(tags.get("sub_2", ""))
        if (a, b) not in [(x, y), (y, x)] or float(asset["voltage_kv"]) not in line["voltage_kv"]:
            continue
        if line["geometry"]:
            matches.append(line)
    return matches


def check_route_extent(row):
    """Flag large differences from stated work mileage; this is not a match score."""
    expected = row.get("expected_length_miles", "")
    if not expected:
        return row
    geometry = json.loads(row["geometry"])
    parts = [geometry["coordinates"]] if geometry["type"] == "LineString" else geometry["coordinates"]
    actual_km = sum(distance_km(a, b) for part in parts for a, b in zip(part, part[1:]))
    expected_km = float(expected) * 1.609344
    row["distance_km"] = actual_km
    # Broad sanity bounds: smaller differences still need circuit/scope review.
    if actual_km < expected_km * 0.5 or actual_km > expected_km * 1.5:
        row["route_status"] = "route_extent_mismatch"
        row["review_note"] += f"; source path {actual_km:.2f} km versus stated work {expected_km:.2f} km; not used as project route."
    return row


def trace_project_routes(projects, download_missing=True, infrastructure=None, scope=None):
    """Search each description asset separately; never route every pair in a title.

    Use retained routes, then the OSM voltage network, then named public GIS lines.
    Failed and proposed assets remain visible beside successful candidates.
    """
    assets = read_asset_scope(projects, scope)
    if infrastructure is None:
        infrastructure = load_infrastructure(projects.state.unique(), download_missing=download_missing)
    features = list(infrastructure)
    saved_frames = []
    for utility in projects.utility.unique():
        features.extend(load_endpoint_evidence(utility))
        known = load_known_routes(utility)
        if not known.empty:
            saved_frames.append(known)
    saved = pd.concat(saved_frames, ignore_index=True).fillna("") if saved_frames else pd.DataFrame()
    rows = []
    for state, group in assets.groupby("state"):
        if not re.fullmatch(r"[A-Z]{2}", state):
            raise ValueError(f"Expected a two-letter state code: {state}")
        cache = RAW_DATA_DIR / "osm" / state / "power_lines_statewide_overpass.json"
        ways = nodes = public_lines = None
        graphs = {}
        for asset in group.to_dict("records"):
            row = dict(asset, geometry="", route_status="scope_review_needed", route_method="",
                       route_file="", review_note=asset["scope_notes"], anchor_evidence="[]")
            # Reuse discoveries per asset, not by skipping the entire project.
            if not saved.empty:
                previous = saved[saved.asset_key.eq(asset["asset_key"])
                                 & saved.route_status.isin(["resolved", "review_required"])
                                 & saved.geometry.ne("")]
                if not previous.empty:
                    for evidence in previous.to_dict("records"):
                        kept = dict(row, geometry=evidence["geometry"], route_status="saved_route_preserved",
                                    route_method=evidence["route_method"], route_file=evidence["route_file"],
                                    saved_evidence=json.dumps(evidence))
                        rows.append(kept)
                    continue
            if asset["asset_type"] == "scope_review_needed":
                rows.append(row)
                continue
            if asset["asset_stage"] != "existing":
                row["route_status"] = "proposed_asset_needs_source" if asset["asset_stage"] == "proposed" else "scope_review_needed"
                rows.append(row)
                continue
            if asset["scope_role"] == "context":
                rows.append(dict(row, route_status="parent_corridor_context_only"))
                continue
            if asset["asset_type"] == "station":
                facility = find_asset_facility(asset["asset_name"], state, features)
                if facility:
                    row.update(geometry=json.dumps(facility["geometry"]), route_status="facility_candidate",
                               route_method="exact_facility_name", route_file=facility["source_file"],
                               anchor_evidence=json.dumps([facility]), source=facility["source"])
                else:
                    row["route_status"] = "facility_not_found_or_ambiguous"
                rows.append(row)
                continue
            a = find_asset_facility(asset["endpoint_a"], state, features)
            b = find_asset_facility(asset["endpoint_b"], state, features)
            row["anchor_evidence"] = json.dumps([a, b])
            if asset.get("resolution_requirement") == "requires_primary_source" or any(
                    re.search(r"\b(?:str(?:ucture)?\s*\d+|DESCSQ|GOAB|loop.in point|APC border)\b", n, re.I)
                    for n in [asset["endpoint_a"], asset["endpoint_b"]]):
                rows.append(dict(row, route_status="requires_primary_source"))
                continue
            if not str(asset["voltage_kv"]).replace(".", "", 1).isdigit():
                rows.append(dict(row, route_status="voltage_needs_review"))
                continue
            if not asset["endpoint_a"] or not asset["endpoint_b"]:
                rows.append(dict(row, route_status="segment_limits_missing"))
                continue
            route = None
            if a and b:
                if ways is None and (cache.exists() or download_missing):
                    download_osm_power_network(cache, state_iso=f"US-{state}")
                    ways, nodes = load_osm_power_network(cache)
                if ways is not None:
                    voltage = float(asset["voltage_kv"])
                    if voltage not in graphs:
                        graphs[voltage] = build_voltage_graph(ways, nodes, voltage)
                    route = route_between_points(graphs[voltage], *a["coordinates"], *b["coordinates"],
                                                 snap_max_km=2, max_route_km=400)
            if route and route["hops"] > 0:
                row.update(route)
                row.update(geometry=json.dumps(route["geometry"]), route_status="review_required",
                           route_file=str(cache))
                row["review_note"] += "; OSM path candidate; circuit and exact work extent unverified."
                rows.append(check_route_extent(row))
                continue
            # Named line records can supply geography even when facility points are absent.
            if public_lines is None:
                if download_missing or (RAW_DATA_DIR / "public_gis" / state / "netl_lines_0.json").exists():
                    public_lines = fetch_public_lines(state)
                else:
                    public_lines = []
            matches = named_public_lines(asset, public_lines)
            for line in matches:
                candidate = dict(row, geometry=json.dumps(line["geometry"]), route_status="review_required",
                                 route_method="named_public_gis_line", route_file=line["source_file"],
                                 source=line["source"], source_url=line["source_url"], feature_id=line["feature_id"],
                                 source_tags=json.dumps(line["tags"]))
                candidate["review_note"] += "; Named GIS line candidate; circuit and exact work extent unverified."
                rows.append(check_route_extent(candidate))
            if not matches:
                public_route = trace_public_path(asset, a, b, public_lines) if a and b else None
                if public_route:
                    row.update(public_route)
                    row["geometry"] = json.dumps(public_route["geometry"])
                    row["route_status"] = "review_required"
                    row["review_note"] += "; GIS endpoint graph candidate; rounded endpoint connectivity, circuit and source-feature extent need review."
                    rows.append(check_route_extent(row))
                    continue
                status = "no_connected_voltage_path" if a and b else "endpoint_anchor_missing"
                if a and b and ways is None:
                    status = "network_cache_missing"
                rows.append(dict(row, route_status=status))
    return pd.DataFrame(rows).fillna("")


def apply_traced_routes(projects, routes):
    """Keep all discovered assets; append new geometry without losing saved routes."""
    result = projects.copy()
    if routes.empty:
        return result
    for index, project in result.iterrows():
        review = routes[routes.project_key.eq(project["project_key"])]
        candidates = review[review.route_status.isin(["review_required", "facility_candidate"])
                            & review.geometry.ne("") & review.scope_role.eq("work")]
        line_candidates = candidates[candidates.asset_type.eq("line")]
        has_saved = project["location_method"] == "saved_traced_route"
        if line_candidates.empty and has_saved:
            continue
        if candidates.empty:
            continue
        # Station-only scope uses actual work facilities, not title corridor endpoints.
        geometries = []
        evidence = []
        if has_saved:
            old = project["geometry"]
            geometries.extend(old["geometries"] if old["type"] == "GeometryCollection" else [old])
            evidence.extend(json.loads(project["route_evidence"]))
        for row in candidates.to_dict("records"):
            geometry = json.loads(row.pop("geometry"))
            if geometry not in geometries:
                geometries.append(geometry)
            evidence.append(row)  # Separate asset/circuit records survive shared geometry.
        geometry = geometries[0] if len(geometries) == 1 else {"type": "GeometryCollection", "geometries": geometries}
        result.at[index, "geometry"] = geometry
        result.at[index, "geometry_type"] = geometry["type"]
        result.at[index, "longitude"], result.at[index, "latitude"] = geometry_center(geometry)
        result.at[index, "location_method"] = "saved_traced_route" if has_saved else (
            "traced_route_candidate" if not line_candidates.empty else "description_facilities")
        result.at[index, "point_location_method"] = "asset_bounds_center"
        result.at[index, "route_evidence"] = json.dumps(evidence)
        result.at[index, "review_required"] = True
        result.at[index, "confidence"] = "medium"
        result.at[index, "source"] = ";".join(dict.fromkeys(
            ([project["source"]] if has_saved else []) + list(candidates.route_method)))
        result.at[index, "location_source_file"] = ";".join(dict.fromkeys(
            ([project["location_source_file"]] if has_saved else []) + list(candidates.route_file)))
        result.at[index, "location_source_url"] = ";".join(dict.fromkeys(
            url for row in candidates.to_dict("records") for url in [row.get("source_url", "")] if url))
        result.at[index, "review_note"] = (project["review_note"] + "; Description asset candidates; review unresolved rows, circuit identity and exact work limits.").strip("; ")
        if len(line_candidates) == 1 and not has_saved:
            result.at[index, "endpoint_a"] = line_candidates.iloc[0].endpoint_a
            result.at[index, "endpoint_b"] = line_candidates.iloc[0].endpoint_b
    return result
