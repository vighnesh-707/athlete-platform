"""Step 2: flag abnormal workload / fatigue patterns.

Two layers:
  1. Transparent RULES (sports-science thresholds) - every flag has a reason.
  2. An Isolation Forest (ML) as a second opinion that catches unusual combinations.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

RED, AMBER, GREEN = "RED", "AMBER", "GREEN"
ORDER = {RED: 0, AMBER: 1, GREEN: 2}

ANOMALY_FEATURES = ["acwr", "hrv_dev", "rhr_dev", "sleep_hours", "soreness", "fatigue", "monotony", "readiness"]


def add_anomaly(df: pd.DataFrame) -> pd.DataFrame:
    """Add an 'anomaly' column (True = unusual day) using Isolation Forest."""
    df = df.copy()
    X = df[ANOMALY_FEATURES]
    ok = X.notna().all(axis=1)
    df["anomaly"] = False
    df["anomaly_score"] = np.nan
    if ok.sum() > 50:
        model = IsolationForest(n_estimators=200, contamination=0.04, random_state=42)
        model.fit(X[ok])
        df.loc[ok, "anomaly"] = model.predict(X[ok]) == -1
        df.loc[ok, "anomaly_score"] = -model.score_samples(X[ok])  # higher = more unusual
    return df


def evaluate_athlete(hist: pd.DataFrame) -> dict:
    """Apply the rules to ONE athlete's history up to the chosen day. Returns status + reasons."""
    last = hist.iloc[-1]
    last3 = hist.tail(3)
    sleep3, sore3, hrv3 = last3["sleep_hours"].mean(), last3["soreness"].mean(), last3["hrv_dev"].mean()
    acwr, ready = last["acwr"], last["readiness"]
    reasons = []  # (severity, code, text)

    if pd.notna(acwr):
        if acwr > 1.5:
            reasons.append((RED, "acwr_high", f"ACWR {acwr:.2f} is above 1.5 - big load spike vs the last 4 weeks"))
        elif acwr > 1.3:
            reasons.append((AMBER, "acwr_caution", f"ACWR {acwr:.2f} is in the 1.3-1.5 caution zone"))
        elif acwr < 0.8:
            reasons.append((AMBER, "acwr_low", f"ACWR {acwr:.2f} is below 0.8 - under-loaded, rebuild gradually"))
    if ready < 40:
        reasons.append((RED, "readiness_low", f"Readiness score is very low ({ready:.0f}/100)"))
    elif ready < 60:
        reasons.append((AMBER, "readiness_mid", f"Readiness score is reduced ({ready:.0f}/100)"))
    if sleep3 < 6 and sore3 > 7:
        reasons.append((RED, "sleep_soreness", f"Poor sleep ({sleep3:.1f} h) together with high soreness ({sore3:.1f}/10) for 3 days"))
    else:
        if sleep3 < 6:
            reasons.append((AMBER, "sleep_low", f"Average sleep only {sleep3:.1f} h over the last 3 days"))
        if sore3 >= 7:
            reasons.append((AMBER, "soreness_high", f"Soreness averaging {sore3:.1f}/10 over the last 3 days"))
    if pd.notna(hrv3) and hrv3 < -0.15:
        reasons.append((AMBER, "hrv_drop", f"HRV is {abs(hrv3) * 100:.0f}% below this athlete's own baseline"))
    if pd.notna(last["monotony"]) and last["monotony"] > 2.5:
        reasons.append((AMBER, "monotony", f"Training monotony {last['monotony']:.1f} (> 2.5) - too little variation"))

    n_amber = sum(1 for s, _, _ in reasons if s == AMBER)
    status = GREEN
    if any(s == RED for s, _, _ in reasons) or n_amber >= 3:   # 3 or more amber signals escalate to red
        status = RED
        if n_amber >= 3 and not any(s == RED for s, _, _ in reasons):
            reasons.append((RED, "multi", f"{n_amber} warning signals at the same time"))
    elif n_amber >= 1:
        status = AMBER

    if bool(last.get("anomaly", False)):
        reasons.append((AMBER, "anomaly", "AI model flags this day as an unusual pattern for the squad"))
        if status == GREEN:
            status = AMBER

    return dict(status=status, reasons=reasons, acwr=acwr, readiness=ready,
                sleep3=sleep3, soreness3=sore3, hrv_dev3=hrv3)


def team_status(daily: pd.DataFrame, medical: pd.DataFrame, as_of=None) -> pd.DataFrame:
    """One row per athlete: status, reasons and key numbers as of a date."""
    as_of = pd.Timestamp(as_of) if as_of is not None else daily["date"].max()
    rows = []
    for aid, hist in daily[daily["date"] <= as_of].groupby("athlete_id"):
        r = evaluate_athlete(hist)
        r["athlete_id"] = aid
        rows.append(r)
    out = pd.DataFrame(rows).merge(medical, on="athlete_id", how="left")
    out["n_reasons"] = out["reasons"].apply(len)
    out["rank"] = out["status"].map(ORDER)
    return out.sort_values(["rank", "readiness"]).drop(columns="rank").reset_index(drop=True)
