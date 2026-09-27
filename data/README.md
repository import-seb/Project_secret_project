# Data used by the three active notebooks

## Inputs and original sources

| Folder | Why it stays |
|---|---|
| `raw/dominion/` | Original SCRTP project PDF for notebook 01. |
| `raw/georgia_power/2025_irp/` | Original ZIP, one extracted Volume 3 PDF, and six selected working PDFs for notebook 02. Other archive contents stay inside the ZIP. |
| `raw/osm/SC/` and `raw/osm/GA/` | State facility caches and query text. Both states now use statewide line/node caches for routing; SC also preserves source geometry behind saved routes. |
| `raw/public_gis/endpoint_points/` | SC and GA HIFLD station points used by notebook 03. |
| `raw/public_gis/SC/` and `raw/public_gis/GA/` | Cached DOE/HIFLD lines for the shared route fallback; original SC evidence remains intact. Census bounds and layer metadata in the parent folder document the queries. |
| `reference/geo/project_asset_scope.csv` | Checked description assets used by notebook 03, with source text, separate work segments, circuits, stages and scope notes. |

## Current outputs

| Folder | Contents |
|---|---|
| `processed/dominion/` | Notebook 01's projects, source review, page text and manifest; notebook 03's project geolocation CSV/GeoJSON. |
| `processed/georgia_power/2025_irp/` | Notebook 02's full ITS list and GPC-only list, including descriptions. |
| `processed/georgia_power/` | Notebook 03's GPC project geolocation CSV/GeoJSON. |
| `processed/geo/` | One asset review CSV: work/context distinctions, attempts, unresolved assets, OSM/GIS IDs and geometry. |
| `processed/gridlock/` | Cross-utility proximity candidates from notebook 03. |

## Saved discoveries reused by notebook 03

`processed/dominion/reference/` contains just five files:

- `dominion_endpoint_registry.csv`: named facility coordinates and evidence.
- `dominion_route_resolution_v3_tagged.csv`: saved route results and primary-source flags.
- `dominion_osm_low_voltage_routes.csv`: saved low-voltage route results.
- `dominion_resolved_existing_routes_v3.geojson`: original traced route geometry.
- `dominion_osm_low_voltage_routes.geojson`: original low-voltage route geometry.

These are inputs to preserve, not temporary output clutter. The tagged route CSV
includes the older untagged table's route data, so the duplicate table was removed.
Experimental Nominatim searches, heatmap downloads, verification exports and old
asset-review tables are no longer part of this data tree.
