"""Small offline checks for endpoint extraction and coverage counting."""

from gridlock.location_discovery import (
    extract_endpoint_names, normalize_endpoint, name_matches, match_endpoint, discover_locations,
)

examples = [
    ("Summerville-Boone Hill 115kV", ["summerville", "boone hill"]),
    ("Urquhart – Toolebeck 115kV", ["urquhart", "toolebeck"]),
    ("Jasper – Okatie 230kV #2", ["jasper", "okatie"]),
    ("Canadys-Ritter 115kV", ["canadys", "ritter"]),
]
for title, expected in examples:
    assert extract_endpoint_names({"project_name": title, "description": ""}) == expected
names = extract_endpoint_names({
    "project_name": "Scout 230 kV Sub and Fold-in: Construct",
    "description": "Construct 230kV Transmission Line for Scout Customer Substation and modify VCS1 and Killian terminals.",
})
assert names == ["scout", "vcs1", "killian"]
assert extract_endpoint_names({"project_name": "Red House Road 115 kV", "description": ""}) == ["red house road"]
assert normalize_endpoint("Yemassee Substation") == "yemassee"
assert normalize_endpoint("N. Bridge Terrace Sub") == "north bridge terrace"
assert name_matches("boone hill", "Boone Hill Substation")
assert not name_matches("north", "Northwest Substation", partial=True)


def feature(name, feature_id, state="SC"):
    return {"aliases": [name], "matched_feature_name": name, "osm_feature_id": feature_id,
            "osm_feature_type": "way", "geometry": {"type": "Point", "coordinates": [-81, 33]},
            "source": "overpass", "state": state}


exact = feature("Boone Hill Substation", "way/1")
partial = feature("Boone Hill Industrial Substation", "way/2")
assert match_endpoint("boone hill", [partial, exact]) == [exact]
line = feature("Canadys-Ritter 115kV", "way/3")
assert match_endpoint("canadys", [line]) == [line]
assert match_endpoint("ritter", [line]) == [line]

projects = [
    {"project_id": "same", "project_name": "Canadys-Ritter 115kV", "state": "SC", "description": ""},
    {"project_id": "same", "project_name": "Missing 115kV", "state": "SC", "description": ""},
]
review, summary = discover_locations(projects, {"SC": [line]}, use_nominatim=False)
assert review.columns.tolist() == ["project_id", "project_name", "endpoint_name", "matched_feature_name",
                                    "osm_feature_id", "osm_feature_type", "geometry", "source"]
assert summary["Total projects"] == 2 and summary["Total endpoint names extracted"] == 3
assert summary["Endpoints matched"] == 2
assert summary["Projects with two matched endpoints"] == 1
assert summary["Projects with no matched endpoints"] == 1
assert len(review[review["source"] == ""]) == 1
assert review[review.endpoint_name == "missing"].iloc[0].geometry == ""
print("Passed: endpoint extraction, exact-first matching, line names, missing rows, duplicate project IDs, and coverage counts.")
