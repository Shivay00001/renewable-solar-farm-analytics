"""Deterministic sample telemetry: 2 days, 100 kW rated plant.
Day 1: clean bell-curve sun. Day 2: same, but a 3-hour outage 10:00-12:00
(sun up, power forced to 0)."""
import csv
import math
from datetime import datetime, timedelta

RATED = 100.0

rows = [("timestamp", "irradiance_wm2", "temp_c", "power_kw")]
for day in ("2026-09-20", "2026-09-21"):
    for h in range(24):
        ts = datetime(2026, 9, int(day[-2:]), h, 0, 0)
        irr = max(0.0, 950.0 * math.sin(math.pi * (h - 6) / 12)) if 6 <= h <= 18 else 0.0
        irr = round(irr, 1)
        temp = round(25.0 + (irr / 1000.0) * 15.0, 1)
        power = round(RATED * (irr / 1000.0) * 0.85, 3)
        if day == "2026-09-21" and h in (10, 11, 12):
            power = 0.0  # outage
        rows.append((ts.isoformat(), irr, temp, power))

from pathlib import Path
Path("data").mkdir(exist_ok=True)
with open("data/sample.csv", "w", newline="") as f:
    csv.writer(f).writerows(rows)
print(f"wrote data/sample.csv: {len(rows)-1} rows, rated={RATED} kW")
