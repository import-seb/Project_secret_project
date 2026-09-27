"""Offline checks for real-node routing and project corridor extraction."""
import pandas as pd
import json
from gridlock.project_routes import (read_asset_scope, find_asset_facility, named_public_lines,
                                     trace_project_routes, apply_traced_routes, check_route_extent, SCOPE_PATH)
from gridlock.public_lines import trace_public_path
from gridlock.geolocation import geolocate_projects, load_known_routes, apply_known_routes
from gridlock.osm_power_network import build_voltage_graph, route_between_points

scope = pd.read_csv(SCOPE_PATH, dtype=str, keep_default_na=False)
gpc = scope[scope.utility.eq("GPC")]
assert gpc.project_key.nunique() == 122 and scope.asset_key.is_unique

def work_lines(pid):
    return gpc[gpc.project_id.eq(pid) & gpc.asset_type.eq("line") & gpc.scope_role.eq("work")]

assert work_lines("17993")[["endpoint_a", "endpoint_b"]].values.tolist() == [["Thomson Primary", "Pumpkin Center"]]
assert work_lines("19632")[["endpoint_a", "endpoint_b"]].values.tolist() == [["Hopewell", "Birmingham"]]
assert work_lines("20428")[["endpoint_a", "endpoint_b"]].values.tolist() == [
    ["Deepstep", "Robins Spring"], ["Robins Spring", "Kaolin J"], ["Kaolin J", "Sandersville #6"]]
assert work_lines("20668").empty  # Relay panels are station work, not a full corridor.
assert work_lines("16887").voltage_kv.tolist() == ["115"]  # Existing, not future 230 kV.
assert len(work_lines("20512")) == 2
assert len(work_lines("20771")[work_lines("20771").endpoint_a.eq("Line Creek")]) == 2
assert work_lines("09661").asset_stage.tolist() == ["proposed"]

projects = scope.drop_duplicates("project_key").copy()
assert len(read_asset_scope(projects)) == len(scope)
changed = projects.iloc[[0]].copy()
changed["description"] = "Different work scope"
assert read_asset_scope(changed).asset_type.tolist() == ["scope_review_needed"]
changed["project_key"] = "GA:new.pdf:1:new"
assert read_asset_scope(changed).scope_source.tolist() == ["unchecked"]

# Exact endpoint identity cannot drop station numbers or turn a tap into a station.
def facility(name, state="GA"):
    return dict(name=name, names=[name], state=state, source="osm", kind="substation",
                geometry={"type": "Point", "coordinates": [-83, 33]}, source_file="fixture",
                source_url="fixture", feature_id=name, review_required=False)

assert find_asset_facility("Sandersville #6", "GA", [facility("Sandersville #1")]) is None
assert find_asset_facility("Pearson tap", "GA", [facility("Pearson")]) is None
assert find_asset_facility("Alpha", "SC", [facility("Alpha")]) is None

nodes = pd.DataFrame([
    {"node_id": 1, "lon": -83.0, "lat": 33.0},
    {"node_id": 2, "lon": -82.99, "lat": 33.01},
    {"node_id": 3, "lon": -82.98, "lat": 33.0},
    # Same coordinate, different OSM node: not an electrical connection.
    {"node_id": 4, "lon": -82.99, "lat": 33.01},
    {"node_id": 5, "lon": -82.99, "lat": 33.02},
])
base = dict(power="line", name=None, operator=None, owner=None, ref=None, circuits=None, cables=None)
ways = pd.DataFrame([
    dict(base, way_id=10, voltages_kv=[115], node_ids=[1, 2, 3]),
    dict(base, way_id=20, voltages_kv=[115], node_ids=[4, 5]),
    dict(base, way_id=30, voltages_kv=[], node_ids=[3, 5]),
])
graph = build_voltage_graph(ways, nodes, 115)
route = route_between_points(graph, -83, 33, -82.98, 33, snap_max_km=0.01)
assert route["osm_way_ids"] == [10]
assert route["geometry"]["coordinates"] == [[-83, 33], [-82.99, 33.01], [-82.98, 33]]
assert route_between_points(graph, -83, 33, -82.99, 33.02, snap_max_km=0.01) is None
assert build_voltage_graph(ways, nodes, 230).edge_count == 0
# Public GIS fallback preserves every original vertex and excludes wrong voltage.
def public_line(fid, coords, a="Alpha", b="Beta", voltage=115):
    return dict(feature_id=fid, voltage_kv=[voltage], tags={"sub_1": a, "sub_2": b},
                geometry={"type": "MultiLineString", "coordinates": [coords]}, source_file="fixture")

asset = {"endpoint_a": "Alpha", "endpoint_b": "Beta", "voltage_kv": "115"}
first = [[-83,33],[-82.97,33.01],[-82.95,33]]
second = [[-82.95,33],[-82.92,33.01],[-82.90,33]]
lines = [public_line("one", first), public_line("two", second),
         public_line("wrong_voltage", first, voltage=230)]
assert len(named_public_lines(asset, lines)) == 2
public_route = trace_public_path(asset, {"coordinates": first[0]}, {"coordinates": second[-1]}, lines)
assert public_route["geometry"]["coordinates"] == [first, second]
assert public_route["segment_feature_ids"] == ["one", "two"]
disjoint = public_line("disjoint", first)
disjoint["geometry"]["coordinates"].append([[ -82, 33], [-81.9,33]])
assert trace_public_path(asset, {"coordinates": first[0]}, {"coordinates": [-81.9,33]}, [disjoint]) is None

# A real source line with the wrong extent must not become a project route.
long_route = {"geometry": json.dumps({"type": "LineString", "coordinates": [[-83,33],[-82,33]]}),
              "expected_length_miles": "1", "route_status": "review_required", "review_note": ""}
assert check_route_extent(long_route)["route_status"] == "route_extent_mismatch"

# One unresolved asset must survive alongside found assets of the same project.
mini = gpc[gpc.project_id.eq("20512")].copy()
mini["utility"] = "TEST"
mini["state"] = "ZZ"
mini_projects = mini.drop_duplicates("project_key")
review = trace_project_routes(mini_projects, download_missing=False, infrastructure=[], scope=mini)
assert set(review.asset_key) == set(mini.asset_key)
assert len(review) == 3 and review.geometry.eq("").all()

# All saved geometries remain in the actual notebook product.
from gridlock.paths import PROCESSED_DATA_DIR
product = pd.read_csv(PROCESSED_DATA_DIR / "dominion/dominion_projects_geo.csv", dtype=str, keep_default_na=False)
for row in load_known_routes("DESC").to_dict("records"):
    if row["route_status"] not in ["resolved", "review_required"] or not row["geometry"]:
        continue
    geometry = json.loads(product[product.project_key.eq(row["project_key"])].iloc[0].geometry)
    parts = geometry["geometries"] if geometry["type"] == "GeometryCollection" else [geometry]
    assert json.loads(row["geometry"]) in parts
print("Description scope, identity, OSM/GIS topology, unresolved assets and saved route tests passed")
