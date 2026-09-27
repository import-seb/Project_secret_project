"""The same saved route data and colors for notebooks 03 and 04."""

import html
import json
import ast
import folium
import pandas as pd
from gridlock.paths import PROCESSED_DATA_DIR
from gridlock.geolocation import geometry_points

ROUTE_COLORS = {
    ("DESC", "resolved"): "#1e3a8a",
    ("DESC", "needs_review"): "#60a5fa",
    ("GPC", "resolved"): "#991b1b",
    ("GPC", "needs_review"): "#f87171",
}


def load_traced_routes():
    """Read accepted route candidates, retaining original saved routing status."""
    path = PROCESSED_DATA_DIR / "geo/project_route_review.csv"
    review = pd.read_csv(path, dtype=str, keep_default_na=False)
    routes = review[
        review.asset_type.eq("line") & review.scope_role.eq("work")
        & review.route_status.isin(["saved_route_preserved", "resolved", "review_required"])
        & review.geometry.ne("")
    ].copy()
    statuses = []
    for row in routes.to_dict("records"):
        status = row["route_status"]
        if status == "saved_route_preserved":
            status = json.loads(row["saved_evidence"])["route_status"]
        statuses.append("resolved" if status == "resolved" else "needs_review")
    routes["map_status"] = statuses
    routes["geometry"] = routes.geometry.apply(json.loads)
    return routes


def add_traced_routes(map_object, routes):
    """Draw separate utility/status layers and return their bounds coordinates."""
    bounds = []
    for (utility, status), color in ROUTE_COLORS.items():
        selected = routes[routes.utility.eq(utility) & routes.map_status.eq(status)]
        label = "resolved" if status == "resolved" else "needs review"
        layer = folium.FeatureGroup(name=f"{utility} routes - {label} ({len(selected)})", show=True)
        for row in selected.to_dict("records"):
            fields = {
                "Project": row["project_name"], "Project ID": row["project_id"],
                "Asset": row["asset_name"], "Utility": utility, "Route status": label,
                "Voltage (kV)": row["voltage_kv"], "Circuit": row.get("circuit", ""),
                "Method": row["route_method"], "Scope": row["scope_notes"],
                "Review note": row["review_note"],
            }
            popup = "<br>".join(f"<b>{html.escape(key)}:</b> {html.escape(str(value))}"
                                  for key, value in fields.items() if value)
            popup += "<br><i>Routing status does not certify exact project work limits.</i>"
            feature = {"type": "Feature", "geometry": row["geometry"],
                       "properties": {"color": color, "dash": "" if status == "resolved" else "8 5"}}
            folium.GeoJson(
                feature,
                style_function=lambda f: {"color": f["properties"]["color"], "weight": 4,
                                          "opacity": 0.9, "dashArray": f["properties"]["dash"]},
                tooltip=html.escape(f"{utility}: {row['asset_name']} ({label})"),
                popup=folium.Popup(popup, max_width=440),
            ).add_to(layer)
            bounds.extend([[lat, lon] for lon, lat in geometry_points(row["geometry"])])
        layer.add_to(map_object)
    legend = '<div style="position:fixed;bottom:30px;right:30px;z-index:9999;background:white;padding:12px;border:1px solid #999;font-size:13px"><b>Traced routes</b><br>'
    for (utility, status), color in ROUTE_COLORS.items():
        border = "solid" if status == "resolved" else "dashed"
        legend += f'<span style="display:inline-block;width:28px;border-top:4px {border} {color}"></span> {utility}: {status.replace("_", " ")}<br>'
    legend += '<small>Resolved = saved routing status;<br>exact work limits may still need review.</small></div>'
    map_object.get_root().html.add_child(folium.Element(legend))
    return bounds


def add_proximity_connections(map_object, candidates, max_distance_km=40, layer=None):
    """Draw saved closest positions; never reconstruct links from project centers."""
    required = ["desc_nearest_latitude", "desc_nearest_longitude",
                "gpc_nearest_latitude", "gpc_nearest_longitude", "distance_method"]
    if any(column not in candidates for column in required):
        raise ValueError("Nearest-route coordinates are missing. Rerun notebook 03.")
    if not candidates.distance_method.eq("nearest_route_or_point_local_meters").all():
        raise ValueError("Outdated proximity distances. Rerun notebook 03.")
    if layer is None:
        layer = folium.FeatureGroup(name=f"{max_distance_km:g} km nearest-route proximity", show=True)
        layer.add_to(map_object)
    bounds = []
    for row in candidates.to_dict("records"):
        distance = float(row["distance_km"])
        if distance > max_distance_km:
            continue
        positions = [[float(row["desc_nearest_latitude"]), float(row["desc_nearest_longitude"])],
                     [float(row["gpc_nearest_latitude"]), float(row["gpc_nearest_longitude"])]]
        desc_name = html.escape(str(row["desc_project_name"]))
        gpc_name = html.escape(str(row.get("gpc_corridor_family", row.get("gpc_project_name", ""))))
        popup = (f"<b>Proximity candidate</b><br>DESC: {desc_name}<br>GPC: {gpc_name}"
                 f"<br>Nearest route/point distance: {distance:.1f} km"
                 "<br><i>Screening lead, not a confirmed corridor overlap.</i>")
        folium.PolyLine(positions, color="#7c3aed", weight=3, opacity=0.8, dash_array="8 8",
                        tooltip=f"Nearest route/point distance: {distance:.1f} km",
                        popup=folium.Popup(popup, max_width=400)).add_to(layer)
        bounds.extend(positions)
    return bounds


def selected_connection_map(candidate, desc, gpc, routes):
    """Show a selected family, its route evidence and the actual closest connector."""
    desc_projects = desc[desc.project_key.eq(candidate["desc_project_key"])]
    ids = candidate["gpc_project_ids"]
    if isinstance(ids, str):
        ids = ast.literal_eval(ids)
    gpc_projects = gpc[gpc.project_id.astype(str).isin([str(value) for value in ids])]
    selected = pd.concat([desc_projects, gpc_projects], ignore_index=True)
    selected_routes = routes[routes.project_key.isin(selected.project_key)]
    m = folium.Map(location=[float(candidate["desc_nearest_latitude"]),
                             float(candidate["desc_nearest_longitude"])], tiles=None)
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        attr="Esri", name="Dark map").add_to(m)
    bounds = add_traced_routes(m, selected_routes)
    for utility, table, color in [("DESC", desc_projects, "blue"), ("GPC", gpc_projects, "red")]:
        route_names = set(selected_routes.loc[selected_routes.utility.eq(utility), "project_name"].astype(str).str.strip())
        for row in table.to_dict("records"):
            if str(row["project_name"]).strip() in route_names:
                continue
            position = [float(row["latitude"]), float(row["longitude"])]
            folium.Marker(position, tooltip=html.escape(str(row["project_name"])),
                          icon=folium.Icon(color=color)).add_to(m)
            bounds.append(position)
    bounds.extend(add_proximity_connections(m, pd.DataFrame([candidate]), max_distance_km=40))
    if bounds:
        m.fit_bounds(bounds)
    folium.LayerControl(collapsed=False).add_to(m)
    return m
