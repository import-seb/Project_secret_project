"""Area lookup and nearby project evidence for the planning tab."""

import json
import re
import pandas as pd
import requests
from gridlock.paths import RAW_DATA_DIR
from gridlock.proximity import prepare_proximity, closest_positions


def lookup_zip(zip_code):
    """Return postal place locations, not a project site or ZIP boundary."""
    zip_code = str(zip_code).strip()
    if not re.fullmatch(r"[0-9]{5}", zip_code):
        raise ValueError("Enter a five-digit US ZIP code, including any leading zero.")
    path = RAW_DATA_DIR / "geocoding/us_zip" / f"{zip_code}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        response = requests.get(f"https://api.zippopotam.us/us/{zip_code}", timeout=20)
        if response.status_code == 404:
            raise ValueError("That ZIP code was not found. Try coordinates or a map pin.")
        response.raise_for_status()
        data = response.json()
        if not data.get("places"):
            raise ValueError("The ZIP lookup returned no locations.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
    return [{"label": f"{place['place name']}, {place['state abbreviation']} {zip_code}",
             "latitude": float(place["latitude"]), "longitude": float(place["longitude"]),
             "source": "Zippopotam.us / GeoNames"} for place in data["places"]]


def find_nearby_projects(projects, latitude, longitude, radius_km):
    """Distance from the chosen location to the nearest route part or fallback point."""
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("Latitude must be -90 to 90 and longitude -180 to 180.")
    if not 0 < radius_km <= 200:
        raise ValueError("Choose a search radius greater than 0 and no more than 200 km.")
    query = pd.DataFrame([{"latitude": latitude, "longitude": longitude, "geometry": None}])
    sites, targets, backward = prepare_proximity(query, projects)
    rows = []
    for target in targets:
        distance, _, _, lon, lat = closest_positions(sites[0], target, backward)
        if distance <= radius_km:
            row = {key: value for key, value in target.items() if key != "proximity_shape"}
            row.update(distance_km=distance, nearest_longitude=lon, nearest_latitude=lat,
                       location_basis="point" if target["proximity_shape"].geom_type == "Point" else "route")
            rows.append(row)
    if not rows:
        return pd.DataFrame(columns=list(projects.columns) + ["distance_km", "nearest_longitude", "nearest_latitude", "location_basis"])
    return pd.DataFrame(rows).sort_values("distance_km").reset_index(drop=True)


def add_timing_evidence(nearby):
    """Use published milestone years only; do not infer construction windows."""
    rows = []
    for project in nearby.fillna("").to_dict("records"):
        dates = []
        if project.get("in_service_dates"):
            dates.extend(json.loads(project["in_service_dates"]))
        dates.extend([project.get("in_service_date", ""), project.get("need_date", "")])
        parsed = pd.to_datetime(pd.Series(dates, dtype=str), errors="coerce")
        years = sorted(set(parsed.dropna().dt.year.astype(int)))
        milestone_type = "in-service date" if project.get("in_service_date") or project.get("in_service_dates") else "need date"
        status = str(project.get("status", "")).strip().lower()
        excluded = status in ["completed", "complete", "cancelled", "canceled", "withdrawn"]
        project["milestone_years"] = years
        project["milestone_type"] = milestone_type if years else "missing date"
        project["planning_years"] = years if not excluded else []
        project["timing_basis"] = ("excluded: " + status if excluded else "no published date" if not years
                                   else "published milestone years only")
        rows.append(project)
    columns = list(nearby.columns) + ["milestone_years", "milestone_type", "planning_years", "timing_basis"]
    return pd.DataFrame(rows, columns=columns)


def rank_year_spans(evidence, first_year, last_year):
    """Rank by nearby project count, then utility count, proximity and earliest year.

    Adjacent years become one span only when the same projects support each year.
    Multiple assets or milestones of one project count once.
    """
    if first_year > last_year or last_year - first_year > 50:
        raise ValueError("Choose an ordered planning range of no more than 50 years.")
    columns = ["start_year", "end_year", "nearby_projects", "utilities", "utility_count", "nearest_project_km", "project_keys"]
    if evidence.empty:
        return pd.DataFrame(columns=columns)
    spans = []
    for year in range(first_year, last_year + 1):
        supporting = evidence[evidence.planning_years.apply(lambda years: year in years)].drop_duplicates("project_key")
        if supporting.empty:
            continue
        keys = sorted(supporting.project_key.tolist())
        if spans and spans[-1]["end_year"] == year - 1 and spans[-1]["project_keys"] == keys:
            spans[-1]["end_year"] = year
        else:
            spans.append({"start_year": year, "end_year": year, "nearby_projects": len(keys),
                          "utilities": ", ".join(sorted(supporting.utility.unique())),
                          "utility_count": supporting.utility.nunique(),
                          "nearest_project_km": float(supporting.distance_km.min()),
                          "project_keys": keys})
    result = pd.DataFrame(spans, columns=columns)
    return result.sort_values(["nearby_projects", "utility_count", "nearest_project_km", "start_year"],
                              ascending=[False, False, True, True]).reset_index(drop=True)
