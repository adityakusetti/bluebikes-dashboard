# Bluebikes Live

A public Streamlit dashboard for Boston's Bluebikes bike-share system: live station availability, usage history, collection health, and a demand forecast with anomaly scores.

Live app: https://bluebikes-live.streamlit.app/

## What it shows

- **Home**: network totals (bikes and docks available, empty and full stations), a 24-hour chart, and a live availability map.
- **History**:
  - *Estimated rides*: a lower-bound estimate built from drops in bikes available between snapshots, not an official trip count.
  - *Station explorer*: a day view per station, a historical-availability trip planner, and the stations most often empty or full.
  - *Collection health*: snapshots per day, typical and longest gaps, and a table of the largest gaps.
- **Forecast**: stored hourly-demand forecasts and anomaly scores for Jan to Sep 2026, served by a separate API (see below). These are hold-out results, not live predictions.

## How it works

```
Bluebikes GBFS feed (public)
   -> collector, every 5 minutes (Android device, cron)
   -> Supabase (PostgreSQL)
   -> this Streamlit app (Home and History)

Forecast page -> Cloud Run API (FastAPI) -> stored LightGBM forecasts and city-adjusted anomaly scores
```

The collector runs on a phone, so the data has gaps. The Collection health tab shows them, including two multi-day outages (about 6.8 and 3.8 days, Oct 2026).

## Run it locally

```
pip install -r requirements.txt
streamlit run app.py
```

Home and History read from Supabase through a Streamlit SQL connection named `supabase`, configured in `.streamlit/secrets.toml` (kept out of this repository). The Forecast page needs no secrets. It can also run on its own: `streamlit run forecast_page.py`.

## Notes and limits

- Ride counts on Home and History are estimates, as described above.
- The Forecast page serves a fixed hold-out period. It does not forecast the future.
- The first load after the app or the API has been idle can take several seconds.

Data source: the public Bluebikes GBFS feed. This project is not affiliated with Bluebikes.
