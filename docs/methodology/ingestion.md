# GridLock Ingestion Blueprint

## Goal

Take future power-grid construction plans from different utilities and turn them into one clean dataset that GridLock can use.

Think of ingestion as:

**Find the data → save the original → pull out the useful fields → clean everything into one format → save the clean version**

---

## Step 1 — Find the official source

For each utility, find where it publishes future transmission projects.

Examples:

- Dominion Energy South Carolina → SCRTP project documents
- Georgia Power → transmission project pages / planning documents
- HIFLD → existing transmission lines and substations

It is fine if one source is a PDF and another is a website.

---

## Step 2 — Save the original data first

Before cleaning anything, save exactly what you downloaded.

```text
data/
    raw/
        dominion/
            planned_projects.pdf

        georgia_power/
            transmission_projects.html

        hifld/
            transmission_lines.geojson
```

Never edit the raw files.

Why? If your code breaks later, you still have the original source.

---

## Step 3 — Pull out the useful project information

For every project, try to collect:

```text
utility
project_name
project_id
description
project_type
status
start_date
end_date
in_service_date
estimated_cost
location
source_url
```

Some sources will not have every field. That is okay.

Example:

```text
utility: Dominion Energy South Carolina
project_name: Okatie-Sherwood 230 kV
status: Planned
in_service_date: 2029
estimated_cost: 42000000
location: Okatie, SC
```

---

## Step 4 — Make every utility use the same column names

This is one of the most important steps.

Georgia Power might call something:

```text
Target Completion
```

Dominion might call it:

```text
Planned In-Service Date
```

GridLock should not care.

Both become:

```text
in_service_date
```

Eventually every utility should look like:

| utility | project_name | status | start_date | in_service_date | cost |
|---|---|---|---|---|---|
| Dominion | Project A | Planned | 2028 | 2029 | 40M |
| Georgia Power | Project B | Planned | 2028 | 2030 | 55M |

---

## Step 5 — Clean obvious differences

Examples:

```text
"$45 million"  → 45000000
"230KV"        → 230
"GA"           → Georgia
```

The goal is not perfection. The goal is consistency.

---

## Step 6 — Figure out where each project is

Projects need locations so GridLock can compare distance.

Best sources, from strongest to weakest:

1. Official project map or coordinates
2. Known transmission line / substation locations
3. Project endpoints
4. City or county name
5. Rough estimate

Also save how you found the location.

Example:

```text
location_method: official_map
```

or

```text
location_method: city_estimate
```

That way, GridLock knows which locations are exact and which are rough.

---

## Step 7 — Turn locations into map data

A substation can be a point:

```text
latitude
longitude
```

A transmission line can be a line made of several coordinates:

```text
(-81.1, 32.3)
(-81.2, 32.4)
(-81.4, 32.5)
```

This is the information the map and distance calculator will use.

---

## Step 8 — Save the cleaned project data

Keep clean data separate from raw data.

```text
data/
    raw/

    processed/
        projects.csv
        projects.geojson
```

For the hackathon, CSV + GeoJSON is enough.

Later, if GridLock grows, you can move the same data into PostgreSQL/PostGIS.

---

## Step 9 — Compare projects

Now GridLock does its main job.

For every Dominion project, compare it with nearby Georgia Power projects.

Ask:

```text
Are they within 40 km?

If yes:
    How close are they?

Do their construction dates overlap?
```

Distance groups:

```text
Touching / crossing
Under 1.6 km
Under 8 km
Under 40 km
Over 40 km → ignore
```

---

## Step 10 — Save the matches

Create a separate table for GridLock's findings.

```text
project_a
project_b
distance_km
distance_group
timeline_overlap
```

Example:

| project_a | project_b | distance_km | timeline_overlap |
|---|---|---:|---|
| Dominion A | Georgia B | 3.8 | Yes |
| Dominion C | Georgia D | 17.2 | No |

This table powers the ranked coordination list in the app.

---

# Simple Folder Structure

```text
gridlock/

    data/
        raw/
            dominion/
            georgia_power/
            hifld/

        processed/
            projects.csv
            projects.geojson
            overlaps.csv

    src/
        ingestion/
            dominion.py
            georgia_power.py
            hifld.py

        cleaning/
            clean_projects.py

        mapping/
            resolve_locations.py

        matching/
            find_overlaps.py

    app/
        dashboard.py
```

---

# What Each File Does

## `dominion.py`

Gets Dominion's future projects and turns them into project records.

## `georgia_power.py`

Gets Georgia Power's future projects and turns them into project records.

## `hifld.py`

Gets existing transmission lines and substations.

Use it for:

- map background
- helping locate projects
- matching known substations and lines

## `clean_projects.py`

Makes Dominion and Georgia Power use the same column names and formats.

## `resolve_locations.py`

Figures out where the projects belong on the map.

## `find_overlaps.py`

Calculates:

```text
distance
distance category
timeline overlap
```

---

# The Whole Pipeline

```text
Dominion source
      ↓
 dominion.py
      ↓

Georgia Power source
      ↓
georgia_power.py
      ↓

HIFLD
      ↓
  hifld.py
      ↓

   ALL PROJECTS
        ↓
clean_projects.py
        ↓
  CLEAN PROJECTS
        ↓
resolve_locations.py
        ↓
PROJECTS ON A MAP
        ↓
 find_overlaps.py
        ↓
COORDINATION MATCHES
        ↓
    GRIDLOCK APP
```

---

# Build It in This Order

### Phase 1

Get Dominion projects into a DataFrame.

```python
df_dominion
```

### Phase 2

Get Georgia Power projects into a DataFrame.

```python
df_georgia
```

### Phase 3

Make both DataFrames use the same columns.

### Phase 4

Combine them.

```python
projects = pd.concat([
    df_dominion,
    df_georgia
])
```

### Phase 5

Add locations.

### Phase 6

Calculate distances.

### Phase 7

Compare construction timelines.

### Phase 8

Build the map and ranked coordination list.

---

# Most Important Rule

Do not build GridLock around Dominion's PDF or Georgia Power's website.

Instead:

```text
Dominion's weird format
        ↓
      clean
        ↓

Georgia's weird format
        ↓
      clean
        ↓

SAME PROJECT FORMAT
        ↓
      GridLock
```

If you later add Duke Energy, FPL, or another utility, you only need to teach the ingestion code how to read that new source.

The rest of GridLock stays the same.
