"""Generate synthetic data for 15 athletes x 90 days, saved as 5 separate CSVs.

The 5 files mimic the 5 separate sources a coach really has:
training, matches, wearable, recovery, medical.

Three "stories" are planted on purpose so the demo is always clear:
  A07 - training load spikes in the last 2 weeks + poor sleep  -> HIGH RISK
  A03 - load is normal, but hidden fatigue builds up (HRV down, soreness up)
  A11 - returned from injury, load ramped up too fast          -> ACWR spike

Run:  python generate_data.py
"""
from pathlib import Path

import numpy as np
import pandas as pd

rng = np.random.default_rng(42)  # fixed seed -> same data every time
OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True)

N_DAYS = 90
END = pd.Timestamp("2026-11-25")
DATES = pd.date_range(end=END, periods=N_DAYS)

NAMES = [
    "Arjun Mehta", "Rohan Patil", "Vikram Singh", "Aditya Rao", "Karan Joshi",
    "Imran Shaikh", "Siddharth Nair", "Neeraj Kulkarni", "Dev Malhotra",
    "Faisal Khan", "Yash Deshmukh", "Harsh Verma", "Manish Gupta",
    "Tarun Reddy", "Omkar Jadhav",
]
POSITIONS = ["Forward", "Midfielder", "Defender", "Goalkeeper", "Midfielder"] * 3
IDS = [f"A{i:02d}" for i in range(1, 16)]


def fatigue_index(aid: str, t: int) -> float:
    """Hidden 'fatigue' level (0 = fresh, 1 = very fatigued) for the planted stories."""
    if aid == "A07":  # ramps up over the last 14 days
        return float(np.clip((t - (N_DAYS - 14)) / 14, 0, 1))
    if aid == "A03":  # slow build-up over the last 21 days
        return float(np.clip((t - (N_DAYS - 21)) / 21, 0, 1)) * 0.9
    return 0.0


def load_multiplier(aid: str, t: int) -> float:
    if aid == "A07":  # training spike
        return 1 + 1.4 * float(np.clip((t - (N_DAYS - 14)) / 14, 0, 1))
    if aid == "A11":  # injured -> very light -> sudden return to full load
        if N_DAYS - 28 <= t < N_DAYS - 7:
            return 0.3
        if t >= N_DAYS - 7:
            return 1.5
    return 1.0


training, matches, wearable, recovery, medical = [], [], [], [], []

for idx, (aid, name) in enumerate(zip(IDS, NAMES)):
    base_rhr = rng.uniform(48, 62)
    base_hrv = rng.uniform(55, 90)
    starter = idx < 11
    for t, d in enumerate(DATES):
        dow = d.dayofweek  # Mon=0 ... Sun=6
        f = fatigue_index(aid, t) + abs(rng.normal(0, 0.03))
        mult = load_multiplier(aid, t)

        # --- training (Mon-Fri) ---
        if dow <= 4:
            dur = max(30, rng.normal(70, 8) * mult)
            rpe = float(np.clip(rng.normal(6, 0.8) + (1.5 if aid == "A07" and mult > 1.3 else 0), 3, 10))
            training.append(dict(date=d.date(), athlete_id=aid, duration_min=round(dur),
                                 rpe=round(rpe, 1), distance_km=round(dur / 60 * rng.normal(6.5, 0.8), 1)))
        # --- match (Saturday) ---
        if dow == 5 and not (aid == "A11" and N_DAYS - 28 <= t < N_DAYS - 7):
            minutes = int(rng.integers(60, 91)) if starter else int(rng.integers(15, 41))
            matches.append(dict(date=d.date(), athlete_id=aid, minutes_played=minutes,
                                high_intensity_sprints=int(rng.poisson(minutes * 0.3))))

        # --- wearable (daily) ---
        wearable.append(dict(
            date=d.date(), athlete_id=aid,
            resting_hr=round(base_rhr + 7 * f + rng.normal(0, 1.5), 1),
            hrv_ms=round(max(15, base_hrv * (1 - 0.3 * f) + rng.normal(0, 4)), 1),
            sleep_hours=round(float(np.clip(7.6 - 1.8 * f + rng.normal(0, 0.5), 4, 9.5)), 1),
        ))
        # --- recovery self-report (daily) ---
        post_match = 1 if dow == 6 else 0
        recovery.append(dict(
            date=d.date(), athlete_id=aid,
            soreness=int(np.clip(round(3 + 4.5 * f + post_match + rng.normal(0, 1)), 1, 10)),
            mood=int(np.clip(round(7 - 3 * f + rng.normal(0, 1)), 1, 10)),
            fatigue=int(np.clip(round(3 + 4 * f + rng.normal(0, 1)), 1, 10)),
        ))

# --- medical (static per athlete) ---
HISTORY = {
    "A07": (1, "Hamstring strain (2025)"),
    "A03": (2, "Recurring knee tendinopathy"),
    "A11": (1, "Hamstring strain - returned 3 weeks ago"),
    "A02": (1, "Ankle sprain (2024)"),
    "A09": (1, "Groin strain (2025)"),
}
for aid, name, pos in zip(IDS, NAMES, POSITIONS):
    n, note = HISTORY.get(aid, (0, "No significant history"))
    medical.append(dict(athlete_id=aid, name=name, position=pos,
                        past_injury_count=n, medical_notes=note))

pd.DataFrame(training).to_csv(OUT / "training.csv", index=False)
pd.DataFrame(matches).to_csv(OUT / "matches.csv", index=False)
pd.DataFrame(wearable).to_csv(OUT / "wearable.csv", index=False)
pd.DataFrame(recovery).to_csv(OUT / "recovery.csv", index=False)
pd.DataFrame(medical).to_csv(OUT / "medical.csv", index=False)
print(f"Wrote 5 CSVs to {OUT}")
