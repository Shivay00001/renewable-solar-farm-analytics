# renewable-solar-farm-analytics

A real solar farm telemetry analytics API. Ingest panel telemetry CSV, get
real computed metrics: energy yield, capacity factor, performance ratio
(IEC 61724 style), downtime detection, and chart-ready daily series.

## Run

```bash
pip install fastapi uvicorn python-multipart
uvicorn app:app --port 8003
```

## Usage

```bash
# 1. set the plant's rated capacity (kW)
curl -X POST localhost:8003/config \
  -H 'Content-Type: application/json' -d '{"rated_capacity_kw":100}'

# 2. ingest telemetry CSV (columns: timestamp,irradiance_wm2,temp_c,power_kw)
curl -F "file=@data/sample.csv" localhost:8003/ingest

# 3. metrics
curl localhost:8003/metrics
curl localhost:8003/chart/daily
```

A deterministic 2-day sample (with a planted 3-hour outage on day 2) ships
in `data/sample.csv` — regenerate with `python3 make_sample.py`.

## Endpoints

- `POST /config` — `{"rated_capacity_kw": N}`
- `POST /ingest` — multipart CSV upload
- `GET /metrics` — total yield, capacity factor, performance ratio,
  insolation, downtime events with estimated lost kWh, daily breakdown
- `GET /chart/daily` — per-day yield / avg irradiance / avg temp
- `DELETE /data` — clear telemetry
- `GET /health`

## Metrics math

- `energy = Σ power × Δt` (Δt from consecutive timestamps)
- `capacity_factor = energy / (rated_kW × span_hours)`
- `performance_ratio = (energy / rated_kW) / (insolation_kWh_m² / 1 kW/m²)`
- downtime: irradiance > 200 W/m² while power < 2% of rated, grouped into
  events with an estimated lost-kWh figure

## Test

```bash
pytest -q
```

Tests recompute every metric independently from the raw CSV (no app code)
and assert the API agrees, including the exact outage window.
