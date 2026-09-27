from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from gridlock.paths import PROCESSED_DATA_DIR
from gridlock.route_maps import load_traced_routes, selected_connection_map
from gridlock.planner_tab import render_area_planner


st.set_page_config(
    page_title="GridLock",
    page_icon="⚡",
    layout="wide",
)

st.markdown("""
<style>
/* ===== Dark theme for Streamlit dataframes ===== */
div[data-testid="stDataFrame"] {
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 14px;
    overflow: hidden;
    background: #0D1620 !important;
}

/* Glide Data Grid theme vars */
div[data-testid="stDataFrame"] [data-testid="stDataFrameGlideDataEditor"] {
    --gdg-bg-cell: #0D1620;
    --gdg-bg-cell-medium: #101D29;
    --gdg-bg-header: #111C27;
    --gdg-bg-header-hovered: #152535;
    --gdg-bg-search-result: rgba(20,184,200,0.18);

    --gdg-text-dark: #EAF1F7;
    --gdg-text-medium: #C6D2DE;
    --gdg-text-light: #8FA1B3;

    --gdg-border-color: rgba(255,255,255,0.08);
    --gdg-horizontal-border-color: rgba(255,255,255,0.06);
    --gdg-vertical-border-color: rgba(255,255,255,0.06);

    --gdg-accent-color: #14B8C8;
    --gdg-accent-light: rgba(20,184,200,0.18);

    --gdg-font-family: "Inter", sans-serif;
}

/* Make sure text stays light */
div[data-testid="stDataFrame"] * {
    color: #EAF1F7 !important;
}

/* Header row */
div[data-testid="stDataFrame"] [role="columnheader"] {
    background-color: #111C27 !important;
    color: #C6D2DE !important;
    font-weight: 600 !important;
}

/* Body cells */
div[data-testid="stDataFrame"] [role="gridcell"] {
    background-color: #0D1620 !important;
    color: #EAF1F7 !important;
}

/* Hover feel */
div[data-testid="stDataFrame"] [role="gridcell"]:hover,
div[data-testid="stDataFrame"] [role="columnheader"]:hover {
    background-color: #132231 !important;
}

/* Optional: st.table support too */
div[data-testid="stTable"] table {
    background: #0D1620 !important;
    color: #EAF1F7 !important;
    border-collapse: collapse;
}

div[data-testid="stTable"] th {
    background: #111C27 !important;
    color: #C6D2DE !important;
}

div[data-testid="stTable"] td {
    background: #0D1620 !important;
    color: #EAF1F7 !important;
}

div[data-testid="stTable"] th,
div[data-testid="stTable"] td {
    border: 1px solid rgba(255,255,255,0.08) !important;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
    <style>
        /* Streamlit top header */
        [data-testid="stHeader"] {
            background: #0D1620 !important;
        }

        header[data-testid="stHeader"] {
            background: #0D1620 !important;
        }

        /* Keep toolbar visible but dark */
        [data-testid="stToolbar"] {
            background: #0D1620 !important;
        }

        /* Optional: remove extra top spacing */
        .block-container {
            padding-top: 1rem;
        }
    </style>
    """, unsafe_allow_html=True)

