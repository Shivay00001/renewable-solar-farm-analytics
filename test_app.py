"""Real verification: independently recompute every metric from the raw CSV
(plain Python, not calling app code) and assert the API agrees."""
import csv
import math
import os
import subprocess
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import app as appmod

CSV = os.path.join(os.path.dirname(__file__), "data", "sample.csv")
RATED = 100.0


@pytest.fixture(scope="module")
def client():
    if os.path.exists(appmod.DB):
        os.remove(appmod.DB)
    subprocess.run(["python3", "make_sample.py"],
                   cwd=os.path.dirname(__file__), check=True,
                   capture_output=True)
    c = TestClient(appmod.app)
    c.post("/config", json={"rated_capacity_kw": RATED})
    with open(CSV, "rb") as f:
        r = c.post("/ingest", files={"file": ("sample.csv", f, "text/csv")})
    assert r.status_code == 200 and r.json()["ingested"] == 48
    yield c
    if os.path.exists(appmod.DB):
        os.remove(appmod.DB)


def load_csv():
    with open(CSV) as f:
        return list(csv.DictReader(f))


def expected():
    """Independent math: energy, capacity factor, performance ratio, downtime."""
    rows = load_csv()
    ts = [datetime.fromisoformat(r["timestamp"]) for r in rows]
    energy = insol = 0.0
    for i, r in enumerate(rows):
        dt = (ts[i + 1] - ts[i]).total_seconds() / 3600 if i + 1 < len(ts) else 1.0
        energy += float(r["power_kw"]) * dt
        insol += float(r["irradiance_wm2"]) * dt
    span = (ts[-1] - ts[0]).total_seconds() / 3600
    cf = energy / (RATED * span)
    pr = (energy / RATED) / (insol / 1000.0)
    # downtime windows: irradiance > 200 and power < 2% rated
    wins, cur = [], None
    for r in rows:
        down = float(r["irradiance_wm2"]) > 200 and float(r["power_kw"]) < 0.02 * RATED
        if down and cur is None:
            cur = [r["timestamp"]]
        if cur is not None and not down:
            cur.append(prev_ts)
            wins.append(tuple(cur))
            cur = None
        prev_ts = r["timestamp"]
    if cur is not None:
        wins.append((cur[0], rows[-1]["timestamp"]))
    day1 = sum(float(r["power_kw"]) for r in rows if r["timestamp"].startswith("2026-09-20"))
    return energy, cf, pr, wins, day1


def test_metrics_match_independent_math(client):
    energy, cf, pr, wins, day1 = expected()
    m = client.get("/metrics").json()
    assert m["n_readings"] == 48
    assert m["total_yield_kwh"] == pytest.approx(round(energy, 3))
    assert m["capacity_factor"] == pytest.approx(round(cf, 4))
    assert m["performance_ratio"] == pytest.approx(round(pr, 4))
    assert m["insolation_kwh_m2"] == pytest.approx(
        sum(float(r["irradiance_wm2"]) for r in load_csv()) / 1000.0, rel=1e-6)


def test_downtime_detected(client):
    _, _, _, wins, _ = expected()
    assert wins == [("2026-09-21T10:00:00", "2026-09-21T12:00:00")]
    m = client.get("/metrics").json()
    assert m["downtime_events"] == 1
    ev = m["downtime"][0]
    assert ev["start"].startswith("2026-09-21T10:00")
    assert ev["end"].startswith("2026-09-21T12:00")
    assert ev["lost_kwh_est"] > 200  # ~3h x ~100kW x irr/1000


def test_daily_chart(client):
    _, _, _, _, day1 = expected()
    d = client.get("/chart/daily").json()["daily"]
    assert [x["date"] for x in d] == ["2026-09-20", "2026-09-21"]
    assert d[0]["yield_kwh"] == pytest.approx(day1, rel=1e-6)
    assert d[1]["yield_kwh"] < d[0]["yield_kwh"]  # outage day yields less
    assert d[0]["avg_irradiance_wm2"] > 200


def test_bad_csv_rejected(client):
    r = client.post("/ingest", files={"file": ("x.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 400


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
