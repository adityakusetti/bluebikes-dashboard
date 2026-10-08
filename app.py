import pandas as pd
import pydeck as pdk
import streamlit as st

st.set_page_config(page_title="Bluebikes Live", page_icon="🚲", layout="wide",
                   initial_sidebar_state="expanded")
TZ = "America/New_York"
ACCENT = "#4FC3F7"
AMBER = "#F2C94C"
MAP_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"

st.markdown(
    """
    <style>
    footer {visibility: hidden;}
    .block-container {padding-top: 3rem; padding-bottom: 2rem; max-width: 1400px;}
    h1 {font-weight: 650; letter-spacing: -0.6px; margin-bottom: 0;}
    h2, h3 {font-weight: 550; letter-spacing: -0.2px;}
    [data-testid="stSidebar"] {background: #08192B; border-right: 1px solid #14304d;}
    .brand {font-size: 1.35rem; font-weight: 650; letter-spacing: -0.4px; margin-bottom: 2px;}
    .brand span {color: #4FC3F7;}
    .tagline {font-size: 0.8rem; opacity: 0.6; margin-bottom: 1.2rem;}
    .pill {display: inline-block; padding: 4px 12px; border-radius: 999px; font-size: 0.8rem;
           background: #12304f; border: 1px solid #1d4468;}
    div[data-testid="stMetric"] {background: #12304f; border: 1px solid #1d4468;
           border-radius: 12px; padding: 14px 16px;}
    div[data-testid="stMetricLabel"] {opacity: 0.75;}
    div[data-testid="stMetricValue"] {font-weight: 600;}
    .legend {font-size: 0.8rem; opacity: 0.8; margin-top: 6px;}
    .dot {display:inline-block; width:9px; height:9px; border-radius:50%; margin: 0 4px 0 12px;}
    hr {border-color: #14304d;}
    </style>
    """,
    unsafe_allow_html=True,
)

conn = st.connection("supabase", type="sql")


@st.cache_data(ttl=600, show_spinner=False)
def q(sql: str, **params) -> pd.DataFrame:
    return conn.query(sql, params=params or None, ttl=600)


@st.cache_data(ttl=3600, show_spinner=False)
def station_columns() -> dict:
    cols = q("select column_name from information_schema.columns where table_name = 'stations'")
    names = set(cols.column_name.str.lower())
    pick = lambda opts: next((o for o in opts if o in names), None)
    return {
        "name": pick(["name", "station_name"]),
        "lat": pick(["lat", "latitude"]),
        "lon": pick(["lon", "lng", "longitude"]),
    }


SC = station_columns()
name_expr = f"s.{SC['name']}" if SC["name"] else "d.station_id"

meta = q("""
    select count(*) as total_rows, count(distinct fetched_at) as snapshots,
           count(distinct station_id) as stations,
           min(fetched_at) as first_snapshot, max(fetched_at) as last_snapshot,
           extract(epoch from now() - max(fetched_at)) / 60 as age_min
    from dock_status
""").iloc[0]

# ---------------- sidebar ----------------
with st.sidebar:
    st.markdown('<div class="brand">Bluebikes <span>Live</span></div>'
                '<div class="tagline">Network availability and history</div>',
                unsafe_allow_html=True)
    page = st.radio("Navigate", ["Home", "History"], label_visibility="collapsed")
    st.divider()
    age = meta.age_min
    if age is None or pd.isna(age):
        pill = "No data"
    elif age <= 15:
        pill = f"🟢 Live · {age:.0f} min ago"
    elif age <= 60:
        pill = f"🟡 Delayed · {age:.0f} min ago"
    else:
        pill = f"🔴 Stale · {age / 60:.1f} h ago"
    st.markdown(f'<span class="pill">{pill}</span>', unsafe_allow_html=True)
    st.caption(f"Collecting since {pd.to_datetime(meta.first_snapshot).strftime('%b %d, %Y')}")
    st.caption(f"{int(meta.total_rows):,} rows · {int(meta.snapshots):,} snapshots")
    st.caption("Source: public Bluebikes GBFS feed")

