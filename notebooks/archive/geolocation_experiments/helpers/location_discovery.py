"""Find endpoint candidates in bulk OSM data. No scoring or project geometry."""

import json
import re
import time

import pandas as pd
import requests

from gridlock.paths import RAW_DATA_DIR, PROCESSED_DATA_DIR
from gridlock.geolocation import normalize_state, osm_geometry, fetch_osm_substations, fetch_osm_lines

OVERPASS_URL = "https://overpass.private.coffee/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "GridLock-location-discovery/0.1 (one-time endpoint research)"}

# These are parsing words, not a list of project-specific locations.
ELECTRICAL_WORDS = """substation substations station stations sub transmission distribution
line lines loop rebuild rebuilding construct constructing construction tap tie ties fold
in spdc spsc acsr acss b1272 b795 conductor conductors circuit circuits terminal terminals
upgrade upgrades upgrading convert converting new existing replace replacing replacement
move modify add adding install installing installation extend expand portion portions
section sections phase phases project projects customer supporting self steel structures
structure pole poles wood wooden laminated bus bank autobank transformer transformers
reactor reactors relay relays panel panels switch switches switching house equipment
single double supporting string restring insulated insulation breaker breakers goab
gpc desc descsq dep scdot us78 cip hut mva tbd approx approximately miles mile length
total standard end life required service need date streetlight and from to between at
the for with into off on of will be is are as both by this that includes include
roadway widening funded funded impact medium countywide no work northbound southbound
verify modification modifications energy duke progress sw sta large angles dead ends
polls series laminated supporting hardening system estimated str strs current future
because replace room plant bay bays feeder feeders underbuild underbuilt notes
set pull most we there due designed bec point across scope ibis interconnection way frame osmose crossing
""".split()
ELECTRICAL_WORDS.remove("house")  # 'Red House Road' is a real place phrase.


