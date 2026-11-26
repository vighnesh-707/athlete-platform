"""Athlete Workload & Injury-Risk Intelligence Platform - Streamlit dashboard.

Run:  streamlit run app.py
"""
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import csv
import io
import os
import re
import smtplib
from email.message import EmailMessage
from urllib.parse import quote

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import auth
import metrics
import risk_rules as rr
from recommendations import build_recommendations

st.set_page_config(page_title="Athlete Workload & Injury-Risk Platform", page_icon="🏃", layout="wide")

COL = {"RED": "#D64545", "AMBER": "#E0A100", "GREEN": "#2E9E5B"}
TEXT = {"RED": "HIGH RISK", "AMBER": "WATCH", "GREEN": "OK"}
LABEL = {"RED": "🔴 HIGH RISK", "AMBER": "🟠 WATCH", "GREEN": "🟢 OK"}
ACUTE, CHRONIC, BAR = "#2F6FDE", "#6B7280", "#D1D5DB"
REQUIRED = ["training", "matches", "wearable", "recovery", "medical"]
PAGES = ["🏠 Home", "👤 Athlete", "🔔 Alerts & actions", "🧪 What-if simulator", "ℹ️ How it works"]

st.markdown("""
<style>
.badge{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.78rem;font-weight:700;color:#fff;vertical-align:middle}
.hero{padding:1rem 1.3rem;border-radius:14px;background:rgba(47,111,222,.10);border:1px solid rgba(47,111,222,.35);margin-bottom:1rem}
.hero h3{margin:0 0 .3rem 0}
</style>
""", unsafe_allow_html=True)

# ---- login gate: nothing below runs until the user is signed in
USER = auth.require_login()
if USER["role"] == "Coach":
    PAGES.append("✉️ Send athlete suggestions")


def badge(s):
    fg = "#1F2937" if s == "AMBER" else "#fff"
    return f'<span class="badge" style="background:{COL[s]};color:{fg}">{TEXT[s]}</span>'


# ---------------------------------------------------------------- data
def extract_pdf_text(uploaded_pdf):
    reader = PdfReader(uploaded_pdf)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_section(text, section):
    pattern = rf"\[{section}\]\s*(.*?)(?=\n\s*\[[A-Za-z]+\]\s*|$)"
    match = re.search(pattern, text, flags=re.I | re.S)
    if not match:
        return None
    lines = [line.strip() for line in match.group(1).splitlines() if line.strip()]
    header_index = next((i for i, line in enumerate(lines)
                         if "athlete_id" in line.lower() and "," in line), None)
    if header_index is None:
        return None
    csv_text = "\n".join(lines[header_index:])
    csv_text = "\n".join(line for line in csv_text.splitlines()
                         if not re.match(r"^(Page|Athlete data file|Generated)", line, re.I))
    try:
        frame = pd.read_csv(io.StringIO(csv_text), skipinitialspace=True)
    except Exception:
        return None
    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    return frame


def read_pdf_upload(uploaded_pdf):
    if uploaded_pdf is None:
        return None, "Choose one PDF file."
    try:
        text = extract_pdf_text(uploaded_pdf)
    except Exception as exc:
        return None, f"Could not read the PDF: {exc}"

    got = {}
    missing = []
    for name in REQUIRED:
        frame = parse_section(text, name)
        if frame is None or frame.empty:
            missing.append(name)
        else:
            got[name] = frame
    if missing:
        return None, "Missing or unreadable section(s): " + ", ".join(missing)

    for name in REQUIRED[:4]:
        if "date" not in got[name].columns:
            return None, f"The [{name}] section needs a date column."
        got[name]["date"] = pd.to_datetime(got[name]["date"], errors="coerce")
        bad_dates = got[name]["date"].isna()
        if bad_dates.any():
            return None, f"The [{name}] section has invalid dates."

    for name in REQUIRED:
        if "athlete_id" not in got[name].columns:
            return None, f"The [{name}] section needs an athlete_id column."

    med = got["medical"]
    med["name"] = med["name"] if "name" in med.columns else med["athlete_id"]
    med["email"] = med["email"] if "email" in med.columns else ""
    for column, default in [("position", "-"), ("past_injury_count", 0), ("medical_notes", "No notes")]:
        if column not in med.columns:
            med[column] = default

    med["email"] = med["email"].fillna("").astype(str).str.strip()
    bad_emails = med["email"].apply(
        lambda value: bool(value) and not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value)
    )
    missing_emails = med.loc[med["email"] == "", ["athlete_id", "name"]]
    if bad_emails.any():
        bad_ids = ", ".join(med.loc[bad_emails, "athlete_id"].astype(str))
        return None, f"Invalid email address(es) for athlete(s): {bad_ids}"
    if not missing_emails.empty:
        missing_ids = ", ".join(missing_emails["athlete_id"].astype(str))
        return None, f"Email is required in the [medical] section for athlete(s): {missing_ids}"
    return got, ""


