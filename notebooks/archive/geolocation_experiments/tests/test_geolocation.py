"""Run with .venv/Scripts/python.exe tests/test_geolocation.py (no network)."""

from gridlock.geolocation import (
    build_project_geometry, build_overpass_query, find_project_matches,
    normalize_name, normalize_state, osm_geometry,
)


def feature(feature_id, name, voltage="230000", power="substation", state="SC"):
    return {
        "geometry": {"type": "Point", "coordinates": [-81.0, 33.0]},
        "properties": {
            "state": state,
            "osm_feature_id": feature_id, "osm_feature_name": name,
            "osm_feature_type": "node", "osm_url": "https://www.openstreetmap.org/" + feature_id,
            "power": power, "operator": "Dominion Energy South Carolina",
            "voltage": voltage, "tags": {"name": name},
        },
    }


project = {
    "state": "SC", "utility": "Dominion Energy South Carolina",
    "project_key": "scrtp-page-test", "project_id": "001",
    "project_name": "Okatie - Sherwood 230 kV: Rebuild",
    "description": "Rebuild the line from Okatie to Sherwood.",
}
okatie = feature("node/1", "Okatie Substation")
sherwood = feature("node/2", "Sherwood Substation")
sherwood["geometry"]["coordinates"] = [-80.8, 33.2]

assert normalize_name("St. George 115kV Substation") == "saint george"
matches = find_project_matches(project, [okatie, sherwood])
assert len(matches) == 2
assert all(m["match_confidence"] == "high" for m in matches)
assert build_project_geometry(matches)[1] == "unresolved", "High scores must not silently confirm geometry"

matches[0]["confirmed"] = True
geometry, method, note = build_project_geometry(matches)
assert method == "single_confirmed_point" and geometry["coordinates"] == [-81.0, 33.0]
matches[1]["confirmed"] = True
geometry, method, note = build_project_geometry(matches)
assert method == "endpoint_straight_line"
assert geometry["coordinates"] == [[-81.0, 33.0], [-80.8, 33.2]]

conflict = find_project_matches(project, [feature("node/3", "Okatie", "115000")])
assert conflict[0]["match_confidence"] == "low"
assert "VOLTAGE CONFLICT" in conflict[0]["match_evidence"]
unknown_voltage = find_project_matches(project, [feature("node/4", "Okatie", "")])
assert unknown_voltage[0]["match_confidence"] == "medium"
ambiguous = find_project_matches(project, [okatie, feature("node/5", "Okatie")])
assert len(ambiguous) == 2 and all(m["match_confidence"] == "low" for m in ambiguous)
assert find_project_matches(project, [feature("node/6", "Unrelated Station")]) == []

ring = [[-81.0, 33.0], [-81.1, 33.0], [-81.1, 33.1], [-81.0, 33.0]]
element = {"type": "way", "geometry": [{"lon": p[0], "lat": p[1]} for p in ring]}
polygon = osm_geometry(element, "substation")
assert polygon == {"type": "Polygon", "coordinates": [ring]}
assert osm_geometry(element, "line")["type"] == "LineString"
assert osm_geometry({"type": "way", "geometry": [{"lon": 1}]}, "line") is None
matches[0]["geometry"] = polygon
matches[1]["confirmed"] = False
geometry, method, note = build_project_geometry(matches)
assert geometry["coordinates"] == ring[0] and "boundary_vertex" in note

line_match = dict(matches[0])
line_match["power"] = "line"
line_match["geometry"] = {"type": "LineString", "coordinates": ring[:3]}
assert build_project_geometry([line_match])[0] == line_match["geometry"]
assert build_project_geometry([line_match])[1] == "confirmed_osm_line"
assert build_project_geometry([])[:2] == (None, "unresolved")

# Same source project ID must not collapse independent source-page keys.
other_project = dict(project)
other_project["project_key"] = "scrtp-page-other"
other_matches = find_project_matches(other_project, [okatie])
assert other_matches[0]["project_key"] != matches[0]["project_key"]
assert other_matches[0]["project_id"] == matches[0]["project_id"]
print("Passed: matching evidence, ambiguity, voltage conflicts, confirmation, geometry, and duplicate-ID checks.")

# Identical names in other states must never cross-match.
mixed_features = []
for state in ["SC", "GA", "FL", "NC"]:
    mixed_features.append(feature("node/" + state, "Okatie Substation", state=state))
    query = build_overpass_query(state, "substation")
    assert '"US-' + state + '"' in query
    assert '["power"="substation"]' in query
    assert '["operator"' not in query, "Statewide fetch must not be restricted to Dominion"
for state in ["SC", "GA", "FL", "NC"]:
    state_project = dict(project, state=state, project_key=state + ":001")
    state_matches = find_project_matches(state_project, mixed_features)
    assert len(state_matches) == 1
    assert state_matches[0]["state"] == state
    assert state_matches[0]["osm_feature_id"] == "node/" + state
assert normalize_state(" ga ") == "GA"
for bad_state in ["", None, "South Carolina", "S", 'SC";out;']:
    try:
        normalize_state(bad_state)
    except ValueError:
        pass
    else:
        raise AssertionError("Invalid state accepted: " + repr(bad_state))
print("Passed: dynamic state queries, missing-state rejection, and SC/GA/FL/NC match isolation.")