def normalize_endpoint(name):
    name = str(name).lower().replace("’", "'")
    name = re.sub(r"\b\d+(?:\.\d+)?(?:\s*[-/]\s*\d+(?:\.\d+)?)*\s*kv\b", " ", name)
    name = re.sub(r"#\s*\d+", " ", name)
    name = re.sub(r"\bst[.]?(?=\s)", "saint", name)
    name = re.sub(r"\bst[.]?$", "street", name)
    name = re.sub(r"\bjct\b", "junction", name)
    name = re.sub(r"\brd\b", "road", name)
    name = re.sub(r"\bn\.\s*", "north ", name)
    name = re.sub(r"\b(substation|substations|station|sub|switchyard|switching|transmission|distribution|line|lines)\b", " ", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return " ".join(name.split())


def extract_endpoint_names(project):
    """Read capitalized place phrases from both source fields; discard electrical text."""
    names = []
    for field in ["project_name", "description"]:
        text = str(project.get(field, ""))
        text = text.replace("–", "-").replace("—", "-").replace("�", "-")
        text = re.sub(r"\b(St|Jct|Rd)\.", r"\1", text)
        text = re.sub(r"\bN\.\s*", "North ", text)
        text = re.sub(r"\bswitch(?:ing)?\s+house\b", " | ", text, flags=re.I)
        text = re.sub(r"\b\d+(?:\.\d+)?(?:\s*[-/]\s*\d+(?:\.\d+)?)*\s*kv\b", " | ", text, flags=re.I)
        text = re.sub(r"#\s*\d+", " ", text)
        text = re.sub(r"\b(?:" + "|".join(ELECTRICAL_WORDS) + r")\b", " | ", text, flags=re.I)
        # Names in these source documents are capitalized, including VCS1 and PSA.
        for phrase in re.findall(r"\b[A-Z][A-Za-z0-9']*(?:[ \t]+[A-Z][A-Za-z0-9']*)*", text):
            name = normalize_endpoint(phrase)
            if name and len(name) > 1 and name not in ["road", "river", "marsh", "house"] and name not in names:
                names.append(name)
    # Do not count a shortened road name twice within a project.
    return [name for name in names if not (
        len(name.split()) >= 2 and name + " road" in names)]


def fetch_all_power(state):
    """Reuse statewide caches; add plants and explicitly tagged switchyards."""
    state = normalize_state(state)
    datasets = [fetch_osm_substations(state), fetch_osm_lines(state)]
    folder = RAW_DATA_DIR / "osm" / state
    path = folder / "power_plants_switchyards_overpass.json"
    query = f'''[out:json][timeout:180];area["ISO3166-2"="US-{state}"]["admin_level"="4"]->.search_state;nwr["power"~"^(plant|switchyard)$"](area.search_state);out geom;'''
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        (folder / "power_plants_switchyards_query.overpassql").write_text(query, encoding="utf-8")
        response = requests.post(OVERPASS_URL, data={"data": query}, headers=HEADERS, timeout=240)
        response.raise_for_status()
        data = response.json()
        if data.get("remark") or "elements" not in data:
            raise ValueError("Overpass returned incomplete plants/switchyards")
        with path.open("xb") as file:
            file.write(response.content)
    datasets.append(data)
    features = []
    seen = set()
    for dataset in datasets:
        if dataset.get("remark") or "elements" not in dataset:
            raise ValueError("Incomplete Overpass cache")
        for element in dataset["elements"]:
            feature_id = element["type"] + "/" + str(element["id"])
            if feature_id in seen:
                continue
            seen.add(feature_id)
            tags = element.get("tags", {})
            aliases = []
            for key in ["name", "alt_name", "old_name", "official_name", "short_name", "ref"]:
                aliases.extend(name.strip() for name in tags.get(key, "").split(";") if name.strip())
            features.append({
                "matched_feature_name": tags.get("name", aliases[0] if aliases else ""),
                "osm_feature_id": feature_id, "osm_feature_type": element["type"],
                "geometry": osm_geometry(element, "substation" if tags.get("power") in ["plant", "switchyard"] else tags.get("power")),
                "aliases": aliases, "source": "overpass", "state": state,
            })
    return features


def name_matches(endpoint, name, partial=False):
    endpoint, name = normalize_endpoint(endpoint), normalize_endpoint(name)
    if not endpoint or not name:
        return False
    if endpoint == name:
        return True
    if not partial:
        return False
    # Whole words only: 'North' does not match 'Northwest'. No scores or ranking.
    words, other = set(endpoint.split()), set(name.split())
    return words.issubset(other) or (len(other) >= 2 and other.issubset(words))


def match_endpoint(endpoint, features):
    """Exact normalized names first; then whole-word partials, including line names."""
    for partial in [False, True]:
        matches = []
        for feature in features:
            if feature["geometry"] and any(name_matches(endpoint, name, partial) for name in feature["aliases"]):
                matches.append(feature)
        if matches:
            return matches
    return []


def search_nominatim(endpoint, state):
    """One-time, sequential, cached fallback; only actual power features count."""
    state = normalize_state(state)
    folder = RAW_DATA_DIR / "osm" / state / "nominatim"
    folder.mkdir(parents=True, exist_ok=True)
    name = normalize_endpoint(endpoint)
    matches = []
    seen = set()
    for kind in ["substation", "power plant"]:
        query = f"{name} {kind}, {state}, United States"
        path = folder / (name.replace(" ", "_") + "_" + kind.replace(" ", "_") + ".json")
        if path.exists():
            results = json.loads(path.read_text(encoding="utf-8"))["results"]
        else:
            # Each uncached request waits, even when the preceding request failed.
            time.sleep(1.1)
            response = requests.get(NOMINATIM_URL, params={
                "q": query, "format": "jsonv2", "countrycodes": "us", "limit": 10,
                "addressdetails": 1, "extratags": 1, "namedetails": 1, "polygon_geojson": 1,
            }, headers=HEADERS, timeout=60)
            response.raise_for_status()
            results = response.json()
            if not isinstance(results, list):
                raise ValueError("Unexpected Nominatim response")
            with path.open("x", encoding="utf-8") as file:
                json.dump({"query": query, "results": results}, file, ensure_ascii=False, indent=2)
        for result in results:
            address = result.get("address", {})
            if address.get("ISO3166-2-lvl4") != "US-" + state:
                continue
            power = result.get("extratags", {}).get("power", "")
            if power not in ["substation", "line", "plant", "switchyard"] and not (
                result.get("category") == "power" and result.get("type") in ["substation", "line", "plant", "switchyard"]):
                continue
            feature_name = result.get("name", "")
            aliases = [feature_name] + list(result.get("namedetails", {}).values())
            if not any(name_matches(name, alias, partial=True) for alias in aliases):
                continue
            geometry = result.get("geojson")
            if not geometry or not result.get("osm_id"):
                continue
            feature_id = result["osm_type"] + "/" + str(result["osm_id"])
            if feature_id in seen:
                continue
            seen.add(feature_id)
            matches.append({"matched_feature_name": feature_name,
                            "osm_feature_id": feature_id, "osm_feature_type": result["osm_type"],
                            "geometry": geometry, "source": "nominatim", "state": state})
        if matches:
            break
    return matches


def discover_locations(projects, infrastructure, use_nominatim=True):
    """Match each distinct endpoint once per state, then reuse across projects."""
    endpoint_cache = {}
    rows = []
    summary = {"Total projects": len(projects), "Total endpoint names extracted": 0,
               "Endpoints matched": 0, "Projects with at least one matched endpoint": 0,
               "Projects with two matched endpoints": 0, "Projects with no matched endpoints": 0}
    for project in projects:
        state = normalize_state(project["state"])
        endpoints = extract_endpoint_names(project)
        summary["Total endpoint names extracted"] += len(endpoints)
        found = 0
        for endpoint in endpoints:
            key = (state, endpoint)
            if key not in endpoint_cache:
                matches = match_endpoint(endpoint, infrastructure[state])
                if not matches and use_nominatim:
                    matches = search_nominatim(endpoint, state)
                endpoint_cache[key] = matches
            matches = endpoint_cache[key]
            if matches:
                found += 1
            for match in matches or [None]:
                rows.append({
                    "project_id": project["project_id"], "project_name": project["project_name"],
                    "endpoint_name": endpoint,
                    "matched_feature_name": match["matched_feature_name"] if match else "",
                    "osm_feature_id": match["osm_feature_id"] if match else "",
                    "osm_feature_type": match["osm_feature_type"] if match else "",
                    "geometry": json.dumps(match["geometry"], ensure_ascii=False) if match else "",
                    "source": match["source"] if match else "",
                })
        if not endpoints:
            rows.append(dict(project_id=project["project_id"], project_name=project["project_name"],
                             endpoint_name="", matched_feature_name="", osm_feature_id="",
                             osm_feature_type="", geometry="", source=""))
        summary["Endpoints matched"] += found
        summary["Projects with at least one matched endpoint"] += found >= 1
        summary["Projects with two matched endpoints"] += found >= 2
        summary["Projects with no matched endpoints"] += found == 0
    return pd.DataFrame(rows), summary
