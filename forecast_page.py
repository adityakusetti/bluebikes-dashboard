"""Forecast and anomaly views, backed by the Bluebikes Cloud Run API (stored hold-out results).

Used from app.py when the "Forecast" page is chosen. It can also run on its own, without any database secrets:
    streamlit run forecast_page.py
"""
from datetime import date

import pandas as pd
import pydeck as pdk
import requests
import streamlit as st

import api_client as api

ACCENT = "#4FC3F7"
AMBER = "#F2C94C"
RED = [239, 83, 80]
BLUE = [79, 195, 247]
MAP_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"


@st.cache_data(ttl=3600, show_spinner="Contacting the API (the first request after idle can take a few seconds)...")
def load_health():
    return api.get_health()


@st.cache_data(ttl=3600, show_spinner=False)
def load_stations():
    return pd.DataFrame(api.get_stations())


@st.cache_data(ttl=600, show_spinner=False)
def load_forecast(station_id, start, end):
    return api.get_forecast(station_id, start, end)


@st.cache_data(ttl=600, show_spinner=False)
def load_anomalies(start, end, threshold, station_id, limit):
    return api.get_anomalies(start, end, threshold, station_id, limit)


def _day_view(stations, first_day, last_day, default_threshold):
    c1, c2, c3 = st.columns(3)
    day = c1.date_input("Day", value=date(2026, 7, 11), min_value=first_day, max_value=last_day, key="fc_day")
    threshold = c2.slider("Alert level |z|", 3.0, 12.0, round(default_threshold, 1), 0.1, key="fc_thr")
    limit = c3.slider("Rows", 5, 200, 30, key="fc_limit")
    try:
        res = load_anomalies(day.isoformat(), day.isoformat(), threshold, None, limit)
    except requests.RequestException as e:
        st.error(f"API error: {e}")
        return
    rows = pd.DataFrame(res["alarms"])
    st.subheader(f"Strongest anomalies on {day:%b %d, %Y}")
    if rows.empty:
        st.info("No station-hours at or above this alert level on that day.")
        return
    rows = rows.merge(stations[["station_id", "lat", "lng"]], on="station_id", how="left")
    best = rows.sort_values("z", key=lambda s: s.abs(), ascending=False).drop_duplicates("station_id")
    m = best.dropna(subset=["lat", "lng"]).copy()
    if len(m):
        m["color"] = m["z"].apply(lambda z: (BLUE if z > 0 else RED) + [235])
        m["halo"] = m["z"].apply(lambda z: (BLUE if z > 0 else RED) + [55])
        m["radius"] = 60 + m["z"].abs() * 20
        m = m.rename(columns={"lng": "lon"})
        m["z_txt"] = m["z"].map(lambda z: f"{z:+.1f}")
        halo = pdk.Layer("ScatterplotLayer", data=m, get_position="[lon, lat]", get_fill_color="halo",
                         get_radius="radius * 2.2", radius_min_pixels=9, radius_max_pixels=34, pickable=False)
        core = pdk.Layer("ScatterplotLayer", data=m, get_position="[lon, lat]", get_fill_color="color",
                         get_line_color=[8, 25, 43, 255], stroked=True, line_width_min_pixels=1.5,
                         get_radius="radius", radius_min_pixels=5, radius_max_pixels=20, pickable=True)
        view = pdk.ViewState(latitude=float(m["lat"].mean()), longitude=float(m["lon"].mean()), zoom=11.2, pitch=0)
        st.pydeck_chart(
            pdk.Deck(layers=[halo, core], initial_view_state=view, map_style=MAP_STYLE,
                     tooltip={"html": "<div style='font-weight:600;margin-bottom:4px'>{name}</div>"
                                      "<div>z <b>{z_txt}</b> &nbsp;·&nbsp; last {window_hours} h</div>"
                                      "<div>Trips <b>{window_actual}</b> vs forecast <b>{window_forecast}</b></div>",
                              "style": {"backgroundColor": "#0B2239", "color": "#E6F1FA",
                                        "border": "1px solid #1d4468", "borderRadius": "8px",
                                        "padding": "8px 10px", "fontSize": "12px"}}),
            height=520)
        st.markdown('<span style="font-size:0.8rem;opacity:.8">'
                    '<span style="color:#4FC3F7">●</span> more trips than forecast &nbsp;&nbsp;'
                    '<span style="color:#EF5350">●</span> fewer trips than forecast &nbsp;&nbsp;'
                    'One dot per station (its strongest alarm of the day); bigger = larger |z|.</span>',
                    unsafe_allow_html=True)
    st.dataframe(rows[["name", "window_end_hour", "window_hours", "window_actual", "window_forecast", "z"]]
                 .rename(columns={"name": "Station", "window_end_hour": "Window ends", "window_hours": "Window (h)",
                                  "window_actual": "Actual trips", "window_forecast": "Forecast trips"}),
                 hide_index=True, use_container_width=True)


