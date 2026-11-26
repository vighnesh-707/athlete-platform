"""Login / signup for coaches and medical staff.

- Users are stored in a local SQLite file (users.db), created automatically.
- Passwords are NEVER stored as plain text: PBKDF2-SHA256 with a random salt per user.
- 5 wrong passwords lock the account for 60 seconds (slows down guessing).
- A demo account is created on first run so the presentation never depends on signing up live.
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

import streamlit as st

DB_PATH = Path(os.environ.get("AUTH_DB", Path(__file__).parent / "users.db"))
ITERATIONS = 200_000
MAX_FAILS, LOCK_SECONDS = 5, 60
ROLES = ["Coach", "Medical staff"]
DEMO = {"username": "coach", "password": "Coach@123", "name": "Demo Coach", "role": "Coach"}


# ---------------------------------------------------------------- database
def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, team TEXT,
        pw_hash TEXT NOT NULL, failed INTEGER DEFAULT 0, locked_until REAL DEFAULT 0,
        created REAL NOT NULL)""")
    return con


def _hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2${ITERATIONS}${salt.hex()}${h.hex()}"


def _verify(password: str, stored: str) -> bool:
    _, iters, salt, h = stored.split("$")
    test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
    return hmac.compare_digest(test.hex(), h)


def ensure_demo_user():
    with _conn() as con:
        if not con.execute("SELECT 1 FROM users WHERE username=?", (DEMO["username"],)).fetchone():
            con.execute("INSERT INTO users VALUES (?,?,?,?,?,0,0,?)",
                        (DEMO["username"], DEMO["name"], DEMO["role"], "Demo FC", _hash(DEMO["password"]), time.time()))


def signup(name, username, role, team, password, confirm):
    """Returns (ok, message)."""
    name, username, team = name.strip(), username.strip().lower(), team.strip()
    if len(name) < 2:
        return False, "Please enter your full name."
    if not re.fullmatch(r"[a-z0-9_.]{3,20}", username):
        return False, "Username must be 3-20 characters: letters, numbers, dot or underscore."
    if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        return False, "Password must be at least 8 characters and include a letter and a number."
    if password != confirm:
        return False, "Passwords do not match."
    try:
        with _conn() as con:
            con.execute("INSERT INTO users VALUES (?,?,?,?,?,0,0,?)",
                        (username, name, role, team, _hash(password), time.time()))
    except sqlite3.IntegrityError:
        return False, "That username is already taken. Try another one."
    return True, "Account created! You can log in now."


def login(username, password):
    """Returns (user_dict | None, message)."""
    username = username.strip().lower()
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            _hash(password)  # same work as a real check, so timing doesn't reveal valid usernames
            return None, "Wrong username or password."
        if row["locked_until"] > time.time():
            return None, f"Too many attempts. Try again in {int(row['locked_until'] - time.time()) + 1} seconds."
        if _verify(password, row["pw_hash"]):
            con.execute("UPDATE users SET failed=0, locked_until=0 WHERE username=?", (username,))
            return dict(username=row["username"], name=row["name"], role=row["role"], team=row["team"]), ""
        fails = row["failed"] + 1
        if fails >= MAX_FAILS:
            con.execute("UPDATE users SET failed=0, locked_until=? WHERE username=?", (time.time() + LOCK_SECONDS, username))
            return None, f"Too many wrong attempts. Account locked for {LOCK_SECONDS} seconds."
        con.execute("UPDATE users SET failed=? WHERE username=?", (fails, username))
        return None, f"Wrong username or password. ({MAX_FAILS - fails} attempts left)"


# ---------------------------------------------------------------- UI
def _demo_login():
    user, _ = login(DEMO["username"], DEMO["password"])
    if user:
        st.session_state.user = user


def require_login() -> dict:
    """Show the login/signup page until the user is signed in. Returns the user dict."""
    ensure_demo_user()
    if "user" in st.session_state:
        return st.session_state.user

    _, mid, _ = st.columns([1, 1.6, 1])
    with mid:
        st.markdown("# 🏃 Athlete Workload & Injury-Risk Platform")
        st.markdown('<div class="hero"><h3>Welcome, coach 👋</h3>'
                    'Log in to see who is at risk today and what to do about it.</div>', unsafe_allow_html=True)
        tab_in, tab_up = st.tabs(["🔑 Log in", "📝 Sign up"])

        with tab_in:
            with st.form("login_form"):
                u = st.text_input("Username")
                p = st.text_input("Password", type="password")
                go = st.form_submit_button("Log in", type="primary", width="stretch")
            if go:
                if not u or not p:
                    st.error("Enter both username and password.")
                else:
                    user, msg = login(u, p)
                    if user:
                        st.session_state.user = user
                        st.rerun()
                    st.error(msg)
            st.divider()
            st.button("🎯 Try the demo account", on_click=_demo_login, width="stretch",
                      help=f"Logs in as '{DEMO['username']}' - for presentations")
            st.caption(f"Demo login: **{DEMO['username']}** / **{DEMO['password']}**")

        with tab_up:
            with st.form("signup_form"):
                name = st.text_input("Full name")
                c1, c2 = st.columns(2)
                username = c1.text_input("Choose a username")
                role = c2.selectbox("I am a", ROLES)
                team = st.text_input("Team / club (optional)")
                p1 = st.text_input("Password", type="password", help="At least 8 characters, with a letter and a number")
                p2 = st.text_input("Confirm password", type="password")
                go = st.form_submit_button("Create account", type="primary", width="stretch")
            if go:
                ok, msg = signup(name, username, role, team, p1, p2)
                (st.success if ok else st.error)(msg)
    st.stop()


def sidebar_user(user: dict):
    """Small user card + logout button for the sidebar."""
    with st.container(border=True):
        st.markdown(f"👤 **{user['name']}**  \n<span style='opacity:.65'>{user['role']}"
                    f"{' · ' + user['team'] if user.get('team') else ''}</span>", unsafe_allow_html=True)
        st.button("Log out", on_click=lambda: st.session_state.clear(), width="stretch")
