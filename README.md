# GridLock

GridLock ingests DESC and Georgia Power planning projects, assigns defensible
project locations, and screens cross-utility proximity for the hackathon.

## Setup

Use the project virtual environment and editable installation:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[notebooks]"
```

Select `.venv\Scripts\python.exe` as the PyCharm/notebook interpreter. Imports
such as `from gridlock.paths import PROCESSED_DATA_DIR` work from any notebook
folder. No new dependencies were added by the consolidation.

## Three active notebooks

Run these in order from `notebooks/ingestion/`:

1. **01_dominion_ingestion.ipynb** reads the SCRTP PDF and produces 54 DESC projects.
   It preserves source-quality flags, descriptions and timing.
2. **02_georgia_power_ingestion.ipynb** downloads/reuses the official IRP ZIP,
   extracts only Volume 3, verifies PDF sections and extracts projects plus detailed descriptions. It
   produces the 208-entry ITS table and 122-entry GPC subset. Original ZIP/PDFs
   and existing selected PDFs are preserved.
3. **03_project_geolocation.ipynb** produces `df_desc_geo` and `df_gpc_geo` using
   shared functions, preserves saved routes, traces additional OSM corridor candidates for both utilities, and screens proximity.

No archived notebook needs to run. Originals and their helper code are under
`notebooks/archive/`.

## Geographic representation

Saved traced route geometry takes precedence. Otherwise, use a labeled midpoint
between two named facilities, a single named facility, or a reviewed center for
multiple facilities. All source locations remain in `matched_locations`.
Unresolved projects remain in the output with null geometry.

State comes from project data. Public infrastructure is not filtered by operator.
Names are normalized without permissive fuzzy matching. Distant facilities with
identical names remain ambiguous. Internal engineering/tap identifiers and
numbered structures retain `requires_primary_source` even when a parent corridor
or related endpoint provides a useful project-level location.

`geometry` holds route geometry where available; `latitude` and `longitude` hold
screening coordinates. `point_location_method` distinguishes a published point,
facility-bounds center, endpoint midpoint, multi-facility center or route-bounds
center. These points are not invented transmission routes.

Source planning-document fields remain intact. Geographic provenance uses
`location_source_url`, `location_source_file`, `matched_locations` and
`route_evidence`. Confidence describes location evidence, not certainty about
construction limits. Saved route review flags remain visible.

## Outputs and preserved evidence

- `data/processed/dominion/dominion_projects.csv`: clean DESC projects.
- `data/processed/georgia_power/2025_irp/`: enriched ITS and GPC project tables.
- `data/processed/dominion/dominion_projects_geo.csv` and `.geojson`.
- `data/processed/georgia_power/georgia_power_projects_geo.csv` and `.geojson`.
- `data/processed/gridlock/overlap_candidates.csv`.
- `data/processed/dominion/reference/`: the endpoint registry and saved routes
  reused by the active geolocation notebook, plus their route GeoJSONs.
- `data/raw/`: original documents and public GIS/OSM caches.

See `data/README.md` for the purpose of each retained folder. Unused experimental
datasets, duplicate extractions, failed downloads and obsolete review tables have
been removed. The original Georgia ZIP and the source geometry behind saved
routes remain intact.

Notebook 03 currently screens within **40.2336 km (25 miles)**; notebook 04 displays candidates within **40 km**. These existing thresholds remain editable. Results
are candidate overlaps measured between project screening points, not proven
corridor intersections. Need dates and in-service dates retain their distinct
meanings; neither alone establishes simultaneous construction.

The geolocation notebook reuses caches. For a new state, set
`download_missing=True` when calling `load_infrastructure`. Missing sources are
reported rather than silently treated as a complete search.

## Small reusable modules

- `georgia_ingestion.py`: the existing PDF preparation/extraction steps.
- `asset_sources.py`: state-based cached public facility ingestion.
- `geolocation.py`: project matching, saved-route integration, exports and proximity.
- `osm_power_network.py`: OSM ingestion and voltage-specific network routing.
- `project_routes.py`: checked description assets, per-asset searches and route review.
- `public_lines.py`: the original DESC public-GIS download and routing fallback.
- `paths.py`: shared repository paths.

Run the offline checks:

```powershell
.\.venv\Scripts\python.exe tests/test_project_geolocation.py
.\.venv\Scripts\python.exe tests/test_project_routes.py
```

### Description-based route tracing

Notebook 03 uses `data/reference/geo/project_asset_scope.csv` before routing, as the original DESC workflow did. It contains 109 recovered DESC references and 222 GPC references checked against all 122 extracted descriptions. The full description is retained for each project. Work segments, separate circuits, station equipment, proposed assets, unresolved scope and parent-corridor context have separate rows. The title does not override explicit description work limits.

This is a readable checked input, not automatic interpretation of arbitrary prose. New or changed descriptions become `scope_review_needed` until their asset rows are checked; adding a utility/state does not require new Python logic. `scope_notes` explains interpretation, and `expected_length_miles` is populated only where a segment's mileage is stated. Chain totals are not assigned to each child segment.

The shared resolver preserves each saved DESC route, then searches each remaining asset using the state's facilities and OSM voltage network. Notebook 07's named public-GIS line lookup and endpoint-connected GIS fallback are also reused. Sources are cached under `data/raw/osm/<state>/` and `data/raw/public_gis/<state>/`; no operator filter applies.

OSM paths require shared real node IDs. GIS paths connect source endpoints rounded to three decimal places, so their topology remains a review assumption; original vertices are retained and gaps are not drawn as new lines. Unknown voltage is excluded. Proposed routes and internal engineering endpoints remain unresolved. A current 115 kV asset scheduled for conversion is searched at 115 kV, not its future voltage.

`data/processed/geo/project_route_review.csv` is the single review output. It preserves every referenced asset and all found geometries with evidence and failure reasons. New paths are candidates, not confirmed circuits or exact work footprints. Paths outside 50?150% of explicitly stated work mileage remain `route_extent_mismatch` in review and are excluded from project routes. This is a broad sanity check, not a scoring model or a guarantee that paths within the bounds are correct.

Coverage is reported per project as candidate evidence for all, some, or no work assets. Context rows do not count as work. Finding one segment never marks all of a project's assets located. Existing DESC source routes and original downloads remain intact.

### Route maps in notebooks 03 and 04

Both notebooks load `processed/geo/project_route_review.csv` through `route_maps.py`. DESC uses blue shades and GPC red shades. Dark solid lines use the original resolved routing status; lighter dashed lines need review. New GPC candidates remain needs review. Each utility/status has a separate layer. Length-mismatch paths and station geometries are excluded from route layers. The resolved shade does not certify exact project work limits. Notebook 04 saves `data/processed/gridlock/gridlock_map.html`. Folium is included in the notebook dependencies.

### Nearest-route proximity

Proximity now measures between the nearest positions on traced routes, falling back to the existing representative point for a project without a route. It considers segment interiors and all disconnected route parts, rather than using route centers or only vertices. Station geometry inside a route collection does not substitute for the route.

The approved Shapely and pyproj dependencies handle geometry and coordinate conversion. `proximity.py` uses a local azimuthal equidistant projection centered on the input region; distances are approximate regional screening measurements, not survey distances. Source route vertices remain unchanged.

Notebook 03 saves the nearest coordinates alongside each distance. Corridor-family grouping keeps the coordinates from the actual minimum-distance pair. Notebook 04 draws the proximity connector between those positions, rather than choosing the first project in a family. Existing route status shades and DESC duplicate-marker suppression remain in place.

Run `python tests/test_proximity.py` to check segment interiors, route crossings, disconnected parts and point fallback.

### Consistent notebook and dashboard maps

Notebook 03 draws its map after proximity grouping. Notebooks 03 and 04, plus the dashboard's selected-opportunity map, share `add_proximity_connections()` in `route_maps.py`. It requires saved nearest coordinates and the current distance method, so outdated center-distance tables cannot silently appear as nearest-route results. All three maps display the same 40 km screen.

The dashboard overview displays notebook 04's saved HTML. Its selected view loads the corresponding route assets, keeps the utility/status shades, and draws the actual closest connection for that family. It uses unique project keys for DESC instead of assuming project IDs are unique. CSVs reload on each dashboard rerun, and GPC traced-route coverage is included.

To run the dashboard: `python -m pip install -e ".[notebooks,dashboard]"`, then `python -m streamlit run app/dashboard.py`.

### Area planner

The dashboard's Area planner tab accepts coordinates, a five-digit US ZIP code, or a click on the map. Choose a radius and milestone-year range. It measures to the nearest route segment, with point fallback, using the same proximity code as the notebooks.

Results rank published milestone years by nearby project count, then utility count, nearest distance and earliest year. A project counts once per year. Adjacent years combine only when the same projects have milestones in each year. These are in-service or need milestones, **not estimated construction windows or evidence that crews will be present**. Missing dates remain visible; completed, cancelled and withdrawn projects do not contribute to ranking. Supporting projects retain the route review status and date evidence.

`area_planner.py` handles ZIP lookup, proximity and milestone ranking; `planner_tab.py` contains the dashboard controls and maps. The approved `streamlit-folium` connector returns map-click coordinates through `st_folium()`. ZIP lookup uses requests with Zippopotam.us / GeoNames and saves responses under `data/raw/geocoding/us_zip/`. ZIP positions are approximate postal place locations; refine them for an actual project site. User-entered coordinates are held in the dashboard session, not saved as project data.

Run `python tests/test_area_planner.py` for milestone ranking and nearest-route checks.
