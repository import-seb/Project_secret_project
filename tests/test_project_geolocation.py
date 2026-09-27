"""Offline checks for the simplified project-level workflow."""

import json
import pandas as pd
from gridlock.geolocation import (
    get_action_type, normalize_name, geolocate_projects, apply_known_routes,
    load_known_routes, find_overlap_candidates,
)
from gridlock.paths import PROCESSED_DATA_DIR


def project(name, page="1", state="SC"):
    return {"project_id": "001", "project_name": name, "description": "", "state": state,
            "source_file": "test.pdf", "source_page": page, "in_service_date": "2028-01-01"}


def facility(name, point, state="SC"):
    return {"name": name, "names": [name], "state": state, "kind": "substation", "source": "osm",
            "feature_id": name, "source_url": "https://example.org/source", "source_file": "fixture",
            "geometry": {"type": "Point", "coordinates": point}, "review_required": False}


assert get_action_type("Construct and Rebuild") == "rebuild"
assert normalize_name("Jefferson Street #3") != normalize_name("Jefferson Street #2")
features = [facility("Alpha Substation", [-81, 33]), facility("Beta Substation", [-80, 34])]
inputs = pd.DataFrame([project("Alpha - Beta 115kV Rebuild"), project("Unknown 115kV Rebuild", "2")])
located = geolocate_projects(inputs, "TEST", features)
assert len(located) == 2 and located.project_key.is_unique
assert located.iloc[0].location_method == "endpoint_midpoint"
assert located.iloc[0].geometry == {"type": "Point", "coordinates": [-80.5, 33.5]}
assert located.iloc[1].location_method == "unresolved" and located.iloc[1].geometry is None
assert located.in_service_date.tolist() == inputs.in_service_date.tolist()

single = geolocate_projects(inputs.iloc[[0]], "TEST", features[:1])
assert single.iloc[0].location_method == "single_endpoint"
other_state = geolocate_projects(inputs.iloc[[0]], "TEST", [facility("Alpha Substation", [-81, 33], "GA")])
assert other_state.iloc[0].location_method == "unresolved"

ambiguous = geolocate_projects(inputs.iloc[[0]], "TEST", features + [facility("Alpha Substation", [-84, 34])])
assert ambiguous.iloc[0].location_method == "single_endpoint"
assert ambiguous.iloc[0].review_required

internal = geolocate_projects(pd.DataFrame([project("Replace Structure 67 at Alpha")]), "TEST", features)
assert internal.iloc[0].resolution_requirement == "requires_primary_source"
assert internal.iloc[0].review_required
internal = geolocate_projects(pd.DataFrame([project("Frogmore Transmission 115 kV Rebuild")]), "TEST",
                             [facility("Frogmore Substation", [-81, 33])])
assert internal.iloc[0].location_method == "unresolved"

route1 = {"type": "LineString", "coordinates": [[-81, 33], [-80.8, 33.5], [-80, 34]]}
route2 = {"type": "LineString", "coordinates": [[-81, 33], [-81.1, 33.5], [-80, 34]]}
routes = pd.DataFrame([
    {"project_key": located.iloc[0].project_key, "geometry": json.dumps(g), "route_status": status,
     "route_file": "fixture.csv", "route_method": "fixture_public_line", "endpoint_a": "Alpha", "endpoint_b": "Beta"}
    for g, status in [(route1, "resolved"), (route2, "review_required")]
])
with_routes = apply_known_routes(located, routes)
assert len(with_routes) == len(located)
assert with_routes.iloc[0].geometry == {"type": "GeometryCollection", "geometries": [route1, route2]}
assert with_routes.iloc[0].point_location_method == "route_bounds_center"
assert with_routes.iloc[0].review_required
assert "review_required" in with_routes.iloc[0].route_evidence

# Preserve all historical traced geometry; 12 resolved assets plus one review route.
known = load_known_routes("DESC")
assert (known.route_status == "resolved").sum() == 12
assert (known.route_status == "review_required").sum() == 1
desc = pd.read_csv(PROCESSED_DATA_DIR / "dominion/dominion_projects.csv", dtype=str, keep_default_na=False)
desc_geo = apply_known_routes(geolocate_projects(desc, "DESC", []), known)
assert len(desc_geo) == 54 and desc_geo.project_key.nunique() == 54
for row in known[known.route_status.isin(["resolved", "review_required"])].to_dict("records"):
    geometry = desc_geo.loc[desc_geo.project_key == row["project_key"], "geometry"].iloc[0]
    source_geometry = json.loads(row["geometry"])
    assert geometry == source_geometry or source_geometry in geometry.get("geometries", [])

overlaps = find_overlap_candidates(located.iloc[[0]], located.iloc[[0]], max_distance_km=0)
assert len(overlaps) == 1 and overlaps.iloc[0].distance_km == 0
assert overlaps.iloc[0].review_required
assert find_overlap_candidates(located.iloc[[1]], located, max_distance_km=100).empty
print("Project geolocation checks passed: point methods, ambiguity, internal IDs, state isolation, saved routes and proximity.")