def build_pdf_template():
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 50
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(45, y, "Athlete Platform - Single PDF Data Template")
    y -= 28
    pdf.setFont("Helvetica", 9)
    instructions = [
        "Use the exact section names below. Put one CSV-style header and rows under each section.",
        "Use YYYY-MM-DD dates and ensure athlete_id is consistent in every section.",
        "[medical] must include email for every athlete.",
    ]
    for line in instructions:
        pdf.drawString(45, y, line)
        y -= 15

    sections = [
        ("[training]",
         "date,athlete_id,duration_min,rpe,distance_km",
         "2026-11-25,A01,75,6.5,7.2"),
        ("[matches]",
         "date,athlete_id,minutes_played",
         "2026-11-22,A01,68"),
        ("[wearable]",
         "date,athlete_id,hrv_ms,resting_hr",
         "2026-11-25,A01,52,62"),
        ("[recovery]",
         "date,athlete_id,sleep_hours,soreness,mood,fatigue",
         "2026-11-25,A01,7.5,4,7,4"),
        ("[medical]",
         "athlete_id,name,email,position,past_injury_count,medical_notes",
         "A01,Arjun Mehta,athlete@example.com,Forward,0,No significant history"),
    ]
    pdf.setFont("Helvetica-Bold", 11)
    for heading, header, example in sections:
        if y < 120:
            pdf.showPage()
            y = height - 50
            pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(45, y, heading)
        y -= 16
        pdf.setFont("Courier", 8)
        pdf.drawString(45, y, header)
        y -= 12
        pdf.drawString(45, y, example)
        y -= 24
        pdf.setFont("Helvetica-Bold", 11)
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def build_action_pdf(report, as_of):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 50
    pdf.setTitle(f"Athlete action plan {as_of}")
    pdf.setFont("Helvetica-Bold", 16)
    pdf.drawString(45, y, f"Athlete action plan - {as_of}")
    y -= 28
    pdf.setFont("Helvetica", 9)
    for item in report:
        lines = [
            f"Status: {item['status']}  Athlete: {item['athlete']}  Role: {item['role']}",
            f"Action: {item['action']}",
            f"Done: {'Yes' if item['done'] else 'No'}",
        ]
        for line in lines:
            if y < 70:
                pdf.showPage()
                y = height - 50
                pdf.setFont("Helvetica", 9)
            pdf.drawString(45, y, line[:150])
            y -= 13
        y -= 8
    pdf.save()
    buffer.seek(0)
    return buffer.getvalue()


def athlete_notification(row, rec):
    reasons = "; ".join(text for _, _, text in row["reasons"])
    coach_actions = "\n".join(f"- {text}" for text in rec["coach"])
    medical_actions = "\n".join(f"- {text}" for text in rec["medical"])
    subject = f"Your Athlete Platform high-risk action plan"
    body = (
        f"Hello {row['name']},\n\n"
        f"Your current athlete status is HIGH RISK as of {st.session_state.as_of_date}.\n\n"
        f"Why: {reasons}\n\n"
        f"Requirements to complete:\n\n"
        f"Coach requirements:\n{coach_actions or '- None'}\n\n"
        f"Medical requirements:\n{medical_actions or '- None'}\n\n"
        "Please follow these actions and contact your coach or medical staff if symptoms worsen.\n"
        "This is decision support, not a medical diagnosis.\n"
    )
    return subject, body


def send_notification_email(recipient, subject, body):
    host = os.getenv("SMTP_HOST")
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM", username)
    port = int(os.getenv("SMTP_PORT", "587"))
    if not host or not username or not password or not sender:
        return False, "SMTP is not configured. Use the email-app option instead."
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls()
            server.login(username, password)
            server.send_message(message)
        return True, f"Email sent to {recipient}"
    except Exception as exc:
        return False, f"Could not send email: {exc}"


@st.cache_data(show_spinner="Merging data sources and computing metrics...")
def run_pipeline(data):
    return rr.add_anomaly(metrics.build_daily(data))


# ---------------------------------------------------------------- state + callbacks
st.session_state.setdefault("page", PAGES[0])


def go_to(page, athlete=None):
    st.session_state.page = page
    if athlete:
        st.session_state.athlete_sel = athlete


