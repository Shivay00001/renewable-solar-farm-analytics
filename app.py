"""renewable-solar-farm-analytics - real solar farm telemetry analytics API.

Ingest panel telemetry CSV (timestamp, irradiance_wm2, temp_c, power_kw),
then get real computed metrics:
  * total energy yield (kWh) and daily yield
  * capacity factor = energy / (rated_capacity x hours)
  * performance ratio = final_yield / reference_yield  (IEC 61724 style)
  * downtime events: sun is up (irradiance > 200 W/m2) but power ~ 0
  * chart-ready daily series (yield, avg irradiance, avg temp)

Run: uvicorn app:app --port 8003
"""
import csv
import io
import math
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

DB = Path(__file__).with_name("solar.db")
G_REF = 1000.0          # reference irradiance W/m2
SUN_THRESHOLD = 200.0   # W/m2: above this the sun is "up"
POWER_FLOOR_FRAC = 0.02  # below 2% of rated while sun up => downtime

app = FastAPI(title="renewable-solar-farm-analytics", version="1.0.0")


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS telemetry(
        ts TEXT PRIMARY KEY, irradiance_wm2 REAL, temp_c REAL, power_kw REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS config(
        key TEXT PRIMARY KEY, value REAL)""")
    return con


class SiteConfig(BaseModel):
    rated_capacity_kw: float


@app.post("/config")
def set_config(cfg: SiteConfig):
    if cfg.rated_capacity_kw <= 0:
        raise HTTPException(400, "rated_capacity_kw must be positive")
    con = db()
    con.execute("INSERT OR REPLACE INTO config VALUES ('rated_kw', ?)",
                (cfg.rated_capacity_kw,))
    con.commit()
    con.close()
    return {"rated_capacity_kw": cfg.rated_capacity_kw}


@app.post("/ingest")
async def ingest(file: UploadFile = File(...)):
    raw = (await file.read()).decode("utf-8", errors="replace")
    rows = list(csv.DictReader(io.StringIO(raw)))
    need = {"timestamp", "irradiance_wm2", "temp_c", "power_kw"}
    if not rows or not need.issubset(rows[0].keys()):
        raise HTTPException(400, f"CSV must have columns: {sorted(need)}")
    parsed = []
    for r in rows:
        try:
            parsed.append((
                datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).isoformat(),
                float(r["irradiance_wm2"]), float(r["temp_c"]), float(r["power_kw"]),
            ))
        except (ValueError, KeyError) as exc:
            raise HTTPException(400, f"Bad row {r}: {exc}")
    con = db()
    con.executemany(
        "INSERT OR REPLACE INTO telemetry VALUES (?,?,?,?)", parsed)
    con.commit()
    con.close()
    return {"ingested": len(parsed)}


def _load():
    con = db()
    rows = con.execute("SELECT * FROM telemetry ORDER BY ts").fetchall()
    cfg = con.execute("SELECT value FROM config WHERE key='rated_kw'").fetchone()
    con.close()
    rated = cfg["value"] if cfg else None
    return [dict(r) for r in rows], rated


def compute_metrics(rows, rated_kw):
    """Pure function: real math on telemetry rows. Raises ValueError w/o data."""
    if not rows:
        raise ValueError("no telemetry ingested")
    if not rated_kw:
        raise ValueError("rated capacity not set - POST /config first")
    ts = [datetime.fromisoformat(r["ts"]) for r in rows]
    energy = 0.0
    insol = 0.0  # Wh/m2
    for i, r in enumerate(rows):
        dt_h = ((ts[i + 1] - ts[i]).total_seconds() / 3600.0) if i + 1 < len(ts) else 1.0
        if dt_h <= 0 or dt_h > 24:
            dt_h = 1.0
        energy += r["power_kw"] * dt_h
        insol += r["irradiance_wm2"] * dt_h
    span_h = (ts[-1] - ts[0]).total_seconds() / 3600.0 or 1.0
    capacity_factor = energy / (rated_kw * span_h)
    ref_yield = (insol / 1000.0) / (G_REF / 1000.0)  # = insol_kwh_m2 / 1kW/m2
    final_yield = energy / rated_kw
    perf_ratio = final_yield / ref_yield if ref_yield > 0 else 0.0

    # downtime: sun up but (near-)zero power -> group consecutive rows
    events = []
    cur = None
    floor = POWER_FLOOR_FRAC * rated_kw
    for r in rows:
        down = r["irradiance_wm2"] > SUN_THRESHOLD and r["power_kw"] < floor
        if down and cur is None:
            cur = {"start": r["ts"], "end": r["ts"], "lost_kwh_est": 0.0}
        if cur is not None:
            if down:
                cur["end"] = r["ts"]
                # rough lost energy: expected ~ rated * (irradiance/G_REF)
                cur["lost_kwh_est"] += rated_kw * (r["irradiance_wm2"] / G_REF)
            else:
                events.append(cur)
                cur = None
    if cur is not None:
        events.append(cur)

    # daily aggregation for charts (energy via per-row dt)
    daily = {}
    for i, r in enumerate(rows):
        dt_h = ((ts[i + 1] - ts[i]).total_seconds() / 3600.0) if i + 1 < len(ts) else 1.0
        if dt_h <= 0 or dt_h > 24:
            dt_h = 1.0
        day = r["ts"][:10]
        d = daily.setdefault(day, {"yield_kwh": 0.0, "irr_sum": 0.0,
                                  "temp_sum": 0.0, "n": 0})
        d["yield_kwh"] += r["power_kw"] * dt_h
        d["irr_sum"] += r["irradiance_wm2"]
        d["temp_sum"] += r["temp_c"]
        d["n"] += 1
    daily_series = [
        {"date": day,
         "yield_kwh": round(v["yield_kwh"], 3),
         "avg_irradiance_wm2": round(v["irr_sum"] / v["n"], 1),
         "avg_temp_c": round(v["temp_sum"] / v["n"], 1)}
        for day, v in sorted(daily.items())
    ]
    return {
        "n_readings": len(rows),
        "period_start": rows[0]["ts"],
        "period_end": rows[-1]["ts"],
        "rated_capacity_kw": rated_kw,
        "total_yield_kwh": round(energy, 3),
        "capacity_factor": round(capacity_factor, 4),
        "performance_ratio": round(perf_ratio, 4),
        "insolation_kwh_m2": round(insol / 1000.0, 3),
        "downtime_events": len(events),
        "downtime": [
            {**e, "lost_kwh_est": round(e["lost_kwh_est"], 2)} for e in events
        ],
        "daily": daily_series,
    }


@app.get("/metrics")
def metrics():
    rows, rated = _load()
    try:
        return compute_metrics(rows, rated)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/chart/daily")
def chart_daily():
    rows, rated = _load()
    try:
        m = compute_metrics(rows, rated)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"daily": m["daily"]}


@app.delete("/data")
def clear():
    con = db()
    con.execute("DELETE FROM telemetry")
    con.commit()
    con.close()
    return {"cleared": True}


@app.get("/health")
def health():
    return {"status": "ok", "service": "renewable-solar-farm-analytics"}
