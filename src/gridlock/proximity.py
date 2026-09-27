"""Closest-route screening in a local meter-based coordinate system."""

import json
import pandas as pd
from pyproj import Transformer
from shapely.geometry import shape, Point, GeometryCollection
from shapely.ops import nearest_points, transform


def route_parts(geometry):
    if geometry["type"] == "LineString":
        return [shape(geometry)] if len(geometry["coordinates"]) >= 2 else []
    if geometry["type"] == "MultiLineString":
        return [shape({"type": "LineString", "coordinates": part})
                for part in geometry["coordinates"] if len(part) >= 2]
    if geometry["type"] == "GeometryCollection":
        return [line for part in geometry["geometries"] for line in route_parts(part)]
    return []


def project_shape(project):
    """Use every traced line part, otherwise the existing representative point."""
    geometry = project.get("geometry")
    if isinstance(geometry, str) and geometry:
        geometry = json.loads(geometry)
    if isinstance(geometry, dict):
        lines = route_parts(geometry)
        if lines:
            return GeometryCollection(lines)
    # A legacy one-coordinate line is not a segment; use its existing point.
    lon, lat = project.get("longitude"), project.get("latitude")
    if lon in [None, ""] or lat in [None, ""] or pd.isna(lon) or pd.isna(lat):
        return None
    return Point(float(lon), float(lat))


def prepare_proximity(desc, gpc):
    """Project once for this regional screening run, keeping source data unchanged."""
    groups = []
    for table in [desc, gpc]:
        rows = []
        for project in table.to_dict("records"):
            geometry = project_shape(project)
            if geometry is not None and not geometry.is_empty:
                rows.append(dict(project, proximity_shape=geometry))
        groups.append(rows)
    shapes = [row["proximity_shape"] for group in groups for row in group]
    if not shapes:
        return groups[0], groups[1], None
    bounds = GeometryCollection(shapes).bounds
    lon, lat = (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2
    # Local azimuthal equidistant projection. Regional screening distances are
    # approximate, not survey measurements; do not measure longitude degrees.
    local_crs = f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m"
    forward = Transformer.from_crs("EPSG:4326", local_crs, always_xy=True)
    backward = Transformer.from_crs(local_crs, "EPSG:4326", always_xy=True)
    for group in groups:
        for row in group:
            row["proximity_shape"] = transform(forward.transform, row["proximity_shape"])
    return groups[0], groups[1], backward


def closest_positions(a, b, backward):
    """Closest positions can lie inside a segment, not just at route vertices."""
    point_a, point_b = nearest_points(a["proximity_shape"], b["proximity_shape"])
    distance = point_a.distance(point_b) / 1000
    lon_a, lat_a = backward.transform(point_a.x, point_a.y)
    lon_b, lat_b = backward.transform(point_b.x, point_b.y)
    return distance, lon_a, lat_a, lon_b, lat_b