def jump_days(days):
    st.session_state.as_of_date = st.session_state.dmax - pd.Timedelta(days=days)


if "welcomed" not in st.session_state:
    st.session_state.welcomed = True
    st.toast(f"Welcome, {USER['name'].split()[0]}! Click any athlete card to explore their data.", icon="👋")

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("## 🏃 Athlete Platform")
    auth.sidebar_user(USER)
    st.radio("Go to", PAGES, key="page", label_visibility="collapsed")
    st.divider()
    st.subheader("Data")
    st.warning("Public demo: upload sample data only. Do not upload real athlete emails, medical records, or performance data.")
    uploaded_pdf = st.file_uploader(
        "Upload one athlete data PDF",
        type="pdf",
        accept_multiple_files=False,
        help="The PDF must contain [training], [matches], [wearable], [recovery], and [medical] sections.",
    )
    data = None
    pdf_error = ""
    if uploaded_pdf:
        data, pdf_error = read_pdf_upload(uploaded_pdf)
        if pdf_error:
            st.error(pdf_error)
    st.download_button(
        "Download PDF template",
        build_pdf_template(),
        file_name="athlete-data-template.pdf",
        mime="application/pdf",
        width="stretch",
    )
if data is None:
    st.title("Athlete Workload & Injury-Risk Platform")
    st.info("Upload PDF to get suggestions for your athletes.")
    st.stop()

daily = run_pipeline(data)
medical = data["medical"]
DMIN, DMAX = daily["date"].min(), daily["date"].max()
MIN_DATE = min(DMIN + pd.Timedelta(days=28), DMAX)
st.session_state.dmax = DMAX.date()

cur = st.session_state.get("as_of_date")
if cur is None or not (MIN_DATE.date() <= cur <= DMAX.date()):
    st.session_state.as_of_date = DMAX.date()

with st.sidebar:
    st.subheader("Date")
    st.date_input("Status as of", min_value=MIN_DATE.date(), max_value=DMAX.date(), key="as_of_date",
                  help="Move back in time to see how risk built up.")
    b1, b2, b3 = st.columns(3)
    b1.button("Today", on_click=jump_days, args=(0,), width="stretch")
    b2.button("-2 wk", on_click=jump_days, args=(14,), width="stretch")
    b3.button("-4 wk", on_click=jump_days, args=(28,), width="stretch")
    with st.expander("💡 30-second guide"):
        st.markdown("1. **Home:** cards show who needs attention.\n"
                    "2. **Click a card** to see the full story.\n"
                    "3. **Alerts & actions:** tick off what staff should do.\n"
                    "4. **What-if:** test how cutting load changes risk.\n"
                    "5. Move the **date** back to watch risk build up.")

as_of = pd.Timestamp(st.session_state.as_of_date)
status = rr.team_status(daily, medical, as_of)
names = dict(zip(medical["athlete_id"], medical["name"]))
ids = list(status["athlete_id"])
if st.session_state.get("athlete_sel") not in ids:
    st.session_state.athlete_sel = ids[0]
n_red, n_amber = int((status["status"] == "RED").sum()), int((status["status"] == "AMBER").sum())


def style(fig, h=340):
    fig.update_layout(template="plotly_white", height=h, margin=dict(l=10, r=10, t=40, b=10),
                      legend=dict(orientation="h", y=1.12, x=0))
    return fig


def athlete_picker(label="Choose an athlete"):
    pick = st.selectbox(label, ids, index=ids.index(st.session_state.athlete_sel),
                        format_func=lambda i: f"{names[i]} ({i})")
    st.session_state.athlete_sel = pick
    return pick, status[status["athlete_id"] == pick].iloc[0]


def plain_summary(row, h):
    last = h.iloc[-1]
    parts = []
    if pd.notna(last["chronic"]) and last["chronic"] > 0:
        pct = (last["acute"] / last["chronic"] - 1) * 100
        if abs(pct) < 10:
            parts.append("Training load over the last 7 days is **in line** with their usual 4-week level.")
        else:
            parts.append(f"Training load over the last 7 days is **{abs(pct):.0f}% {'higher' if pct > 0 else 'lower'}** "
                         "than their usual 4-week level.")
    parts.append(f"Sleep averaged **{row['sleep3']:.1f} h** and soreness **{row['soreness3']:.0f}/10** over the last 3 days.")
    if pd.notna(row["hrv_dev3"]) and row["hrv_dev3"] < -0.10:
        parts.append(f"Heart-rate variability (a recovery signal) is **{abs(row['hrv_dev3']) * 100:.0f}% below** their own normal.")
    verdict = {"RED": "👉 **Needs attention today** - see the actions below.",
               "AMBER": "👉 **Keep an eye on this athlete** over the next few days.",
               "GREEN": "👉 **All good** - continue the planned training."}[row["status"]]
    return " ".join(parts) + "\n\n" + verdict


