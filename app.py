import pandas as pd
import streamlit as st

st.set_page_config(page_title="Bluebikes Live Analysis", page_icon="🚲", layout="wide")
TZ = "America/New_York"

ACCENT = "#4FC3F7"
st.markdown(
    """
    <style>
    #MainMenu, footer {visibility: hidden;}
    .block-container {padding-top: 2rem; max-width: 1200px;}
    h1 {font-weight: 600; letter-spacing: -0.5px;}
    h2, h3 {font-weight: 500;}
    div[data-testid="stMetric"] {
        background: #12304f; border: 1px solid #1d4468;
        border-radius: 12px; padding: 14px 16px;
    }
    div[data-testid="stMetricLabel"] {opacity: 0.75;}
    button[data-baseweb="tab"] {font-size: 0.95rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

conn = st.connection("supabase", type="sql")


@st.cache_data(ttl=600, show_spinner=False)
def q(sql: str, **params) -> pd.DataFrame:
    return conn.query(sql, params=params or None, ttl=600)


# ---------- helpers ----------
@st.cache_data(ttl=3600, show_spinner=False)
def station_columns() -> dict:
    cols = q("select column_name from information_schema.columns where table_name = 'stations'")
    names = set(cols.column_name.str.lower())
    pick = lambda opts: next((o for o in opts if o in names), None)
    return {
        "name": pick(["name", "station_name"]),
        "lat": pick(["lat", "latitude"]),
        "lon": pick(["lon", "lng", "longitude"]),
        "capacity": pick(["capacity"]),
    }


SC = station_columns()
name_expr = f"s.{SC['name']}" if SC["name"] else "d.station_id"

# ---------- header ----------
st.title("🚲 Bluebikes Live Analysis")
st.caption("Station availability collected from the public Bluebikes GBFS feed.")

meta = q("""
    select count(*) as total_rows,
           count(distinct fetched_at) as snapshots,
           count(distinct station_id) as stations,
           min(fetched_at) as first_snapshot,
           max(fetched_at) as last_snapshot
    from dock_status
""").iloc[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Rows collected so far", f"{int(meta.total_rows):,}")
c2.metric("Snapshots", f"{int(meta.snapshots):,}")
c3.metric("Stations tracked", f"{int(meta.stations):,}")
c4.metric("Latest snapshot", pd.to_datetime(meta.last_snapshot).strftime("%b %d, %H:%M UTC"))
st.caption(f"Collection started {pd.to_datetime(meta.first_snapshot).strftime('%b %d, %Y')}.")

tab1, tab2, tab3, tab4 = st.tabs(["Network overview", "Estimated daily rides", "Station explorer", "Data quality"])

# ---------- tab 1: network overview ----------
with tab1:
    latest = q(f"""
        select distinct on (d.station_id)
               d.station_id, {name_expr} as name,
               d.num_bikes_available as bikes, d.num_docks_available as docks,
               {('s.' + SC['lat'] + ' as lat') if SC['lat'] else 'null::float as lat'},
               {('s.' + SC['lon'] + ' as lon') if SC['lon'] else 'null::float as lon'}
        from dock_status d
        left join stations s on s.station_id = d.station_id
        order by d.station_id, d.fetched_at desc
    """)
    a, b, c = st.columns(3)
    a.metric("Bikes available now", f"{int(latest.bikes.sum()):,}")
    b.metric("Docks available now", f"{int(latest.docks.sum()):,}")
    c.metric("Empty stations now", f"{int((latest.bikes == 0).sum()):,}")

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Latest availability map")
        m = latest.dropna(subset=["lat", "lon"])
        if len(m):
            st.map(m, latitude="lat", longitude="lon", size=20, color=ACCENT)
        else:
            st.info("No coordinates found in the stations table, so the map is hidden.")
    with right:
        st.subheader("Bikes available by hour of day")
        by_hour = q(f"""
            with t as (select fetched_at, sum(num_bikes_available) as bikes
                       from dock_status group by fetched_at)
            select extract(hour from fetched_at at time zone '{TZ}')::int as hour,
                   round(avg(bikes)) as avg_bikes
            from t group by 1 order by 1
        """)
        st.bar_chart(by_hour.set_index("hour").avg_bikes, color=ACCENT)
        st.caption("Average network-wide bikes available, Boston time.")

# ---------- tab 2: estimated rides ----------
with tab2:
    st.subheader("Estimated bike checkouts per day")
    daily = q(f"""
        with s as (
            select station_id, fetched_at, num_bikes_available,
                   lag(num_bikes_available) over (partition by station_id order by fetched_at) as prev
            from dock_status)
        select (fetched_at at time zone '{TZ}')::date as day,
               sum(greatest(prev - num_bikes_available, 0)) as est_checkouts,
               count(distinct fetched_at) as snapshots
        from s where prev is not null
        group by 1 order by 1
    """)
    daily["day"] = pd.to_datetime(daily["day"])
    st.warning(
        "This is a **lower-bound estimate**, not a count of real rides. It adds up drops in "
        "bikes available between consecutive snapshots. Bikes taken and returned between two "
        "snapshots cancel out, so the fewer snapshots a day has, the more it undercounts. "
        "Official trip counts are published monthly by Bluebikes."
    )
    st.bar_chart(daily.set_index("day").est_checkouts, color=ACCENT)
    st.dataframe(daily.rename(columns={"est_checkouts": "estimated checkouts"}),
                 use_container_width=True, hide_index=True)
    st.caption("The first and last days may be partial.")

# ---------- tab 3: station explorer ----------
with tab3:
    stations = q(f"""
        select distinct d.station_id, {name_expr} as name
        from dock_status d left join stations s on s.station_id = d.station_id
        order by 2
    """)
    stations["label"] = stations.name.fillna(stations.station_id)
    pick = st.selectbox("Choose a station", stations.label.tolist())
    sid = stations.loc[stations.label == pick, "station_id"].iloc[0]

    days = st.slider("Days to show", 1, 30, 7)
    ts = q(f"""
        select fetched_at at time zone '{TZ}' as time,
               num_bikes_available as bikes, num_docks_available as docks
        from dock_status
        where station_id = :sid and fetched_at >= now() - make_interval(days => :days)
        order by fetched_at
    """, sid=sid, days=days)

    stats = q("""
        select count(*) as readings,
               round(avg(num_bikes_available)::numeric, 1) as avg_bikes,
               round(100 * avg((num_bikes_available = 0)::int)::numeric, 1) as pct_empty,
               round(100 * avg((num_docks_available = 0)::int)::numeric, 1) as pct_full
        from dock_status where station_id = :sid
    """, sid=sid).iloc[0]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Readings", f"{int(stats.readings):,}")
    k2.metric("Avg bikes", stats.avg_bikes)
    k3.metric("% of time empty", f"{stats.pct_empty}%")
    k4.metric("% of time full", f"{stats.pct_full}%")

    if len(ts):
        st.subheader("Bikes and docks available over time")
        st.line_chart(ts.set_index("time")[["bikes", "docks"]], color=[ACCENT, "#F2C94C"])
    else:
        st.info("No readings for this station in the selected window.")

    hourly = q(f"""
        select extract(hour from fetched_at at time zone '{TZ}')::int as hour,
               round(avg(num_bikes_available)::numeric, 1) as avg_bikes,
               round(avg(num_docks_available)::numeric, 1) as avg_docks
        from dock_status where station_id = :sid group by 1 order by 1
    """, sid=sid)
    st.subheader("Typical availability by hour of day")
    st.line_chart(hourly.set_index("hour")[["avg_bikes", "avg_docks"]], color=[ACCENT, "#F2C94C"])

    st.subheader("Most often empty and most often full stations")
    ranks = q(f"""
        select {name_expr} as name,
               round(100 * avg((d.num_bikes_available = 0)::int)::numeric, 1) as pct_empty,
               round(100 * avg((d.num_docks_available = 0)::int)::numeric, 1) as pct_full,
               count(*) as readings
        from dock_status d left join stations s on s.station_id = d.station_id
        group by 1 having count(*) >= 20
    """)
    r1, r2 = st.columns(2)
    r1.write("Most often empty")
    r1.dataframe(ranks.sort_values("pct_empty", ascending=False).head(10),
                 hide_index=True, use_container_width=True)
    r2.write("Most often full")
    r2.dataframe(ranks.sort_values("pct_full", ascending=False).head(10),
                 hide_index=True, use_container_width=True)

# ---------- tab 4: data quality ----------
with tab4:
    st.subheader("Collection health")
    gaps = q("""
        with t as (select distinct fetched_at from dock_status),
        g as (select fetched_at,
                     extract(epoch from fetched_at - lag(fetched_at) over (order by fetched_at)) / 60 as gap_min
              from t)
        select round(avg(gap_min)::numeric, 1) as avg_gap_min,
               round(max(gap_min)::numeric, 0) as max_gap_min,
               count(*) filter (where gap_min > 120) as gaps_over_2h
        from g
    """).iloc[0]
    g1, g2, g3 = st.columns(3)
    g1.metric("Average gap between snapshots", f"{gaps.avg_gap_min} min")
    g2.metric("Longest gap", f"{int(gaps.max_gap_min):,} min")
    g3.metric("Gaps over 2 hours", int(gaps.gaps_over_2h))
    per_day = q(f"""
        select (fetched_at at time zone '{TZ}')::date as day, count(distinct fetched_at) as snapshots
        from dock_status group by 1 order by 1
    """)
    per_day["day"] = pd.to_datetime(per_day["day"])
    st.bar_chart(per_day.set_index("day").snapshots, color=ACCENT)
    st.caption("Snapshots per day. Dips show days when the collector ran less often.")
