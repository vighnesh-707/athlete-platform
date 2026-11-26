# Athlete Workload & Injury-Risk Intelligence Platform

A single dashboard that merges training, match, wearable, recovery and medical data, flags abnormal
workload and fatigue patterns, and gives practical recommendations to coaches and medical staff.

## Run it

```bash
pip install -r requirements.txt
streamlit run app.py         # opens the dashboard in your browser
```

## Uploading athlete data

The dashboard uses one PDF upload instead of separate CSV files or demo data.
Download the PDF template from the sidebar. It contains `[training]`, `[matches]`,
`[wearable]`, `[recovery]`, and `[medical]` sections. Each section uses a CSV-style
header and rows beneath it. The `[medical]` section must include
`athlete_id,name,email,position,past_injury_count,medical_notes`.

## Athlete email notifications

High-risk athletes can receive coach and medical requirements by email.
Automatic sending is enabled with these environment variables:

```text
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-account@gmail.com
SMTP_PASSWORD=your-app-password
SMTP_FROM=your-account@gmail.com
```

When SMTP is not configured, use the "Open email draft" button.

Coaches also have a **Send athlete suggestions** page with high-risk athlete details,
recommended coach and medical actions, an optional personal message, and email buttons.
This page is available only to accounts with the Coach role.

## Share a public demo

1. Create a **public** GitHub repository and upload the project files. Include
   `app.py`, `auth.py`, `metrics.py`, `risk_rules.py`, `recommendations.py`,
   `requirements.txt`, and the README. Do not upload `.env` files or `users.db`.
2. On [Streamlit Community Cloud](https://share.streamlit.io/), create an app from
   that repository, select the branch, and set the main file to `app.py`.
3. Share the generated app URL. Friends can select **Fork** on GitHub to make
   separate copies, then deploy their own copy or run it locally.

This is for a public demo only: use synthetic/sample athlete data. Uploaded PDFs
include contact and health-related information and are processed by the hosting
service. Do not add SMTP credentials to a public demo. Community Cloud's local
SQLite file is not a suitable persistent account database.

## Project structure

| File | Purpose |
|------|---------|
| `generate_data.py` | Optional local data generator for development |
| `metrics.py` | Merges sources; computes acute/chronic load, ACWR, monotony, strain, readiness |
| `risk_rules.py` | Rule-based flags (red/amber/green) + Isolation Forest anomaly detection |
| `recommendations.py` | Converts flags into coach and medical actions |
| `auth.py` | Login and signup (SQLite, hashed passwords, lockout after 5 wrong attempts) |
| `app.py` | Streamlit dashboard with coach messaging (behind the login) |

## Login

- First screen is **Log in / Sign up**. Staff choose a role (Coach or Medical staff); the Alerts page opens on that role's actions.
- Demo account for presentations: username `coach`, password `Coach@123` (or click "Try the demo account").
- Passwords are stored as PBKDF2-SHA256 hashes with a random salt, never as plain text. Users are saved in `users.db` (created automatically).
- For a real deployment: remove the demo account, serve over HTTPS and add password reset / email verification.

## How it works

- **ACWR** = 7-day average load / 28-day average load. 0.8-1.3 is the sweet spot; above 1.5 is high injury risk.
- **Readiness (0-100)** = 100 minus penalties for HRV drop (25), sleep (20), soreness (20), fatigue (15), resting HR (10), ACWR (10).
- **Rules** give a reason for every flag. **Isolation Forest** is a second opinion for unusual combinations.
- Each athlete is compared with **their own baseline**.

## Demo scenarios planted in the data

- **A07 Siddharth Nair:** load spike + poor sleep -> high risk
- **A03 Vikram Singh:** normal load, but hidden fatigue (HRV down, soreness up)
- **A11 Yash Deshmukh:** back from injury, load ramped too fast -> ACWR 2.4

## Limitations

Synthetic data; thresholds come from sports-science practice and need tuning per sport.
Decision support only, not a medical diagnosis.

## Future scope

Live wearable APIs (Garmin, Whoop), a mobile app, a model trained on real injury history, multi-season trends.
