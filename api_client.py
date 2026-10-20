"""Thin client for the Bluebikes Cloud Run API. No Streamlit code here, so it can be tested on its own."""
import os

import requests

DEFAULT_API_URL = "https://bluebikes-api-498367231546.us-central1.run.app"


def base_url() -> str:
    return os.environ.get("API_URL", DEFAULT_API_URL).rstrip("/")


def _get(path, params=None):
    # the first request after the service has been idle can take several seconds
    r = requests.get(base_url() + path, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def get_health():
    return _get("/health")


def get_stations():
    return _get("/stations")


def get_forecast(station_id, start, end):
    return _get("/forecast", {"station_id": station_id, "start": start, "end": end})


def get_anomalies(start, end, threshold=None, station_id=None, limit=50):
    params = {"start": start, "end": end, "limit": limit}
    if threshold is not None:
        params["threshold"] = threshold
    if station_id is not None:
        params["station_id"] = station_id
    return _get("/anomalies", params)