def _station_view(stations, first_day, last_day, default_threshold):
    names = stations.sort_values("name").copy()
    names["label"] = names["name"].fillna(names["station_id"]) + "  (" + names["station_id"] + ")"
    ids = names["station_id"].tolist()
    idx = ids.index("C32023") if "C32023" in ids else 0
    c1, c2 = st.columns([3, 2])
    label = c1.selectbox("Station", names["label"].tolist(), index=idx, key="fc_station")
    picked = c2.date_input("Dates", value=(date(2026, 7, 10), date(2026, 7, 12)),
                           min_value=first_day, max_value=last_day, key="fc_dates")
    if not isinstance(picked, (tuple, list)) or len(picked) != 2:
        st.info("Pick a start and an end date.")
        return
    station_id = names.loc[names["label"] == label, "station_id"].iloc[0]
    start, end = picked
    try:
        fc = load_forecast(station_id, start.isoformat(), end.isoformat())
        al = load_anomalies(start.isoformat(), end.isoformat(), default_threshold, station_id, 50)
    except requests.RequestException as e:
        st.error(f"API error: {e}")
        return
    pts = pd.DataFrame(fc["points"])
    if pts.empty:
        st.info("No data for that station in this range.")
        return
    pts["hour"] = pd.to_datetime(pts["hour"])
    st.line_chart(pts.set_index("hour")[["actual", "forecast"]], color=[ACCENT, AMBER], height=300)
    st.caption("Actual trips per hour (blue) vs the model's forecast (amber).")
    a, b = st.columns(2)
    a.metric("Actual trips in range", f"{int(pts['actual'].sum()):,}")
    b.metric("Forecast trips in range", f"{float(pts['forecast'].sum()):,.0f}")
    st.write("**Alarms at the default alert level**")
    arows = pd.DataFrame(al["alarms"])
    if arows.empty:
        st.write("None in this range.")
    else:
        st.dataframe(arows[["window_end_hour", "window_hours", "window_actual", "window_forecast", "z"]]
                     .rename(columns={"window_end_hour": "Window ends", "window_hours": "Window (h)",
                                      "window_actual": "Actual trips", "window_forecast": "Forecast trips"}),
                     hide_index=True, use_container_width=True)


def render():
    st.title("Forecast and anomalies")
    st.caption("Stored hold-out results for Jan to Sep 2026 from a LightGBM demand model, served by a Cloud Run API. "
               "These are not live predictions. Times are local wall-clock times.")
    try:
        health = load_health()
        stations = load_stations()
    except requests.RequestException as e:
        st.error(f"Could not reach the API at {api.base_url()}: {e}")
        return
    first_day = date.fromisoformat(health["first_hour"][:10])
    last_day = date.fromisoformat(health["last_hour"][:10])
    default_threshold = float(health["alarm_threshold_abs_z"])
    with st.expander("How to read the score (z)"):
        st.write(
            "z measures how unusual recent demand was compared with the forecast. It is the strongest of the last 3, "
            "6 and 24 hours of (actual minus forecast), scaled by expected demand. Large negative: far fewer trips "
            "than forecast (for example an offline station). Large positive: far more trips (for example a nearby "
            "event). Because the window looks back up to 24 hours, a burst of trips keeps z high for hours "
            f"afterward. The default alert level is |z| of {default_threshold:.2f}, set so that about 0.1% of "
            "ordinary validation station-hours alarm; on the test period the actual rate was about 0.19%, and real "
            "events also trigger it.")
    tab_day, tab_station = st.tabs(["Day view", "Station view"])
    with tab_day:
        _day_view(stations, first_day, last_day, default_threshold)
    with tab_station:
        _station_view(stations, first_day, last_day, default_threshold)


if __name__ == "__main__":
    st.set_page_config(page_title="Bluebikes forecast and anomalies", layout="wide")
    render()