st.markdown("""
<style>
:root {
    --bg: #080c10;
    --panel: #0d1620;
    --panel-2: #101d29;
    --text: #f4f6f8;
    --muted: #AEB8C4;
    --cyan: #14b8c8;
    --blue: #2f7fe5;
    --purple: #8b4fd8;
    --pink: #d44a91;
    --border: rgba(255, 255, 255, 0.08);
}

.stApp {
    background:
        radial-gradient(circle at 65% 30%, rgba(20, 184, 200, 0.14), transparent 32%),
        radial-gradient(circle at 90% 72%, rgba(139, 79, 216, 0.13), transparent 34%),
        linear-gradient(135deg, #08101a 0%, #071018 45%, #0b0b12 100%);
    color: var(--text);
}

.block-container {
    padding-top: 2rem;
    max-width: 1500px;
}

/* Hero */
.gridlock-kicker {
    display: inline-flex;
    align-items: center;
    gap: 0.55rem;
    padding: 0.42rem 0.8rem;
    margin-bottom: 0.85rem;
    border: 1px solid var(--border);
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.025);
    color: #aeb8c2;
    font-size: 0.78rem;
    font-weight: 600;
    letter-spacing: 0.06em;
    text-transform: uppercase;
}

.gridlock-kicker-dot {
    width: 0.48rem;
    height: 0.48rem;
    border-radius: 50%;
    background: var(--pink);
    box-shadow: 0 0 16px rgba(212, 74, 145, 0.5);
}

.gridlock-hero {
    margin: 0;
    font-size: clamp(3.6rem, 8vw, 7.4rem);
    line-height: 0.95;
    letter-spacing: -0.065em;
    font-weight: 800;
    max-width: 1100px;
}

.gridlock-hero-white {
    color: #f4f6f8;
}

.gridlock-hero-gradient {
    background: linear-gradient(90deg, var(--cyan) 0%, var(--blue) 46%, var(--purple) 82%, var(--pink) 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    color: transparent;
}

.gridlock-subtitle {
    max-width: 760px;
    margin: 1.4rem 0 2rem 0;
    color: var(--muted);
    font-size: 1.08rem;
    line-height: 1.65;
}

h1, h2, h3 {
    color: var(--text) !important;
    font-weight: 700 !important;
}

[data-testid="stCaptionContainer"],
.stCaption,
small {
    color: var(--muted) !important;
}

[data-testid="stMetric"] {
    background: linear-gradient(145deg, rgba(16, 29, 41, 0.92), rgba(10, 20, 29, 0.92));
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 18px 20px;
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.20), inset 0 1px 0 rgba(255,255,255,0.03);
}

[data-testid="stMetricLabel"] { color: var(--muted); }
[data-testid="stMetricValue"] { color: var(--text); }

button[data-baseweb="tab"] {
    color: var(--muted) !important;
    font-weight: 600;
}

button[data-baseweb="tab"][aria-selected="true"] {
    color: var(--text) !important;
}

div[data-baseweb="tab-highlight"] {
    background: linear-gradient(90deg, var(--cyan), var(--blue), var(--purple)) !important;
}

[data-testid="stAlert"] {
    background: rgba(16, 29, 41, 0.8);
    border: 1px solid rgba(20, 184, 200, 0.25);
    border-radius: 12px;
    color: var(--text);
}

[data-testid="stCheckbox"] label { color: var(--text) !important; }

[data-testid="stDataFrame"] {
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
}

hr { border-color: var(--border) !important; }

::-webkit-scrollbar { width: 10px; }
::-webkit-scrollbar-track { background: #080c10; }
::-webkit-scrollbar-thumb { background: #263747; border-radius: 10px; }
::-webkit-scrollbar-thumb:hover { background: var(--cyan); }

[data-testid="stDecoration"] {
    background: linear-gradient(90deg, var(--blue), var(--cyan), var(--purple), var(--pink));
}
</style>
""", unsafe_allow_html=True)


DESC_GEO_PATH = PROCESSED_DATA_DIR / "dominion" / "dominion_projects_geo.csv"
GPC_GEO_PATH = PROCESSED_DATA_DIR / "georgia_power" / "georgia_power_projects_geo.csv"
OVERLAP_PATH = PROCESSED_DATA_DIR / "gridlock" / "overlap_candidates.csv"
MAP_PATH = PROCESSED_DATA_DIR / "gridlock" / "gridlock_map.html"


def load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        st.error(f"Missing required file: {path}")
        st.stop()
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def load_dashboard_data():
    desc = load_csv(DESC_GEO_PATH)
    gpc = load_csv(GPC_GEO_PATH)
    overlaps = load_csv(OVERLAP_PATH)

    for table in (desc, gpc):
        table["latitude"] = pd.to_numeric(table.get("latitude"), errors="coerce")
        table["longitude"] = pd.to_numeric(table.get("longitude"), errors="coerce")

    if "distance_km" in overlaps.columns:
        overlaps["distance_km"] = pd.to_numeric(overlaps["distance_km"], errors="coerce")

    return desc, gpc, overlaps

