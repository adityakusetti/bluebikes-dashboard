from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import pydeck as pdk
import streamlit as st

st.set_page_config(page_title="Bluebikes Live", page_icon=":material/pedal_bike:", layout="wide",
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
    .sdot {display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:7px;}
    .ramp {height:8px; border-radius:4px; width:220px; margin: 4px 0 2px 0;
           background: linear-gradient(90deg, #EF5350 0%, #F2C94C 50%, #4FC3F7 100%);}
    .ramp-labels {display:flex; justify-content:space-between; width:220px; font-size:0.72rem; opacity:0.7;}
    .legend-row {display:flex; gap:28px; align-items:flex-end; margin-top:8px; flex-wrap:wrap;}
    .legend-title {font-size:0.75rem; opacity:0.7; margin-bottom:2px;}
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
        pill = '<span class="sdot" style="background:#6B7280"></span>No data'
    elif age <= 15:
        pill = f'<span class="sdot" style="background:#4ADE80"></span>Live · {age:.0f} min ago'
    elif age <= 60:
        pill = f'<span class="sdot" style="background:#F2C94C"></span>Delayed · {age:.0f} min ago'
    else:
        pill = f'<span class="sdot" style="background:#EF5350"></span>Stale · {age / 60:.1f} h ago'
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

        st.subheader("Last 24 hours")
        net = q(f"""
            select fetched_at at time zone '{TZ}' as time,
                   sum(num_bikes_available) as bikes, sum(num_docks_available) as docks
            from dock_status where fetched_at >= now() - interval '1 day'
            group by fetched_at order by fetched_at
        """)
        if len(net):
            st.line_chart(net.set_index("time")[["bikes", "docks"]], color=[ACCENT, AMBER], height=220)
        st.caption("Bikes vs docks available across the whole network. Gaps mean the collector was offline.")

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
            def ramp(r):
                total = r.bikes + r.docks
                if total <= 0:
                    return [107, 114, 128, 200]          # offline / no data
                f = r.bikes / total                       # share of the station that is bikes
                if f <= 0.5:
                    t = f / 0.5                           # red -> amber
                    c0, c1 = (239, 83, 80), (242, 201, 76)
                else:
                    t = (f - 0.5) / 0.5                   # amber -> blue
                    c0, c1 = (242, 201, 76), (79, 195, 247)
                return [int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3)] + [235]

            m["color"] = m.apply(ramp, axis=1)
            m["halo"] = m.color.apply(lambda c: c[:3] + [55])
            m["size"] = (m.bikes + m.docks).clip(lower=8)
            m["bikes"] = m.bikes.astype(int)
            m["docks"] = m.docks.astype(int)
            m["capacity"] = (m.bikes + m.docks).astype(int)

            halo = pdk.Layer(
                "ScatterplotLayer", data=m, get_position="[lon, lat]",
                get_fill_color="halo", get_radius="size * 5",
                radius_min_pixels=7, radius_max_pixels=26, pickable=False,
            )
            core = pdk.Layer(
                "ScatterplotLayer", data=m, get_position="[lon, lat]",
                get_fill_color="color", get_line_color=[8, 25, 43, 255],
                stroked=True, line_width_min_pixels=1.5, get_radius="size * 2.4",
                radius_min_pixels=4, radius_max_pixels=14, pickable=True,
            )
            view = pdk.ViewState(latitude=float(m.lat.mean()), longitude=float(m.lon.mean()),
                                 zoom=11.4, pitch=0)
            st.pydeck_chart(
                pdk.Deck(
                    layers=[halo, core], initial_view_state=view, map_style=MAP_STYLE,
                    tooltip={
                        "html": "<div style='font-weight:600;margin-bottom:4px'>{name}</div>"
                                "<div>Bikes <b>{bikes}</b> &nbsp;·&nbsp; Docks <b>{docks}</b></div>"
                                "<div style='opacity:.7'>{capacity} total</div>",
                        "style": {"backgroundColor": "#0B2239", "color": "#E6F1FA",
                                  "border": "1px solid #1d4468", "borderRadius": "8px",
                                  "padding": "8px 10px", "fontSize": "12px"},
                    },
                ),
                height=640,
            )
            st.markdown(
                '<div class="legend-row">'
                '<div><div class="legend-title">Availability</div><div class="ramp"></div>'
                '<div class="ramp-labels"><span>No bikes</span><span>Balanced</span><span>No docks free</span></div></div>'
                '<div><div class="legend-title">Dot size</div><div style="font-size:0.78rem;opacity:.8">'
                'Larger = more docks at the station</div></div>'
                '<div><div class="legend-title">Grey</div><div style="font-size:0.78rem;opacity:.8">'
                'Reporting no bikes or docks</div></div>'
                '</div>',
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
        labels = stations.label.tolist()
        sid_of = lambda label: stations.loc[stations.label == label, "station_id"].iloc[0]
        today = datetime.now(ZoneInfo(TZ)).date()

        # ---------- whole-day view ----------
        st.subheader("Station day view")
        c1, c2 = st.columns([3, 2])
        pick = c1.selectbox("Station", labels, key="day_station")
        day = c2.date_input("Day", value=today, key="day_date")
        sid = sid_of(pick)

        stats = q("""
            select count(*) as readings,
                   round(100 * avg((num_bikes_available = 0)::int)::numeric, 1) as pct_empty,
                   round(100 * avg((num_docks_available = 0)::int)::numeric, 1) as pct_full
            from dock_status where station_id = :sid
        """, sid=sid).iloc[0]
        k1, k2, k3 = st.columns(3)
        k1.metric("Readings (all time)", f"{int(stats.readings):,}")
        k2.metric("Time empty", f"{stats.pct_empty}%")
        k3.metric("Time full", f"{stats.pct_full}%")

        # ---------- estimated rides on the chosen day ----------
        rides = q(f"""
            with s as (
                select station_id, fetched_at, num_bikes_available as bikes,
                       lag(num_bikes_available) over (partition by station_id order by fetched_at) as prev
                from dock_status
                where (fetched_at at time zone '{TZ}')::date = :day)
            select coalesce(sum(greatest(prev - bikes, 0)), 0) as net_out,
                   coalesce(sum(greatest(prev - bikes, 0)) filter (where station_id = :sid), 0) as st_out,
                   coalesce(sum(greatest(bikes - prev, 0)) filter (where station_id = :sid), 0) as st_in,
                   count(distinct fetched_at) as snapshots
            from s where prev is not null
        """, sid=sid, day=day).iloc[0]
        r1c, r2c, r3c, r4c = st.columns(4)
        r1c.metric(f"Est. checkouts, {day:%b %d}", f"{int(rides.st_out):,}", help="Bikes taken from this station")
        r2c.metric(f"Est. returns, {day:%b %d}", f"{int(rides.st_in):,}", help="Bikes returned to this station")
        r3c.metric("Est. network rides that day", f"{int(rides.net_out):,}")
        r4c.metric("Snapshots that day", f"{int(rides.snapshots):,}")
        st.caption(
            "These are lower-bound estimates from changes in bikes available between snapshots, not "
            "official trip counts. Rides that start and end between two snapshots cancel out, so days "
            "with few snapshots undercount heavily (a full 5-minute day has 288)."
        )
        if int(rides.snapshots) < 100:
            st.warning("Few snapshots on this day, so the ride estimates are very low compared with reality.")

        ts = q(f"""
            select fetched_at at time zone '{TZ}' as time,
                   num_bikes_available as bikes, num_docks_available as docks
            from dock_status
            where station_id = :sid and (fetched_at at time zone '{TZ}')::date = :day
            order by fetched_at
        """, sid=sid, day=day)
        if len(ts):
            st.line_chart(ts.set_index("time")[["bikes", "docks"]], color=[ACCENT, AMBER])
            st.caption(f"{len(ts)} readings on {day:%b %d}, Boston time. Straight stretches mean the "
                       "collector was offline or ran less often.")
        else:
            st.info("No readings for this station on that day.")

        # ---------- every station on the chosen day ----------
        st.subheader(f"Every station, {day:%b %d}")
        nm = f"st.{SC['name']}" if SC["name"] else "x.station_id"
        per_station = q(f"""
            with x as (
                select station_id, fetched_at, num_bikes_available as bikes,
                       lag(num_bikes_available) over (partition by station_id order by fetched_at) as prev
                from dock_status
                where (fetched_at at time zone '{TZ}')::date = :day)
            select {nm} as station,
                   sum(greatest(prev - bikes, 0)) as est_checkouts,
                   sum(greatest(bikes - prev, 0)) as est_returns,
                   count(*) as readings
            from x left join stations st on st.station_id = x.station_id
            where prev is not null
            group by 1
            order by est_checkouts desc
        """, day=day)
        if len(per_station):
            top = per_station.head(15).set_index("station")[["est_checkouts", "est_returns"]]
            st.write("Top 15 stations by estimated checkouts")
            st.bar_chart(top, color=[ACCENT, AMBER], horizontal=True, height=420)
            st.dataframe(
                per_station.rename(columns={"station": "Station", "est_checkouts": "Est. checkouts",
                                            "est_returns": "Est. returns", "readings": "Readings"}),
                hide_index=True, use_container_width=True, height=360)
            st.caption("Lower-bound estimates, same method as above. Use the table's search and "
                       "column sorting to find a station.")
        else:
            st.info("No data for that day.")

        st.divider()

        # ---------- trip planner ----------
        st.subheader("Plan a trip")
        st.caption(
            "Shows what has usually been available at that station around that time, using readings "
            "from the same kind of day (weekday or weekend) within 30 minutes either side. "
            "This is a historical pattern, not a model forecast, and it gets more reliable as more "
            "data is collected."
        )

        def profile(station_label, when_date, when_time, window_min=30):
            weekend = when_date.isoweekday() >= 6
            dow = ">= 6" if weekend else "<= 5"
            minute = when_time.hour * 60 + when_time.minute
            return q(f"""
                select num_bikes_available as bikes, num_docks_available as docks
                from dock_status
                where station_id = :sid
                  and extract(isodow from fetched_at at time zone '{TZ}') {dow}
                  and abs(extract(hour from fetched_at at time zone '{TZ}') * 60
                          + extract(minute from fetched_at at time zone '{TZ}') - :m) <= :w
            """, sid=sid_of(station_label), m=minute, w=window_min)

        def verdict(chance):
            if chance >= 0.8:
                return ":green[Likely]"
            if chance >= 0.5:
                return ":orange[Uncertain]"
            return ":red[Unlikely]"

        left_c, right_c = st.columns(2, gap="large")

        with left_c:
            st.markdown("**Pick up a bike**")
            p_station = st.selectbox("From station", labels, key="pu_station")
            p_date = st.date_input("Date", value=today, key="pu_date")
            p_time = st.time_input("Time", value=time(8, 30), key="pu_time")
            pdf = profile(p_station, p_date, p_time)
            if len(pdf) == 0:
                st.info("No readings for that station around that time yet.")
            else:
                bikes = pd.to_numeric(pdf.bikes, errors="coerce").dropna()
                chance = float((bikes >= 1).mean())
                m1, m2 = st.columns(2)
                m1.metric("Typical bikes to take", f"{bikes.median():.0f}")
                m2.metric("Usual range", f"{bikes.quantile(0.1):.0f} to {bikes.quantile(0.9):.0f}")
                st.markdown(f"**{verdict(chance)}** that at least one bike is available "
                            f"({chance * 100:.0f}% of {len(bikes)} readings).")
                if len(bikes) < 10:
                    st.caption("Few readings so far, so treat this as rough.")

        with right_c:
            st.markdown("**Park a bike**")
            d_station = st.selectbox("At station", labels, key="dr_station")
            d_date = st.date_input("Date", value=today, key="dr_date")
            d_time = st.time_input("Arrival time", value=time(9, 0), key="dr_time")
            ddf = profile(d_station, d_date, d_time)
            if len(ddf) == 0:
                st.info("No readings for that station around that time yet.")
            else:
                docks = pd.to_numeric(ddf.docks, errors="coerce").dropna()
                chance = float((docks >= 1).mean())
                m1, m2 = st.columns(2)
                m1.metric("Typical free docks", f"{docks.median():.0f}")
                m2.metric("Usual range", f"{docks.quantile(0.1):.0f} to {docks.quantile(0.9):.0f}")
                st.markdown(f"**{verdict(chance)}** that at least one dock is free "
                            f"({chance * 100:.0f}% of {len(docks)} readings).")
                if len(docks) < 10:
                    st.caption("Few readings so far, so treat this as rough.")

        st.divider()
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
