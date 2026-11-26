"""Step 1 of the pipeline: merge the 5 sources and compute workload + readiness metrics."""
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).parent / "data"
MATCH_RPE = 7.5  # matches are assumed to feel like RPE 7.5

# Readiness score weights (they add up to 100 points)
W_HRV, W_SLEEP, W_SORE, W_FATIGUE, W_RHR, W_ACWR = 25, 20, 20, 15, 10, 10


def load_data(folder: Path = DATA_DIR) -> dict:
    """Read the 5 CSV files into a dict of DataFrames."""
    names = ["training", "matches", "wearable", "recovery", "medical"]
    data = {n: pd.read_csv(folder / f"{n}.csv") for n in names}
    for n in names[:4]:
        data[n]["date"] = pd.to_datetime(data[n]["date"])
    return data


def build_daily(data: dict) -> pd.DataFrame:
    """One row per athlete per day with load, ACWR, strain and readiness."""
    train = data["training"].assign(load=lambda d: d.duration_min * d.rpe)[["date", "athlete_id", "load"]]
    match = data["matches"].assign(load=lambda d: d.minutes_played * MATCH_RPE)[["date", "athlete_id", "load"]]
    daily_load = pd.concat([train, match]).groupby(["athlete_id", "date"])["load"].sum()

    athletes = sorted(data["wearable"]["athlete_id"].unique())
    dates = pd.date_range(data["wearable"]["date"].min(), data["wearable"]["date"].max())
    grid = pd.MultiIndex.from_product([athletes, dates], names=["athlete_id", "date"])
    df = daily_load.reindex(grid, fill_value=0).rename("daily_load").reset_index()  # rest day = 0 load

    df = df.merge(data["wearable"], on=["athlete_id", "date"], how="left")
    df = df.merge(data["recovery"], on=["athlete_id", "date"], how="left")
    df = df.sort_values(["athlete_id", "date"]).reset_index(drop=True)

    cols = ["resting_hr", "hrv_ms", "sleep_hours", "soreness", "mood", "fatigue"]
    g = df.groupby("athlete_id")
    df[cols] = g[cols].ffill()                    # fill small gaps in the data
    df[cols] = df[cols].fillna(df[cols].median())

    g = df.groupby("athlete_id")
    # --- workload: acute (7 day) vs chronic (28 day) ---
    df["acute"] = g["daily_load"].transform(lambda s: s.rolling(7, min_periods=7).mean())
    df["chronic"] = g["daily_load"].transform(lambda s: s.rolling(28, min_periods=14).mean())
    df["acwr"] = df["acute"] / df["chronic"].replace(0, np.nan)

    # --- monotony & strain (Foster) ---
    m7 = g["daily_load"].transform(lambda s: s.rolling(7, min_periods=7).mean())
    s7 = g["daily_load"].transform(lambda s: s.rolling(7, min_periods=7).std())
    df["monotony"] = m7 / s7.replace(0, np.nan)
    df["strain"] = m7 * 7 * df["monotony"]

    # --- personal baselines (each athlete compared with THEIR OWN normal) ---
    g = df.groupby("athlete_id")
    df["hrv_base"] = g["hrv_ms"].transform(lambda s: s.rolling(28, min_periods=7).mean())
    df["rhr_base"] = g["resting_hr"].transform(lambda s: s.rolling(28, min_periods=7).mean())
    df["hrv_dev"] = (df["hrv_ms"] - df["hrv_base"]) / df["hrv_base"]   # negative = worse
    df["rhr_dev"] = df["resting_hr"] - df["rhr_base"]                   # positive = worse

    df["readiness"] = readiness_score(df)
    return df


def readiness_score(df: pd.DataFrame) -> pd.Series:
    """0-100 score. 100 = fully fresh. Each factor subtracts penalty points."""
    hrv = np.clip(-df["hrv_dev"].fillna(0), 0, 0.4) / 0.4 * W_HRV
    sleep = np.clip((8 - df["sleep_hours"]) / 3, 0, 1) * W_SLEEP
    sore = np.clip((df["soreness"] - 2) / 8, 0, 1) * W_SORE
    fat = np.clip((df["fatigue"] - 2) / 8, 0, 1) * W_FATIGUE
    rhr = np.clip(df["rhr_dev"].fillna(0), 0, 10) / 10 * W_RHR
    acwr = np.clip((df["acwr"].fillna(1) - 1.3) / 0.5, 0, 1) * W_ACWR
    return (100 - hrv - sleep - sore - fat - rhr - acwr).clip(0, 100).round(1)


if __name__ == "__main__":
    d = build_daily(load_data())
    print(d.groupby("athlete_id").tail(1)[["athlete_id", "acwr", "readiness"]].round(2).to_string(index=False))