def render_selected_connection(row):
    m = selected_connection_map(row, df_desc, df_gpc, df_routes)
    components.html(m.get_root().render(), height=560, scrolling=False)


def count_located(df: pd.DataFrame) -> int:
    return int(df[["latitude", "longitude"]].notna().all(axis=1).sum())



def count_traced_routes(df: pd.DataFrame) -> int:
    if "location_method" not in df.columns:
        return 0
    return int(df["location_method"].isin(["saved_traced_route", "traced_route_candidate"]).sum())



def yes_count(series: pd.Series) -> int:
    values = series.astype(str).str.strip().str.lower()
    return int(values.isin({"true", "1", "yes"}).sum())



def render_map():
    if not MAP_PATH.exists():
        st.warning(
            "The saved GridLock map does not exist yet. "
            "Run 04_gridlock_map.ipynb first to create gridlock_map.html."
        )
        return

    map_html = MAP_PATH.read_text(encoding="utf-8")
    components.html(map_html, height=560, scrolling=False)



df_desc, df_gpc, df_overlaps = load_dashboard_data()
df_routes = load_traced_routes()
# Match the threshold displayed by both notebook maps.
df_overlaps = df_overlaps[df_overlaps["distance_km"] <= 40].copy()
required = {"desc_nearest_latitude", "desc_nearest_longitude", "gpc_nearest_latitude",
            "gpc_nearest_longitude", "gpc_closest_project_key", "distance_method"}
if not required.issubset(df_overlaps.columns) or not df_overlaps["distance_method"].eq("nearest_route_or_point_local_meters").all():
    st.error("Regenerate nearest-route proximity results by running notebooks 03 and 04.")
    st.stop()

desc_located = count_located(df_desc)
gpc_located = count_located(df_gpc)
traced_routes = count_traced_routes(df_desc)
gpc_traced_routes = count_traced_routes(df_gpc)
review_count = (
    yes_count(df_overlaps["needs_manual_review"])
    if "needs_manual_review" in df_overlaps.columns
    else 0
)


st.markdown("""
<div class="gridlock-kicker">
    <span class="gridlock-kicker-dot"></span>
    Cross-utility transmission intelligence
</div>

<h1 class="gridlock-hero">
    <span class="gridlock-hero-white">See the grid.</span><br>
    <span class="gridlock-hero-gradient">Before it collides.</span>
</h1>

<p class="gridlock-subtitle">
    GridLock surfaces where DESC and Georgia Power projects may converge,
    helping teams screen future transmission conflicts before they become expensive.
</p>
""", unsafe_allow_html=True)

m1, m2, m3, m4 = st.columns(4)
m1.metric("DESC projects located", f"{desc_located}")
m2.metric("GPC projects located", f"{gpc_located}")
m3.metric("Proximity candidates", len(df_overlaps))
m4.metric("Projects with traced routes", traced_routes + gpc_traced_routes)

st.info(
    "Proximity uses the nearest parts of traced routes, with point locations where no route is available. "
    "They are screening leads, not confirmed shared-corridor overlaps."
)

map_tab, candidate_tab, coverage_tab, planner_tab = st.tabs(
    ["Map", "Proximity candidates", "Coverage", "Area planner"]
)