st.title("Athlete Workload & Injury-Risk Intelligence Platform")
page = st.session_state.page

# ================================================================ HOME
if page == PAGES[0]:
    flagged = status[status["status"] != "GREEN"]
    if flagged.empty:
        msg = "Everyone is within their normal workload and recovery range. 🎉"
    else:
        who = ", ".join(flagged["name"].str.split().str[0])
        msg = f"**{len(flagged)} of {len(status)} athletes need attention:** {who}. Click a card to see why."
    st.markdown(f'<div class="hero"><h3>{as_of:%A, %d %B %Y}</h3>{msg}</div>', unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Athletes monitored", len(status))
    c2.metric("🔴 High risk", n_red, help="Needs action today")
    c3.metric("🟠 Watch", n_amber, help="Monitor closely")
    c4.metric("Average readiness", f"{status['readiness'].mean():.0f} / 100",
              help="100 = fully fresh. Combines HRV, sleep, soreness, fatigue, resting HR and workload.")

    st.subheader("Your squad")
    f1, f2, f3 = st.columns(3)
    show = f1.multiselect("Show status", ["RED", "AMBER", "GREEN"], default=["RED", "AMBER", "GREEN"],
                          format_func=lambda s: LABEL[s])
    pos = f2.multiselect("Position", sorted(status["position"].dropna().unique()))
    q = f3.text_input("Search athlete", placeholder="Type a name...")
    view = status[status["status"].isin(show)]
    if pos:
        view = view[view["position"].isin(pos)]
    if q:
        view = view[view["name"].str.contains(q, case=False)]

    if view.empty:
        st.info("No athletes match these filters.")
    for start in range(0, len(view), 3):
        cols = st.columns(3)
        for col, (_, r) in zip(cols, view.iloc[start:start + 3].iterrows()):
            with col.container(border=True):
                st.markdown(f"**{r['name']}** &nbsp; {badge(r['status'])}<br>"
                            f"<span style='opacity:.65'>{r['position']}</span>", unsafe_allow_html=True)
                st.progress(int(r["readiness"]), text=f"Readiness {r['readiness']:.0f}/100")
                m1, m2 = st.columns(2)
                m1.metric("Workload (ACWR)", f"{r['acwr']:.2f}" if pd.notna(r["acwr"]) else "-",
                          help="Last 7 days of load vs last 28 days. 0.8-1.3 is the sweet spot.")
                m2.metric("Sleep (3-day)", f"{r['sleep3']:.1f} h")
                st.caption(r["reasons"][0][2] if r["reasons"] else "All indicators normal.")
                st.button("View details →", key=f"open_{r['athlete_id']}", on_click=go_to,
                          args=(PAGES[1], r["athlete_id"]), width="stretch")

    with st.expander("📊 Squad charts", expanded=True):
        left, right = st.columns(2)
        with left:
            fig = go.Figure()
            fig.add_vrect(x0=0.8, x1=1.3, fillcolor=COL["GREEN"], opacity=0.10, line_width=0,
                          annotation_text="Sweet spot", annotation_position="top left")
            fig.add_vrect(x0=1.5, x1=3, fillcolor=COL["RED"], opacity=0.10, line_width=0,
                          annotation_text="High risk", annotation_position="top right")
            for s in ["GREEN", "AMBER", "RED"]:
                sub = status[status["status"] == s]
                fig.add_trace(go.Scatter(x=sub["acwr"], y=sub["readiness"], mode="markers+text", name=LABEL[s],
                                         text=sub["name"].str.split().str[0], textposition="top center",
                                         customdata=sub["name"],
                                         hovertemplate="%{customdata}<br>ACWR %{x:.2f}<br>Readiness %{y:.0f}<extra></extra>",
                                         marker=dict(size=13, color=COL[s], line=dict(width=2, color="white"))))
            fig.update_xaxes(title="Workload ratio (ACWR)", range=[0.4, max(2.0, status["acwr"].max() + 0.2)])
            fig.update_yaxes(title="Readiness (0-100)", range=[0, 105])
            fig.update_layout(title="Who is where: workload vs readiness")
            st.plotly_chart(style(fig, 400), width="stretch")
        with right:
            last28 = daily[(daily["date"] <= as_of) & (daily["date"] > as_of - pd.Timedelta(days=28))]
            pv = last28.pivot(index="athlete_id", columns="date", values="readiness").loc[ids]
            hm = go.Figure(go.Heatmap(z=pv.values, x=pv.columns.strftime("%d %b"), y=[names[i] for i in pv.index],
                                      colorscale="Blues", zmin=0, zmax=100, colorbar=dict(title="Readiness"),
                                      hovertemplate="%{y}<br>%{x}: %{z:.0f}<extra></extra>"))
            hm.update_yaxes(autorange="reversed")
            hm.update_layout(title="Readiness, last 28 days (darker = fresher)")
            st.plotly_chart(style(hm, 400), width="stretch")

    with st.expander("📋 See as a table"):
        table = pd.DataFrame({
            "Status": status["status"].map(LABEL), "Athlete": status["name"], "Position": status["position"],
            "ACWR": status["acwr"].round(2), "Readiness": status["readiness"],
            "Sleep (3-day, h)": status["sleep3"].round(1), "Soreness (3-day)": status["soreness3"].round(1),
            "Main reason": status["reasons"].apply(lambda r: r[0][2] if r else "-")})
        st.dataframe(table, hide_index=True, width="stretch",
                     column_config={"Readiness": st.column_config.ProgressColumn("Readiness", min_value=0,
                                                                                  max_value=100, format="%.0f")})

# ================================================================ ATHLETE
elif page == PAGES[1]:
    pick, row = athlete_picker()
    h_all = daily[(daily["athlete_id"] == pick) & (daily["date"] <= as_of)]
    st.markdown(f"## {row['name']} &nbsp; {badge(row['status'])}", unsafe_allow_html=True)
    st.caption(f"{row['position']} · Medical file: {row['medical_notes']}")
    st.info(plain_summary(row, h_all), icon="💬")

    squad = status["readiness"].mean()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Workload (ACWR)", f"{row['acwr']:.2f}", help="Sweet spot is 0.8-1.3; above 1.5 is high risk.")
    m2.metric("Readiness", f"{row['readiness']:.0f} / 100", delta=f"{row['readiness'] - squad:+.0f} vs squad average")
    m3.metric("Sleep (3-day avg)", f"{row['sleep3']:.1f} h")
    m4.metric("Soreness (3-day avg)", f"{row['soreness3']:.1f} / 10")

    o1, o2 = st.columns([2, 1])
    span = o1.radio("Time range", ["Last 30 days", "Last 60 days", "All data"], index=1, horizontal=True)
    compare = o2.toggle("Compare readiness with squad average")
    n = {"Last 30 days": 30, "Last 60 days": 60}.get(span, len(h_all))
    h = h_all.tail(n)

    g1, g2 = st.columns([2, 1])
    with g1:
        f = go.Figure()
        f.add_bar(x=h["date"], y=h["daily_load"], name="Daily load", marker_color=BAR)
        f.add_scatter(x=h["date"], y=h["acute"], name="Acute (7-day avg)", line=dict(color=ACUTE, width=2))
        f.add_scatter(x=h["date"], y=h["chronic"], name="Chronic (28-day avg)", line=dict(color=CHRONIC, width=2))
        f.update_layout(title="Training load (duration x effort)", hovermode="x unified")
        st.plotly_chart(style(f), width="stretch")
    with g2:
        gauge = go.Figure(go.Indicator(
            mode="gauge+number", value=row["readiness"], title={"text": "Readiness today"},
            gauge=dict(axis=dict(range=[0, 100]), bar=dict(color="#374151"),
                       steps=[dict(range=[0, 40], color="#F3B4B4"), dict(range=[40, 60], color="#F6DE9A"),
                              dict(range=[60, 100], color="#B7E0C6")])))
        st.plotly_chart(style(gauge), width="stretch")

    g3, g4 = st.columns(2)
    with g3:
        z = go.Figure()
        for y0, y1, c in [(0, .8, "AMBER"), (.8, 1.3, "GREEN"), (1.3, 1.5, "AMBER"), (1.5, 3, "RED")]:
            z.add_hrect(y0=y0, y1=y1, fillcolor=COL[c], opacity=0.12, line_width=0)
        z.add_scatter(x=h["date"], y=h["acwr"], name="ACWR", line=dict(color="#111827", width=2))
        z.update_yaxes(range=[0, max(2.0, float(h["acwr"].max()) + 0.2)], title="ACWR")
        z.update_layout(title="Injury-risk zone (green = safe, red = high risk)", showlegend=False, hovermode="x unified")
        st.plotly_chart(style(z), width="stretch")
    with g4:
        r = go.Figure()
        r.add_scatter(x=h["date"], y=h["readiness"], name=row["name"], line=dict(color=ACUTE, width=2))
        if compare:
            avg = daily.groupby("date")["readiness"].mean().reindex(h["date"])
            r.add_scatter(x=h["date"], y=avg.values, name="Squad average", line=dict(color=CHRONIC, width=2, dash="dot"))
        r.update_yaxes(range=[0, 100], title="Readiness")
        r.update_layout(title="Readiness trend", showlegend=compare, hovermode="x unified")
        st.plotly_chart(style(r), width="stretch")

    g5, g6 = st.columns(2)
    with g5:
        w = go.Figure()
        w.add_scatter(x=h["date"], y=h["soreness"], name="Soreness", line=dict(color=COL["RED"], width=2))
        w.add_scatter(x=h["date"], y=h["fatigue"], name="Fatigue", line=dict(color=CHRONIC, width=2, dash="dot"))
        w.update_yaxes(range=[0, 10], title="Self-report (1-10)")
        w.update_layout(title="Soreness and fatigue (self-reported)", hovermode="x unified")
        st.plotly_chart(style(w), width="stretch")
    with g6:
        s = go.Figure()
        s.add_scatter(x=h["date"], y=h["hrv_ms"], name="HRV (ms)", line=dict(color=ACUTE, width=2))
        s.add_scatter(x=h["date"], y=h["hrv_base"], name="Own 28-day baseline", line=dict(color=CHRONIC, width=2, dash="dot"))
        s.update_yaxes(title="HRV (ms)")
        s.update_layout(title="Heart-rate variability vs own baseline", hovermode="x unified")
        st.plotly_chart(style(s), width="stretch")

    w1, w2 = st.columns(2)
    with w1:
        st.subheader("Why this status?")
        if row["reasons"]:
            for sev, _, text in row["reasons"]:
                st.write(f"- {LABEL[sev].split()[0]} {text}")
        else:
            st.write("- 🟢 All indicators are within this athlete's normal range.")
    with w2:
        st.subheader("What to do")
        rec = build_recommendations(row)
        st.markdown("**Coach**\n" + "\n".join(f"- {x}" for x in rec["coach"]))
        st.markdown("**Medical staff**\n" + "\n".join(f"- {x}" for x in rec["medical"]))
    st.button("🧪 Test a training change for this athlete", on_click=go_to, args=(PAGES[3], pick))

# ================================================================ ALERTS & ACTIONS
elif page == PAGES[2]:
    st.header("🔔 Alerts & actions")
    flagged = status[status["status"] != "GREEN"]
    if flagged.empty:
        st.success("No alerts. All athletes are within normal workload and recovery ranges.")
    else:
        role = st.radio("Show actions for", ["Coach", "Medical staff", "Both"], horizontal=True,
                        index=1 if USER["role"] == "Medical staff" else 0)
        roles = {"Coach": ["coach"], "Medical staff": ["medical"], "Both": ["coach", "medical"]}[role]
        plan, keys = {}, []
        for _, r in flagged.iterrows():
            rec = build_recommendations(r)
            plan[r["athlete_id"]] = []
            for rl in roles:
                for i, txt in enumerate(rec[rl]):
                    k = f"done|{as_of.date()}|{r['athlete_id']}|{rl}|{i}"
                    plan[r["athlete_id"]].append((k, rl, txt))
                    keys.append(k)
        done = sum(bool(st.session_state.get(k, False)) for k in keys)
        st.progress(done / max(1, len(keys)), text=f"{done} of {len(keys)} actions completed")
        if keys and done == len(keys):
            st.success("All actions completed. 🎉")

        report = []
        for _, r in flagged.iterrows():
            with st.expander(f"{LABEL[r['status']]} - {r['name']}", expanded=r["status"] == "RED"):
                st.markdown("**Why flagged**\n" + "\n".join(f"- {t}" for _, _, t in r["reasons"]))
                st.markdown("**Actions** (tick when done)")
                for k, rl, txt in plan[r["athlete_id"]]:
                    st.checkbox(f"**{'Coach' if rl == 'coach' else 'Medical'}:** {txt}", key=k)
                    report.append(dict(athlete=r["name"], status=r["status"], role=rl, action=txt,
                                       done=bool(st.session_state.get(k, False))))
                st.button("Open athlete page →", key=f"al_{r['athlete_id']}", on_click=go_to,
                          args=(PAGES[1], r["athlete_id"]))
                if r["status"] == "RED":
                    rec = build_recommendations(r)
                    subject, body = athlete_notification(r, rec)
                    email = str(r.get("email", "")).strip()
                    if email:
                        col_send, col_draft = st.columns(2)
                        if col_send.button("Send athlete email", key=f"send_email_{r['athlete_id']}", width="stretch"):
                            sent, message = send_notification_email(email, subject, body)
                            (st.success if sent else st.warning)(message)
                        mailto = f"mailto:{quote(email)}?subject={quote(subject)}&body={quote(body)}"
                        col_draft.link_button("Open email draft", mailto, width="stretch")
                    else:
                        st.warning("No athlete email is available in the uploaded PDF.")
        st.download_button("⬇️ Download action plan (PDF)", build_action_pdf(report, as_of.date()),
                           file_name=f"action_plan_{as_of.date()}.pdf", mime="application/pdf")
    st.caption("Decision support only - final decisions stay with the coaching and medical staff.")

# ================================================================ WHAT-IF
elif page == PAGES[3]:
    st.header("🧪 What-if simulator")
    st.write("Choose an athlete and change their training load. See how the workload ratio (ACWR) "
             "would move over the next few days compared with doing nothing.")
    pick, row = athlete_picker()
    series = daily[(daily["athlete_id"] == pick) & (daily["date"] <= as_of)].set_index("date")["daily_load"]
    typical = float(series.tail(7).mean())

    s1, s2 = st.columns(2)
    change = s1.slider("Change in daily training load", -80, 50, -30, step=5, format="%+d%%",
                       help="Negative = reduce load, positive = increase.")
    days = s2.slider("For how many days", 1, 14, 5)

    def project(ch):
        vals = np.concatenate([series.values, np.full(days, typical * (1 + ch / 100))])
        s = pd.Series(vals)
        return (s.rolling(7, min_periods=7).mean() / s.rolling(28, min_periods=14).mean()).values

    base, plan = project(0), project(change)
    now, end_base, end_plan = base[len(series) - 1], base[-1], plan[-1]
    m1, m2, m3 = st.columns(3)
    m1.metric("ACWR today", f"{now:.2f}")
    m2.metric(f"In {days} days: no change", f"{end_base:.2f}")
    m3.metric(f"In {days} days: your plan", f"{end_plan:.2f}", delta=f"{end_plan - end_base:+.2f} vs no change",
              delta_color="inverse")

    if end_plan > 1.5:
        st.error("Still in the high-risk zone (above 1.5). Reduce more, or extend the number of days.", icon="🔴")
    elif end_plan > 1.3:
        st.warning("Better, but still in the caution zone (1.3-1.5).", icon="🟠")
    elif end_plan >= 0.8:
        st.success("Back in the sweet spot (0.8-1.3).", icon="🟢")
    else:
        st.warning("Load would become too low (below 0.8). Rebuild gradually.", icon="🟠")

    needed = next((c for c in range(0, -81, -5) if project(c)[-1] <= 1.3), None)
    if now <= 1.3:
        st.caption("This athlete is already at or below 1.3, so no reduction is needed.")
    elif needed is None:
        st.caption(f"Even an 80% cut for {days} days is not enough to reach 1.3. Try more days.")
    else:
        st.caption(f"💡 A cut of about **{abs(needed)}%** for {days} days brings ACWR to 1.3 or below.")

    k = min(14, len(series))
    past_x = list(series.index[-k:])
    fut_x = [series.index[-1] + pd.Timedelta(days=i) for i in range(1, days + 1)]
    fig = go.Figure()
    for y0, y1, c in [(0, .8, "AMBER"), (.8, 1.3, "GREEN"), (1.3, 1.5, "AMBER"), (1.5, 3, "RED")]:
        fig.add_hrect(y0=y0, y1=y1, fillcolor=COL[c], opacity=0.12, line_width=0)
    fig.add_scatter(x=past_x, y=base[len(series) - k:len(series)], name="Actual", line=dict(color="#111827", width=2))
    join = [past_x[-1]] + fut_x
    fig.add_scatter(x=join, y=base[len(series) - 1:], name="No change", line=dict(color=CHRONIC, width=2, dash="dot"))
    fig.add_scatter(x=join, y=plan[len(series) - 1:], name="Your plan", line=dict(color=ACUTE, width=3))
    fig.update_yaxes(range=[0, max(2.0, float(np.nanmax(base[-k - days:])) + 0.2)], title="ACWR")
    fig.update_layout(title=f"Projected ACWR for {row['name']}", hovermode="x unified")
    st.plotly_chart(style(fig, 400), width="stretch")
    st.caption("Projection assumes every future day has the same load: the athlete's average of the last 7 days, "
               "adjusted by your chosen change. Real sessions will vary.")

# ================================================================ HOW IT WORKS
elif page == PAGES[4]:
    st.header("ℹ️ How it works")
    st.markdown("""
**The problem:** coaches hold training logs, match stats, wearable readings, recovery surveys and medical notes in separate
places, so a slow build-up of fatigue is easy to miss.

**What this platform does**
1. **Consolidates** the 5 sources into one table (one row per athlete per day).
2. **Measures** each athlete against *their own* normal, not a squad average.
3. **Flags** abnormal patterns with transparent rules, plus an AI model as a second opinion.
4. **Recommends** practical actions for coaches and medical staff.
""")
    with st.expander("What is ACWR? (the main risk number)", expanded=True):
        st.markdown("""
**ACWR** = average load of the last **7 days** ÷ average load of the last **28 days**.
It compares what the athlete did this week with what their body is used to.

| ACWR | Meaning |
|---|---|
| below 0.8 | Under-loaded, rebuild gradually |
| **0.8 - 1.3** | **Sweet spot** |
| 1.3 - 1.5 | Caution |
| above 1.5 | **High injury risk** |

Session load = duration (min) × effort (RPE 1-10). Match load = minutes × 7.5.
""")
    with st.expander("What is the readiness score?"):
        st.markdown("""
A 0-100 score where 100 is fully fresh. It starts at 100 and loses points for:
HRV drop (up to 25), poor sleep (20), soreness (20), fatigue (15), raised resting heart rate (10) and high ACWR (10).
HRV and resting heart rate are compared with the athlete's own 28-day baseline.
""")
    with st.expander("How are athletes flagged?"):
        st.markdown("""
- **Red:** ACWR above 1.5, readiness below 40, poor sleep and high soreness together, or 3+ warning signs at once.
- **Amber:** one warning sign (ACWR 1.3-1.5 or below 0.8, readiness 40-60, HRV down 15%, sleep under 6 h,
  soreness 7+, monotony above 2.5).
- **AI layer:** an Isolation Forest highlights days that look unusual compared with the squad.
- Every flag shows its reason, so staff can trust it and challenge it.
""")
    with st.expander("Limitations"):
        st.markdown("""
- The demo uses **synthetic data** with planted scenarios. Thresholds follow published sports-science practice
  and should be tuned per sport and team.
- This is decision support, not a medical diagnosis.
""")

# ================================================================ COACH MESSAGES
else:
    st.header("✉️ Send suggestions to high-risk athletes")
    st.write("Review each athlete's risk reasons and suggested actions, add a personal note if needed, then send the details by email.")
    high_risk = status[status["status"] == "RED"]
    if high_risk.empty:
        st.success("There are no high-risk athletes to contact right now.")
    else:
        st.caption("Messages include the athlete's high-risk reasons plus suggested coach and medical actions.")
        for _, athlete in high_risk.iterrows():
            rec = build_recommendations(athlete)
            email = str(athlete.get("email", "")).strip()
            with st.container(border=True):
                st.subheader(f"{athlete['name']} · {email or 'No email provided'}")
                st.markdown("**Why they are flagged**")
                if athlete["reasons"]:
                    st.markdown("\n".join(f"- {reason}" for _, _, reason in athlete["reasons"]))
                else:
                    st.write("Review the current risk indicators with staff.")
                st.markdown("**Suggested coach actions**")
                st.markdown("\n".join(f"- {action}" for action in rec["coach"]) or "- No coach actions suggested.")
                st.markdown("**Suggested medical actions**")
                st.markdown("\n".join(f"- {action}" for action in rec["medical"]) or "- No medical actions suggested.")
                note = st.text_area(
                    "Personal message from coach (optional)",
                    key=f"coach_note_{athlete['athlete_id']}",
                    placeholder="Add context or instructions for this athlete.",
                )
                subject, body = athlete_notification(athlete, rec)
                if note.strip():
                    body += f"\nMessage from your coach:\n{note.strip()}\n"
                if email:
                    send_col, draft_col = st.columns(2)
                    if send_col.button("Send email", key=f"coach_send_{athlete['athlete_id']}", width="stretch"):
                        sent, message = send_notification_email(email, subject, body)
                        (st.success if sent else st.warning)(message)
                    mailto = f"mailto:{quote(email)}?subject={quote(subject)}&body={quote(body)}"
                    draft_col.link_button("Open email draft", mailto, width="stretch")
                else:
                    st.warning("Add a valid athlete email to the uploaded PDF before sending.")
    st.caption("These are decision-support suggestions, not a medical diagnosis. Review them before sending.")
