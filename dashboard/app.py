"""
NYC 311 Analytics Dashboard — Streamlit + Plotly
Reads directly from ClickHouse aggregated tables.

Run:
    uv run streamlit run dashboard/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config.settings import settings
from src.utils.clickhouse_client import get_client, ping

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="NYC 311 Analytics",
    page_icon="🗽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'IBM Plex Sans', sans-serif;
    }
    .main { background-color: #0d1117; }
    .stApp { background-color: #0d1117; }

    .metric-card {
        background: linear-gradient(135deg, #1c2333 0%, #161b27 100%);
        border: 1px solid #30363d;
        border-radius: 8px;
        padding: 20px 24px;
        margin-bottom: 12px;
    }
    .metric-value {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 2rem;
        font-weight: 600;
        color: #58a6ff;
        line-height: 1;
    }
    .metric-label {
        font-size: 0.75rem;
        color: #8b949e;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        margin-top: 6px;
    }
    .section-header {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.7rem;
        color: #f78166;
        text-transform: uppercase;
        letter-spacing: 0.15em;
        border-bottom: 1px solid #30363d;
        padding-bottom: 8px;
        margin-bottom: 16px;
    }
    h1 { color: #e6edf3 !important; font-weight: 300 !important; }
    h2, h3 { color: #c9d1d9 !important; }
    .stSelectbox label, .stMultiSelect label, .stSlider label {
        color: #8b949e !important;
        font-size: 0.8rem !important;
    }
    .stSidebar { background-color: #161b22 !important; }
    .stSidebar [data-testid="stSidebarNav"] { background-color: #161b22; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# ClickHouse data loaders (cached)
# ---------------------------------------------------------------------------

DB = settings.clickhouse.database

@st.cache_data(ttl=300, show_spinner=False)
def load_agency_performance() -> pd.DataFrame:
    client = get_client()
    result = client.query(f"""
        SELECT agency, agency_name, year_month,
               total_requests, resolved_count,
               avg_resolution_hrs, median_resolution_hrs, resolution_rate
        FROM {DB}.agg_agency_performance
        ORDER BY year_month, total_requests DESC
    """)
    return pd.DataFrame(result.named_results())


@st.cache_data(ttl=300, show_spinner=False)
def load_borough_complaints() -> pd.DataFrame:
    client = get_client()
    result = client.query(f"""
        SELECT borough, complaint_category, complaint_type,
               year_month, request_count, pct_of_borough
        FROM {DB}.agg_borough_complaints
        ORDER BY borough, request_count DESC
    """)
    return pd.DataFrame(result.named_results())


@st.cache_data(ttl=300, show_spinner=False)
def load_monthly_trend() -> pd.DataFrame:
    client = get_client()
    result = client.query(f"""
        SELECT year_month, borough, complaint_category,
               request_count, resolved_count, avg_resolution_hrs
        FROM {DB}.agg_monthly_trend
        ORDER BY year_month
    """)
    return pd.DataFrame(result.named_results())


@st.cache_data(ttl=300, show_spinner=False)
def load_summary_stats() -> dict:
    client = get_client()
    r = client.query(f"""
        SELECT
            count()                          AS total_requests,
            countIf(is_resolved = 1)         AS resolved,
            round(avg(resolution_hours), 1)  AS avg_hrs,
            uniqExact(agency)                AS agencies,
            uniqExact(borough)               AS boroughs,
            min(created_date)                AS earliest,
            max(created_date)                AS latest
        FROM {DB}.requests_clean
    """)
    row = r.named_results()[0]
    return dict(row)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## 🗽 NYC 311")
    st.markdown("<div class='section-header'>Filters</div>", unsafe_allow_html=True)

    # Connection status
    if ping():
        st.success("ClickHouse ✓ Connected", icon="🟢")
    else:
        st.error("ClickHouse unreachable", icon="🔴")
        st.stop()

    # Load filter options
    trend_df_full = load_monthly_trend()
    all_boroughs = sorted(trend_df_full["borough"].unique().tolist())
    all_categories = sorted(trend_df_full["complaint_category"].unique().tolist())
    all_months = sorted(trend_df_full["year_month"].unique().tolist())

    selected_boroughs = st.multiselect(
        "Borough", all_boroughs, default=all_boroughs[:5]
    )
    selected_categories = st.multiselect(
        "Complaint Category", all_categories, default=all_categories
    )
    if len(all_months) >= 2:
        month_range = st.select_slider(
            "Date Range",
            options=all_months,
            value=(all_months[0], all_months[-1]),
        )
    else:
        month_range = (all_months[0], all_months[-1]) if all_months else ("", "")

    st.markdown("---")
    st.markdown("<div class='section-header'>About</div>", unsafe_allow_html=True)
    st.caption("NYC 311 Service Requests\nSource: NYC Open Data\nPlatform: ClickHouse")


# ---------------------------------------------------------------------------
# Header + KPI cards
# ---------------------------------------------------------------------------

st.markdown("# NYC 311 Service Request Analytics")
st.markdown("*Real-time analytics pipeline — ClickHouse · Streamlit · Plotly*")
st.markdown("---")

try:
    stats = load_summary_stats()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""<div class='metric-card'>
            <div class='metric-value'>{int(stats.get('total_requests', 0)):,}</div>
            <div class='metric-label'>Total Requests</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        rate = int(stats.get('resolved', 0)) / max(int(stats.get('total_requests', 1)), 1)
        st.markdown(f"""<div class='metric-card'>
            <div class='metric-value'>{rate:.1%}</div>
            <div class='metric-label'>Resolution Rate</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""<div class='metric-card'>
            <div class='metric-value'>{stats.get('avg_hrs', 'N/A')}</div>
            <div class='metric-label'>Avg Resolution (hrs)</div>
        </div>""", unsafe_allow_html=True)
    with c4:
        st.markdown(f"""<div class='metric-card'>
            <div class='metric-value'>{int(stats.get('agencies', 0))}</div>
            <div class='metric-label'>Active Agencies</div>
        </div>""", unsafe_allow_html=True)
except Exception as e:
    st.warning(f"Could not load summary stats: {e}")

st.markdown("---")

# ---------------------------------------------------------------------------
# VIZ 1: Monthly Volume Trend (Line Chart)
# Business Q: Is 311 usage increasing? Which categories are driving spikes?
# ---------------------------------------------------------------------------

st.markdown("<div class='section-header'>📈 Monthly Request Volume Trend</div>", unsafe_allow_html=True)
st.markdown("**Business Question:** Is 311 call volume increasing over time, and which complaint categories are driving the spikes?")

trend_df = load_monthly_trend()

# Apply filters
mask = (
    trend_df["borough"].isin(selected_boroughs) &
    trend_df["complaint_category"].isin(selected_categories) &
    trend_df["year_month"].between(month_range[0], month_range[1])
)
filtered_trend = trend_df[mask]

if not filtered_trend.empty:
    trend_grouped = (
        filtered_trend
        .groupby(["year_month", "complaint_category"])["request_count"]
        .sum()
        .reset_index()
    )

    fig1 = px.line(
        trend_grouped,
        x="year_month",
        y="request_count",
        color="complaint_category",
        markers=True,
        color_discrete_sequence=px.colors.qualitative.Bold,
        labels={"year_month": "Month", "request_count": "Requests", "complaint_category": "Category"},
        title="Monthly 311 Request Volume by Complaint Category",
    )
    fig1.update_layout(
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font_color="#c9d1d9",
        title_font_color="#e6edf3",
        legend_bgcolor="#161b22",
        legend_bordercolor="#30363d",
        xaxis=dict(gridcolor="#21262d", tickangle=-45),
        yaxis=dict(gridcolor="#21262d"),
        hovermode="x unified",
    )
    st.plotly_chart(fig1, use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        total = filtered_trend["request_count"].sum()
        st.metric("Total in selection", f"{total:,}")
    with col_b:
        top_cat = trend_grouped.groupby("complaint_category")["request_count"].sum().idxmax()
        st.metric("Top category", top_cat)
else:
    st.info("No data for selected filters.")

st.markdown("---")

# ---------------------------------------------------------------------------
# VIZ 2: Borough Complaint Heatmap
# Business Q: Which complaint categories dominate each borough?
# ---------------------------------------------------------------------------

st.markdown("<div class='section-header'>🗺️ Borough Complaint Heatmap</div>", unsafe_allow_html=True)
st.markdown("**Business Question:** Which complaint categories are most prevalent in each borough, and where should the city allocate resources?")

borough_df = load_borough_complaints()

# Pivot for heatmap
borough_mask = (
    borough_df["borough"].isin(selected_boroughs) &
    borough_df["complaint_category"].isin(selected_categories) &
    borough_df["year_month"].between(month_range[0], month_range[1])
)
filtered_borough = borough_df[borough_mask]

if not filtered_borough.empty:
    pivot = (
        filtered_borough
        .groupby(["borough", "complaint_category"])["request_count"]
        .sum()
        .unstack(fill_value=0)
    )

    fig2 = px.imshow(
        pivot,
        color_continuous_scale="Blues",
        aspect="auto",
        labels=dict(x="Complaint Category", y="Borough", color="Requests"),
        title="Complaint Volume Heatmap by Borough and Category",
        text_auto=True,
    )
    fig2.update_layout(
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font_color="#c9d1d9",
        title_font_color="#e6edf3",
        xaxis=dict(tickangle=-30),
    )
    st.plotly_chart(fig2, use_container_width=True)

    # Bar breakdown — top complaint types per selected borough
    st.markdown("**Top 10 Complaint Types (detailed)**")
    selected_b = st.selectbox(
        "Drill into borough:", options=sorted(filtered_borough["borough"].unique())
    )
    top10 = (
        filtered_borough[filtered_borough["borough"] == selected_b]
        .groupby("complaint_type")["request_count"]
        .sum()
        .nlargest(10)
        .reset_index()
    )
    fig2b = px.bar(
        top10,
        x="request_count",
        y="complaint_type",
        orientation="h",
        color="request_count",
        color_continuous_scale="Blues",
        labels={"request_count": "Requests", "complaint_type": "Complaint Type"},
        title=f"Top 10 Complaints — {selected_b}",
    )
    fig2b.update_layout(
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font_color="#c9d1d9",
        title_font_color="#e6edf3",
        yaxis=dict(autorange="reversed"),
        showlegend=False,
    )
    st.plotly_chart(fig2b, use_container_width=True)
else:
    st.info("No data for selected filters.")

st.markdown("---")

# ---------------------------------------------------------------------------
# VIZ 3: Agency Performance — Avg Resolution Time
# Business Q: Which agencies take longest to close tickets? Is it improving?
# ---------------------------------------------------------------------------

st.markdown("<div class='section-header'>⏱️ Agency Resolution Performance</div>", unsafe_allow_html=True)
st.markdown("**Business Question:** Which NYC agencies take the longest to resolve 311 requests, and are they improving over time?")

agency_df = load_agency_performance()

if not agency_df.empty:
    agency_df["avg_resolution_hrs"] = pd.to_numeric(agency_df["avg_resolution_hrs"], errors="coerce")
    agency_df["total_requests"] = pd.to_numeric(agency_df["total_requests"], errors="coerce")

    # Apply month filter
    agency_filtered = agency_df[
        agency_df["year_month"].between(month_range[0], month_range[1])
    ]

    # Top N slider
    top_n = st.slider("Show top N agencies by volume", min_value=5, max_value=30, value=15)

    top_agencies = (
        agency_filtered
        .groupby(["agency", "agency_name"])["total_requests"]
        .sum()
        .nlargest(top_n)
        .index.get_level_values("agency")
        .tolist()
    )

    summary = (
        agency_filtered[agency_filtered["agency"].isin(top_agencies)]
        .groupby(["agency", "agency_name"])
        .agg(
            total_requests=("total_requests", "sum"),
            avg_resolution_hrs=("avg_resolution_hrs", "mean"),
            resolution_rate=("resolution_rate", "mean"),
        )
        .reset_index()
        .sort_values("avg_resolution_hrs", ascending=False)
    )

    fig3 = px.scatter(
        summary,
        x="avg_resolution_hrs",
        y="resolution_rate",
        size="total_requests",
        color="avg_resolution_hrs",
        color_continuous_scale="RdYlGn_r",
        hover_name="agency_name",
        hover_data={"agency": True, "total_requests": ":,", "avg_resolution_hrs": ":.1f"},
        labels={
            "avg_resolution_hrs": "Avg Resolution (hrs)",
            "resolution_rate": "Resolution Rate",
            "total_requests": "Total Requests",
        },
        title="Agency Performance: Resolution Time vs. Rate (bubble = volume)",
    )
    fig3.update_layout(
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font_color="#c9d1d9",
        title_font_color="#e6edf3",
        xaxis=dict(gridcolor="#21262d"),
        yaxis=dict(gridcolor="#21262d", tickformat=".0%"),
        coloraxis_showscale=False,
    )
    # Add agency labels for top agencies
    for _, row in summary.head(8).iterrows():
        fig3.add_annotation(
            x=row["avg_resolution_hrs"],
            y=row["resolution_rate"],
            text=row["agency"],
            showarrow=False,
            font=dict(size=9, color="#8b949e"),
            yshift=12,
        )
    st.plotly_chart(fig3, use_container_width=True)

    # Trend line for selected agency
    st.markdown("**Resolution Time Trend for a Specific Agency**")
    agency_choice = st.selectbox(
        "Select agency:",
        options=sorted(agency_filtered["agency_name"].unique()),
    )
    agency_trend = agency_filtered[agency_filtered["agency_name"] == agency_choice].sort_values("year_month")

    fig3b = go.Figure()
    fig3b.add_trace(go.Scatter(
        x=agency_trend["year_month"],
        y=agency_trend["avg_resolution_hrs"],
        mode="lines+markers",
        name="Avg Resolution (hrs)",
        line=dict(color="#58a6ff", width=2),
        marker=dict(size=6),
    ))
    fig3b.add_trace(go.Scatter(
        x=agency_trend["year_month"],
        y=agency_trend["median_resolution_hrs"],
        mode="lines+markers",
        name="Median Resolution (hrs)",
        line=dict(color="#3fb950", width=2, dash="dot"),
        marker=dict(size=6),
    ))
    fig3b.update_layout(
        title=f"Resolution Time Over Time — {agency_choice}",
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font_color="#c9d1d9",
        title_font_color="#e6edf3",
        legend_bgcolor="#161b22",
        xaxis=dict(gridcolor="#21262d", tickangle=-45),
        yaxis=dict(gridcolor="#21262d", title="Hours"),
        hovermode="x unified",
    )
    st.plotly_chart(fig3b, use_container_width=True)

else:
    st.info("No agency performance data available. Run the aggregation pipeline first.")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("---")
st.markdown(
    "<div style='text-align:center;color:#484f58;font-size:0.75rem;font-family:IBM Plex Mono,monospace'>"
    "NYC 311 Analytics · ClickHouse · Built with Streamlit · Data: NYC Open Data"
    "</div>",
    unsafe_allow_html=True,
)