with map_tab:
    # Build ranking first
    ranked = (
        df_overlaps
        .dropna(subset=["distance_km"])
        .sort_values("distance_km")
        .head(10)
        .copy()
    )

    ranked.insert(0, "rank", range(1, len(ranked) + 1))

    display_ranked = ranked[
        [
            "rank",
            "desc_project_name",
            "gpc_project_names",
            "distance_km",
        ]
    ].copy()

    display_ranked["distance_km"] = display_ranked["distance_km"].round(2)

    display_ranked.columns = [
        "#",
        "DESC project",
        "GPC project",
        "Distance (km)",
    ]

    map_col, rank_col = st.columns([2, 1])

    # RIGHT SIDE FIRST so we know what is selected
    with rank_col:
        st.subheader("Top coordination opportunities")
        st.caption("Click an opportunity to focus the map.")

        event = st.dataframe(
            display_ranked,
            use_container_width=True,
            hide_index=True,
            height=500,
            on_select="rerun",
            selection_mode="single-row",
            key="opportunity_table",
        )

    selected_rows = event.selection.rows

    # LEFT SIDE: exactly ONE map
    with map_col:
        st.subheader("Project map")

        if selected_rows:
            selected_row = ranked.iloc[selected_rows[0]]
            render_selected_connection(selected_row)
        else:
            st.caption(
                "Blue = DESC projects, red = GPC projects, "
                "dark solid routes = resolved; lighter dashed routes = needs review; "
                "purple dashed links connect the nearest route/point positions."
            )
            render_map()


with candidate_tab:
    st.subheader("Cross-utility proximity candidates")

    if df_overlaps.empty:
        st.success("No cross-utility projects fall inside the current proximity threshold.")
    else:
        show_review_only = st.checkbox(
            "Show review-required candidates only",
            value=False,
        )

        candidates = df_overlaps.copy()
        if show_review_only and "needs_manual_review" in candidates.columns:
            review_values = candidates["needs_manual_review"].astype(str).str.lower()
            candidates = candidates[review_values.isin({"true", "1", "yes"})]

        preferred_columns = [
            "desc_project_id",
            "desc_project_name",
            "gpc_project_ids",
            "gpc_project_names",
            "distance_km",
            "desc_in_service_date",
            "gpc_need_date",
            "desc_location_method",
            "gpc_location_method",
            "desc_confidence",
            "gpc_confidence",
            "needs_manual_review",
            "timing_note",
        ]
        visible_columns = [c for c in preferred_columns if c in candidates.columns]

        display_df = candidates[visible_columns].copy()
        if "distance_km" in display_df.columns:
            display_df = display_df.sort_values("distance_km")
            display_df["distance_km"] = display_df["distance_km"].round(2)

        st.table(
            display_df,
        )

        st.caption(
            f"{len(candidates)} candidates shown. "
            f"{review_count} of {len(df_overlaps)} total candidates are flagged for review."
        )

with coverage_tab:
    st.subheader("Geolocation coverage")

    coverage = pd.DataFrame(
        [
            {
                "utility": "DESC",
                "projects": len(df_desc),
                "located": desc_located,
                "unlocated": len(df_desc) - desc_located,
                "coverage_pct": round(100 * desc_located / len(df_desc), 1),
                "traced_routes": traced_routes,
            },
            {
                "utility": "GPC",
                "projects": len(df_gpc),
                "located": gpc_located,
                "unlocated": len(df_gpc) - gpc_located,
                "coverage_pct": round(100 * gpc_located / len(df_gpc), 1),
                "traced_routes": gpc_traced_routes,
            },
        ]
    )

    st.table(coverage)

    st.markdown("**Location methods**")

    left, right = st.columns(2)

    with left:
        st.caption("DESC")
        desc_methods = (
            df_desc["location_method"].replace("", "unresolved").value_counts().rename_axis("method").reset_index(name="count")
        )
        st.table(desc_methods)

    with right:
        st.caption("GPC")
        gpc_methods = (
            df_gpc["location_method"].replace("", "unresolved").value_counts().rename_axis("method").reset_index(name="count")
        )
        st.table(gpc_methods)

with planner_tab:
    render_area_planner(pd.concat([df_desc, df_gpc], ignore_index=True), df_routes)

st.divider()
st.caption(
    "GridLock MVP • Source project data is processed by notebooks 01–03; "
    "the interactive map is generated by notebook 04."
)
