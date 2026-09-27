"""Offline checks for scope preservation and honest coverage counts."""

import json

import pandas as pd

from gridlock.asset_discovery import (
    get_action_type, extract_actions, extract_identifiers, prepare_projects,
    read_asset_requirements, find_project_matches, summarize_coverage, apply_verified_assets,
)
from gridlock.paths import PROCESSED_DATA_DIR


assert get_action_type("Construct and Rebuild") == "rebuild"
assert get_action_type("UPGRADE and construct") == "upgrade"
assert get_action_type("Construct, replace, rebuild") == "replace"
assert get_action_type("Replacement station") == "other"
assert extract_actions("Construct and Rebuild") == ["construct", "rebuild"]

clues = extract_identifiers({"project_name": "Upgrade Fairfax-Yemassee 115kV", "description":
                            "ACSR portion of DESCSQ #1151 - Yemassee 115 kV line to 1272 ACSR."})
assert clues["line_identifiers"] == ["DESCSQ #1151"]
assert clues["voltage_clues_kv"] == ["115"]
assert clues["description_asset_types"] == ["line"]

projects = prepare_projects(pd.read_csv(PROCESSED_DATA_DIR / "dominion/dominion_projects.csv",
                                       dtype=str, keep_default_na=False))
assets = read_asset_requirements(projects)
assert len(projects) == 54
assert projects["project_key"].nunique() == 54
assert len(assets) == 109
assert len({a["asset_key"] for a in assets}) == 109
assert all(a["asset_stage"] in ["existing", "proposed", "uncertain"] for a in assets)
assert len([a for a in assets if a["source_page"] == "36"]) == 2
assert len([a for a in assets if a["source_page"] == "39"]) == 3
assert len([a for a in assets if a["source_page"] == "52"]) == 4
assert [a["circuit"] for a in assets if a["source_page"] == "15"] == ["1", "2"]
assert next(a for a in assets if a["source_page"] == "18")["asset_identifier"] == "DESCSQ #1151"
assert next(a for a in assets if a["source_page"] == "35")["endpoint_a"] == "Orangeburg #1"
assert next(a for a in assets if a["source_page"] == "44")["voltage_kv"] == "115"
assert get_action_type(projects.loc[projects.source_page == "8", "project_name"].iloc[0]) == "rebuild"


def station(name, feature_id, state="SC"):
    return {"state": state, "asset_type": "station", "feature_id": feature_id,
            "feature_name": name, "names": [name], "voltage_kv": [115],
            "geometry": {"type": "Point", "coordinates": [-80, 33]},
            "tags": {"power": "substation"}, "source": "overpass", "source_file": "fixture",
            "source_url": "https://www.openstreetmap.org/" + feature_id}


subset = [a for a in assets if a["source_page"] == "9"]
feature = station("Cainhoy Substation", "node/1")
review = find_project_matches(subset, [feature])
assert (review["asset_status"] == "located").sum() == 1
assert review["asset_key"].nunique() == 4
summary = summarize_coverage(projects[projects.source_page == "9"], review)
assert summary.iloc[0]["coverage"] == "some"
assert summary.iloc[0]["required_assets"] == 4
assert json.loads(review.loc[review.asset_status == "located", "geometry"].iloc[0]) == feature["geometry"]

# No cross-state matches, even when names and voltages agree.
assert not (find_project_matches(subset, [station("Cainhoy Substation", "node/2", "GA")])
            ["asset_status"] == "located").any()
# Several features cannot inflate the number of located requirements.
duplicate_review = pd.concat([review, review], ignore_index=True)
assert summarize_coverage(projects[projects.source_page == "9"], duplicate_review).iloc[0]["located_assets"] == 1

# Real line geometry is preserved as a candidate until circuit/segment scope is checked.
line_asset = next(a for a in assets if a["source_page"] == "45")
line = {**feature, "asset_type": "line", "feature_id": "way/5", "feature_name": "Wateree - Killian",
        "names": ["Wateree - Killian"], "voltage_kv": [230], "tags": {"power": "line"},
        "geometry": {"type": "LineString", "coordinates": [[-80, 33], [-80.2, 33.1], [-80.5, 33.3]]}}
line_review = find_project_matches([line_asset], [line])
assert line_review.iloc[0]["asset_status"] == "candidate"
assert line_review.iloc[0]["match_basis"] == "both_endpoint_names"
assert json.loads(line_review.iloc[0]["geometry"]) == line["geometry"]
verified = apply_verified_assets(line_review, {
    (line_asset["asset_key"], "way/5"): "TEST FIXTURE ONLY: full required source line independently checked.",
})
assert verified.iloc[0]["asset_status"] == "located"
assert verified.iloc[0]["geometry"] == line_review.iloc[0]["geometry"]
bad = line_review.copy()
bad["geometry"] = json.dumps({"type": "Point", "coordinates": [-80, 33]})
try:
    apply_verified_assets(bad, {(line_asset["asset_key"], "way/5"): "An endpoint only"})
    raise AssertionError("An endpoint must not be accepted as a line")
except ValueError:
    pass

# Changed descriptions cannot silently reuse a previously checked asset list.
changed = projects.iloc[[0]].copy()
changed["description"] = "Rebuild lines A-B and B-C."
assert read_asset_requirements(changed)[0]["asset_type"] == "scope_review_needed"
print("Asset discovery checks passed.")