# =====================================================================
# HOME
# =====================================================================
if page == "Home":
    st.title("Network overview")
    st.caption("Live station availability across the Bluebikes system.")

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
    latest["bikes"] = pd.to_numeric(latest.bikes, errors="coerce").fillna(0)
    latest["docks"] = pd.to_numeric(latest.docks, errors="coerce").fillna(0)

    left, right = st.columns([2, 3], gap="large")

    with left:
        a, b = st.columns(2)
        a.metric("Bikes available", f"{int(latest.bikes.sum()):,}")
        b.metric("Docks available", f"{int(latest.docks.sum()):,}")
        c, d = st.columns(2)
        c.metric("Empty stations", f"{int((latest.bikes == 0).sum()):,}")
        d.metric("Full stations", f"{int((latest.docks == 0).sum()):,}")

        st.subheader("Typical bikes by hour")
        by_hour = q(f"""
            with t as (select fetched_at, sum(num_bikes_available) as bikes
                       from dock_status group by fetched_at)
            select extract(hour from fetched_at at time zone '{TZ}')::int as hour,
                   round(avg(bikes)) as avg_bikes
            from t group by 1 order by 1
        """)
        st.bar_chart(by_hour.set_index("hour").avg_bikes, color=ACCENT, height=220)
        st.caption("Network-wide average, Boston time.")

        st.subheader("Emptiest stations now")
        emptiest = latest[latest.bikes == 0][["name", "docks"]].head(8)
        if len(emptiest):
            st.dataframe(emptiest.rename(columns={"name": "Station", "docks": "Docks"}),
                         hide_index=True, use_container_width=True)
        else:
            st.success("No empty stations right now.")

    with right:
        m = latest.copy()
        m["lat"] = pd.to_numeric(m.lat, errors="coerce")
        m["lon"] = pd.to_numeric(m.lon, errors="coerce")
        m = m.dropna(subset=["lat", "lon"])
        if len(m):
            def color(r):
                if r.bikes == 0:
                    return [239, 83, 80, 210]
                if r.docks == 0:
                    return [242, 201, 76, 210]
                return [79, 195, 247, 210]

            m["color"] = m.apply(color, axis=1)
            layer = pdk.Layer(
                "ScatterplotLayer", data=m, get_position="[lon, lat]",
                get_fill_color="color", get_radius=55, pickable=True,
                radius_min_pixels=3, radius_max_pixels=11,
            )
            view = pdk.ViewState(latitude=float(m.lat.mean()), longitude=float(m.lon.mean()), zoom=11.3)
            st.pydeck_chart(
                pdk.Deck(layers=[layer], initial_view_state=view, map_style=MAP_STYLE,
                         tooltip={"text": "{name}\nBikes: {bikes}\nDocks: {docks}"}),
                height=640,
            )
            st.markdown(
                '<div class="legend"><span class="dot" style="background:#4FC3F7"></span>Bikes and docks available'
                '<span class="dot" style="background:#EF5350"></span>Empty'
                '<span class="dot" style="background:#F2C94C"></span>Full</div>',
                unsafe_allow_html=True,
            )
        else:
            st.info("No coordinates found in the stations table, so the map is hidden.")

