"""Nearest segment positions, crossings, multipart gaps and point fallback."""
import json
import pandas as pd
from gridlock.geolocation import find_overlap_candidates


def project(key, geometry, lon=0, lat=0):
    return pd.DataFrame([dict(project_key=key, project_id=key, project_name=key,
                             geometry=geometry, longitude=lon, latitude=lat,
                             location_method="saved_traced_route", confidence="medium")])


route = project("route", {"type": "LineString", "coordinates": [[-2, 0], [2, 0]]})
point = project("point", None, 1.8, 0.01)
before = route.to_json()
found = find_overlap_candidates(route, point, 40)
assert len(found) == 1  # Its route center is about 200 km away; its nearest part is close.
pair = found.iloc[0]
assert 1 < pair.distance_km < 1.2
assert abs(pair.desc_nearest_longitude - 1.8) < 0.001
assert abs(pair.desc_nearest_latitude) < 0.001
assert pair.gpc_nearest_latitude > 0.009
assert route.to_json() == before

a = project("a", {"type": "LineString", "coordinates": [[-1, -1], [1, 1]]})
b = project("b", {"type": "LineString", "coordinates": [[-1, 1], [1, -1]]})
assert find_overlap_candidates(a, b, 0.001).iloc[0].distance_km < 0.001

parts = {"type": "MultiLineString", "coordinates": [[[-2, 0], [-1, 0]], [[1, 0], [2, 0]]]}
assert find_overlap_candidates(project("parts", parts), project("middle", None), 40).empty
mixed = {"type": "GeometryCollection", "geometries": [parts, {"type": "Point", "coordinates": [0, 0]}]}
assert find_overlap_candidates(project("mixed", mixed), project("middle", None), 40).empty
assert len(find_overlap_candidates(project("json", json.dumps(parts)), project("near", None, 1.5, 0.01), 40)) == 1
assert find_overlap_candidates(project("missing", None, None, None), point, 40).empty
assert find_overlap_candidates(point, point, 0).iloc[0].distance_km == 0
one_vertex = project("legacy", {"type": "LineString", "coordinates": [[0, 0]]})
assert find_overlap_candidates(one_vertex, project("origin", None), 0).iloc[0].distance_km == 0
print("Nearest-route proximity checks passed")
