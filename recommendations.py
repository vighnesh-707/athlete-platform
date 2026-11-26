"""Step 3: turn flags into practical advice for coaches and medical staff."""

COACH = {
    "acwr_high": "Cut training volume by 20-30% for the next 3-5 days; swap high-intensity work for technical or low-load sessions.",
    "acwr_caution": "Hold load steady this week - do not add volume or intensity until ACWR drops below 1.3.",
    "acwr_low": "Increase load gradually (about 10% per week). Avoid jumping straight back to full intensity.",
    "readiness_low": "Consider a rest or recovery-only day and re-check readiness tomorrow before the next hard session.",
    "readiness_mid": "Reduce intensity of the next session and monitor readiness daily.",
    "sleep_soreness": "Make tomorrow a recovery day; check the schedule for late sessions or travel affecting sleep.",
    "sleep_low": "Talk to the athlete about sleep routine; avoid late evening sessions where possible.",
    "soreness_high": "Lighten the next session and include mobility and recovery work.",
    "hrv_drop": "HRV is down on the athlete's own baseline - a sign of accumulated stress. Plan an easier day.",
    "monotony": "Add variation: mix hard and easy days instead of the same load every day.",
    "multi": "Several warning signs together - treat as high risk and reduce load until they improve.",
    "anomaly": "Pattern is unusual compared with the squad - check in with the athlete directly.",
}
MEDICAL = {
    "acwr_high": "Screen for early overuse symptoms (muscle tightness, joint pain) and review the injury history.",
    "acwr_caution": "Keep the athlete under observation; ask about any new niggles.",
    "acwr_low": "Confirm the athlete is cleared for progressive loading; monitor the previously injured area.",
    "readiness_low": "Check for illness, poor nutrition or hydration, and psychological stress.",
    "readiness_mid": "Review recovery routine: nutrition, hydration, sleep.",
    "sleep_soreness": "Assess muscle recovery; consider a soft-tissue or physio check-in.",
    "sleep_low": "Review sleep habits and screen for stress or illness.",
    "soreness_high": "Physio check of the sore area; consider massage or other recovery modalities.",
    "hrv_drop": "Check for signs of illness or overtraining; repeat HRV measurement in the morning.",
    "monotony": "No clinical action - flag to the coaching staff for session planning.",
    "multi": "Run a full wellness screening before the next training session.",
    "anomaly": "Review the athlete's wearable and recovery data for sensor errors or genuine change.",
}


def build_recommendations(row) -> dict:
    """row = one athlete row from team_status(). Returns coach + medical action lists."""
    coach, med, seen = [], [], set()
    for _, code, _ in row["reasons"]:
        if code in seen:
            continue
        seen.add(code)
        coach.append(COACH[code])
        med.append(MEDICAL[code])
    if row.get("past_injury_count", 0) and row["status"] != "GREEN":
        med.append(f"Injury history on file: {row['medical_notes']}. Prioritise that area in the check.")
    if not coach:
        coach.append("No action needed - continue the planned training.")
        med.append("No action needed.")
    return {"coach": coach, "medical": med}