# =====================================================================
# HISTORY
# =====================================================================
else:
    st.title("History")
    st.caption("Estimated usage, station behaviour, and collection health over time.")
    tab1, tab2, tab3 = st.tabs(["Estimated rides", "Station explorer", "Collection health"])

    # ---------- estimated rides ----------
    with tab1:
        daily = q(f"""
            with s as (
                select station_id, fetched_at, num_bikes_available,
                       lag(num_bikes_available) over (partition by station_id order by fetched_at) as prev
                from dock_status)
            select (fetched_at at time zone '{TZ}')::date as day,
                   sum(greatest(prev - num_bikes_available, 0)) as est_checkouts,
                   count(distinct fetched_at) as snapshots
            from s where prev is not null group by 1 order by 1
        """)
        daily["day"] = pd.to_datetime(daily["day"])
        st.warning(
            "This is a **lower-bound estimate**, not a count of real rides. It adds up drops in bikes "
            "available between consecutive snapshots, so rides that start and end between two snapshots "
            "cancel out. Days with more snapshots are more accurate. Official trip counts are published "
            "monthly by Bluebikes."
        )
        st.bar_chart(daily.set_index("day").est_checkouts, color=ACCENT)
        st.dataframe(daily.rename(columns={"est_checkouts": "estimated checkouts"}),
                     use_container_width=True, hide_index=True)
        st.caption("The first and last days may be partial.")

    # ---------- station explorer ----------
    with tab2:
        stations = q(f"""
            select distinct d.station_id, {name_expr} as name
            from dock_status d left join stations s on s.station_id = d.station_id
            order by 2
        """)
        stations["label"] = stations.name.fillna(stations.station_id)
        c1, c2 = st.columns([3, 2])
        pick = c1.selectbox("Choose a station", stations.label.tolist())
        days_back = c2.slider("Days to show", 1, 30, 7)
        sid = stations.loc[stations.label == pick, "station_id"].iloc[0]

        ts = q(f"""
            select fetched_at at time zone '{TZ}' as time,
                   num_bikes_available as bikes, num_docks_available as docks
            from dock_status
            where station_id = :sid and fetched_at >= now() - make_interval(days => :days)
            order by fetched_at
        """, sid=sid, days=days_back)
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
        k3.metric("Time empty", f"{stats.pct_empty}%")
        k4.metric("Time full", f"{stats.pct_full}%")

        if len(ts):
            st.subheader("Bikes and docks over time")
            st.line_chart(ts.set_index("time")[["bikes", "docks"]], color=[ACCENT, AMBER])
        else:
            st.info("No readings for this station in the selected window.")

        hourly = q(f"""
            select extract(hour from fetched_at at time zone '{TZ}')::int as hour,
                   round(avg(num_bikes_available)::numeric, 1) as avg_bikes,
                   round(avg(num_docks_available)::numeric, 1) as avg_docks
            from dock_status where station_id = :sid group by 1 order by 1
        """, sid=sid)
        st.subheader("Typical availability by hour")
        st.line_chart(hourly.set_index("hour")[["avg_bikes", "avg_docks"]], color=[ACCENT, AMBER])

        st.subheader("Most often empty and full")
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

    # ---------- collection health ----------
    with tab3:
        EXPECTED_PER_DAY = 288  # one snapshot every 5 minutes
        now_stats = q("""
            select count(distinct fetched_at) as snaps_24h
            from dock_status where fetched_at >= now() - interval '1 day'
        """).iloc[0]
        latest_n = q("""
            select count(*) as n from dock_status
            where fetched_at = (select max(fetched_at) from dock_status)
        """).iloc[0].n
        snaps_24h = int(now_stats.snaps_24h or 0)

        st.subheader("Right now")
        n1, n2, n3 = st.columns(3)
        n1.metric("Snapshots, last 24 h", f"{snaps_24h:,} / {EXPECTED_PER_DAY}")
        n2.metric("Completeness, last 24 h", f"{min(100 * snaps_24h / EXPECTED_PER_DAY, 100):.0f}%")
        n3.metric("Stations in latest snapshot", f"{int(latest_n):,}")

        hourly_c = q(f"""
            select date_trunc('hour', fetched_at at time zone '{TZ}') as hour,
                   count(distinct fetched_at) as snapshots
            from dock_status where fetched_at >= now() - interval '1 day'
            group by 1 order by 1
        """)
        if len(hourly_c):
            hourly_c["hour"] = pd.to_datetime(hourly_c["hour"])
            st.write("Snapshots per hour, last 24 hours (a full hour is 12)")
            st.bar_chart(hourly_c.set_index("hour").snapshots, color=ACCENT, height=220)

        st.divider()
        st.subheader("History")
        options = {"Last 24 hours": 1, "Last 3 days": 3, "Last 10 days": 10, "All time": 36500}
        choice = st.radio("Window", list(options), index=2, horizontal=True)
        days = options[choice]

        w = q("""
            with t as (select distinct fetched_at from dock_status
                       where fetched_at >= now() - make_interval(days => :d)),
            g as (select fetched_at,
                         extract(epoch from fetched_at - lag(fetched_at) over (order by fetched_at)) / 60 as gap_min
                  from t)
            select count(*) as snapshots,
                   round((percentile_cont(0.5) within group (order by gap_min))::numeric, 1) as median_gap,
                   round(max(gap_min)::numeric, 0) as max_gap,
                   count(*) filter (where gap_min > 15) as gaps_15,
                   count(*) filter (where gap_min > 60) as gaps_60
            from g
        """, d=days).iloc[0]

        if int(w.snapshots or 0) == 0:
            st.info("No snapshots in this window.")
        else:
            w1, w2, w3, w4 = st.columns(4)
            w1.metric("Snapshots", f"{int(w.snapshots):,}")
            w2.metric("Typical gap", f"{w.median_gap} min")
            w3.metric("Longest gap", f"{int(w.max_gap):,} min")
            w4.metric("Gaps over 15 / 60 min", f"{int(w.gaps_15)} / {int(w.gaps_60)}")

            per_day = q(f"""
                select (fetched_at at time zone '{TZ}')::date as day,
                       count(distinct fetched_at) as snapshots
                from dock_status where fetched_at >= now() - make_interval(days => :d)
                group by 1 order by 1
            """, d=days)
            per_day["day"] = pd.to_datetime(per_day["day"])
            st.write(f"Snapshots per day — {EXPECTED_PER_DAY} is a full day at 5-minute spacing")
            st.bar_chart(per_day.set_index("day").snapshots, color=ACCENT)
            st.caption("Older days show fewer snapshots because the first collector ran about hourly. "
                       "The first and last days may be partial.")

            gaps = q(f"""
                with t as (select distinct fetched_at from dock_status
                           where fetched_at >= now() - make_interval(days => :d)),
                g as (select lag(fetched_at) over (order by fetched_at) as gap_start,
                             fetched_at as gap_end,
                             extract(epoch from fetched_at - lag(fetched_at) over (order by fetched_at)) / 60 as gap_min
                      from t)
                select gap_start at time zone '{TZ}' as "from (Boston)",
                       gap_end at time zone '{TZ}' as "to (Boston)",
                       round(gap_min::numeric) as "minutes"
                from g where gap_min > 15 order by gap_min desc limit 10
            """, d=days)
            st.write("Largest gaps (over 15 minutes)")
            if len(gaps):
                st.dataframe(gaps, hide_index=True, use_container_width=True)
            else:
                st.success("No gaps over 15 minutes in this window.")
