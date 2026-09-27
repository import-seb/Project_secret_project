"""Dashboard controls for finding nearby published project milestones."""

import html
import folium
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_folium import st_folium
from gridlock.area_planner import lookup_zip, find_nearby_projects, add_timing_evidence, rank_year_spans
from gridlock.route_maps import add_traced_routes


def render_area_planner(projects, routes):
    st.subheader("Plan around nearby project milestones")
    st.caption("Choose a location and search radius. Published in-service and need years are coordination leads; they do not establish when crews will be working. Current coverage: DESC and Georgia Power.")
    method = st.radio("Choose a location using", ["Coordinates", "ZIP code", "Map pin"], horizontal=True, key="planner_method")
    if method == "Coordinates":
        left, right = st.columns(2)
        latitude = left.number_input("Latitude", min_value=-90.0, max_value=90.0, value=33.46, format="%.6f")
        longitude = right.number_input("Longitude", min_value=-180.0, max_value=180.0, value=-81.97, format="%.6f")
        if st.button("Use coordinates"):
            st.session_state.planner_location = dict(latitude=latitude, longitude=longitude, label="Entered coordinates")
    elif method == "ZIP code":
        zip_code = st.text_input("US ZIP code", max_chars=5)
        if st.button("Look up ZIP"):
            st.session_state.planner_zip_places = []
            try:
                st.session_state.planner_zip_places = lookup_zip(zip_code)
            except (ValueError, requests.RequestException) as error:
                st.error(f"ZIP lookup failed: {error}")
        places = st.session_state.get("planner_zip_places", [])
        if places:
            choice = st.selectbox("Postal place", range(len(places)), format_func=lambda index: places[index]["label"])
            st.caption("Approximate postal place location from Zippopotam.us / GeoNames, not a project site or ZIP boundary. Refine with coordinates or a map pin.")
            if st.button("Use ZIP location"):
                st.session_state.planner_location = places[choice]
    else:
        location = st.session_state.get("planner_location", dict(latitude=33.46, longitude=-81.97))
        picker = folium.Map(location=[location["latitude"], location["longitude"]], zoom_start=8)
        folium.Marker([location["latitude"], location["longitude"]], tooltip="Click anywhere to set the project location").add_to(picker)
        st.caption("Click anywhere on the map to place your project pin.")
        result = st_folium(picker, key="planner_pin_map", height=380, use_container_width=True, returned_objects=["last_clicked"])
        clicked = result.get("last_clicked")
        if clicked:
            new_location = dict(latitude=clicked["lat"], longitude=clicked["lng"], label="Map pin")
            if new_location != st.session_state.get("planner_location"):
                st.session_state.planner_location = new_location
                st.rerun()

    location = st.session_state.get("planner_location")
    if not location:
        st.info("Set a location to see nearby milestones.")
        return
    st.write(f"{location['label']}: {location['latitude']:.6f}, {location['longitude']:.6f}")
    left, middle, right = st.columns(3)
    radius = left.number_input("Search radius (km)", min_value=1.0, max_value=200.0, value=40.0)
    current_year = pd.Timestamp.now().year
    first_year = middle.number_input("First milestone year", min_value=2000, max_value=2100, value=current_year)
    last_year = right.number_input("Last milestone year", min_value=2000, max_value=2100, value=current_year + 10)
    if last_year < first_year or last_year - first_year > 50:
        st.error("Choose an ordered year range of no more than 50 years.")
        return
    nearby = find_nearby_projects(projects, location["latitude"], location["longitude"], radius)
    evidence = add_timing_evidence(nearby)
    spans = rank_year_spans(evidence, first_year, last_year)
    st.caption("Distance uses the nearest part of each traced route, or the project's point when no route is available. Unlocated projects cannot be included.")
    if nearby.empty:
        st.info("No located projects in the current dataset fall within this radius. This does not establish that no work is planned nearby.")
        return
    st.write(f"{nearby.project_key.nunique()} nearby projects; {sum(evidence.milestone_years.apply(len).eq(0))} without published milestone dates.")
    if spans.empty:
        st.info("No eligible published milestones in the selected years. Nearby projects remain listed below.")
        supporting = evidence
    else:
        st.markdown("**Milestone years to investigate first**")
        st.caption("Ranked by nearby project count, then utility count, nearest distance and earliest year. Adjacent years are combined only when the same projects have milestones in every year; these are not construction windows.")
        spans["milestone_years"] = spans.apply(lambda row: str(row.start_year) if row.start_year == row.end_year else f"{row.start_year}–{row.end_year}", axis=1)
        st.dataframe(spans[["milestone_years", "nearby_projects", "utilities", "nearest_project_km"]], hide_index=True)
        selected = st.selectbox("Show projects supporting", range(len(spans)), format_func=lambda index: spans.iloc[index].milestone_years)
        supporting = evidence[evidence.project_key.isin(spans.iloc[selected].project_keys)]

    center = [location["latitude"], location["longitude"]]
    result_map = folium.Map(location=center, zoom_start=9)
    folium.Marker(center, tooltip="Your project location", icon=folium.Icon(color="green")).add_to(result_map)
    folium.Circle(center, radius=radius * 1000, color="green", fill=False, tooltip="Search radius").add_to(result_map)
    selected_routes = routes[routes.project_key.isin(supporting.project_key)]
    add_traced_routes(result_map, selected_routes)
    for project in supporting.to_dict("records"):
        closest = [project["nearest_latitude"], project["nearest_longitude"]]
        label = html.escape(f"{project['project_name']} — {project['distance_km']:.2f} km ({project['location_basis']})")
        folium.PolyLine([center, closest], color="purple", dash_array="5 5", weight=2, tooltip=label).add_to(result_map)
        folium.CircleMarker(closest, radius=4, color="purple", tooltip=label).add_to(result_map)
    result_map.fit_bounds([center] + supporting[["nearest_latitude", "nearest_longitude"]].values.tolist(), max_zoom=11)
    folium.LayerControl().add_to(result_map)
    components.html(result_map.get_root().render(), height=520)
    st.caption("Purple links end at the nearest route position or fallback point. Dark solid routes are resolved; lighter dashed routes need review. Candidate routes and point locations require verification.")
    columns = ["utility", "state", "project_name", "distance_km", "location_basis", "location_method", "milestone_years", "milestone_type", "in_service_date", "in_service_dates", "need_date", "status", "timing_basis"]
    columns = [column for column in columns if column in evidence.columns]
    st.markdown("**Supporting projects**")
    st.dataframe(supporting[columns], hide_index=True)
    with st.expander("All nearby projects, including missing dates and excluded statuses"):
        st.dataframe(evidence[columns], hide_index=True)
