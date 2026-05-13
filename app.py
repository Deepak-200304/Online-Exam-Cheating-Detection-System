# app.py
import os
import time
import threading
import base64
from datetime import datetime
import io

import streamlit as st
import cv2
import numpy as np
import psycopg2
from psycopg2.extras import RealDictCursor
import bcrypt

# Optional JS bridge for tab visibility detection
try:
    from streamlit_javascript import st_javascript
    JS_AVAILABLE = True
except Exception:
    JS_AVAILABLE = False

st.set_page_config(page_title="Proctoring App", layout="wide")

# -----------------------
# Configuration
# -----------------------
DB_URL = os.getenv("DATABASE_URL")  # Railway Postgres connection string
SCREENSHOT_DIR = "screenshots"
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Haar cascades (bundled with opencv-python)
face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_eye.xml")

# -----------------------
# Database helpers
# -----------------------
def get_db_conn():
    if not DB_URL:
        return None
    return psycopg2.connect(DB_URL)

def ensure_tables():
    """
    Create minimal users and logs tables if they don't exist.
    If you already have tables on Railway, this will not overwrite them.
    Expected users table columns: id, username, password_hash, created_at
    Expected logs table columns: id, username, event_type, details, screenshot_path, created_at
    """
    conn = get_db_conn()
    if conn is None:
        return
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id SERIAL PRIMARY KEY,
        username TEXT,
        event_type TEXT NOT NULL,
        details TEXT,
        screenshot_path TEXT,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)
    conn.commit()
    cur.close()
    conn.close()

def create_user_db(username, password_plain):
    conn = get_db_conn()
    if conn is None:
        return False, "DATABASE_URL not configured."
    try:
        pw_hash = bcrypt.hashpw(password_plain.encode(), bcrypt.gensalt()).decode()
        cur = conn.cursor()
        cur.execute("INSERT INTO users (username, password_hash) VALUES (%s, %s);", (username, pw_hash))
        conn.commit()
        cur.close()
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)

def verify_user_db(username, password_plain):
    conn = get_db_conn()
    if conn is None:
        return False, "DATABASE_URL not configured."
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM users WHERE username=%s;", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()
        if not user:
            return False, "User not found."
        stored_hash = user["password_hash"].encode()
        ok = bcrypt.checkpw(password_plain.encode(), stored_hash)
        return ok, None if ok else "Invalid password."
    except Exception as e:
        return False, str(e)

def insert_log(username, event_type, details=None, screenshot_path=None):
    conn = get_db_conn()
    if conn is None:
        return False, "DATABASE_URL not configured."
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO logs (username, event_type, details, screenshot_path) VALUES (%s, %s, %s, %s);",
            (username, event_type, details, screenshot_path)
        )
        conn.commit()
        cur.close()
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)

# Ensure tables exist (safe if they already do)
ensure_tables()

# -----------------------
# Session state defaults
# -----------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None
if "proctoring" not in st.session_state:
    st.session_state.proctoring = False
if "violation_count" not in st.session_state:
    st.session_state.violation_count = 0
if "alert_messages" not in st.session_state:
    st.session_state.alert_messages = []
if "last_preview" not in st.session_state:
    st.session_state.last_preview = None
if "last_screenshot" not in st.session_state:
    st.session_state.last_screenshot = None

# -----------------------
# Utility: screenshots & alerts
# -----------------------
def save_screenshot(frame, reason):
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_reason = reason.replace(" ", "_")
    filename = os.path.join(SCREENSHOT_DIR, f"screenshot_{ts}_{safe_reason}.png")
    cv2.imwrite(filename, frame)
    st.session_state.last_screenshot = filename
    return filename

def record_violation(username, reason, frame=None, details=None):
    st.session_state.violation_count += 1
    msg = f"{datetime.utcnow().isoformat()} - {reason}"
    st.session_state.alert_messages.append(msg)
    screenshot_path = None
    if frame is not None:
        screenshot_path = save_screenshot(frame, reason)
    else:
        # create a blank image as placeholder
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        screenshot_path = save_screenshot(blank, reason)
    # insert into DB logs table if available
    insert_log(username, reason, details or "", screenshot_path)
    # show UI warning
    st.warning(msg)

# -----------------------
# Camera / detection thread
# -----------------------
def camera_loop(username):
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        st.session_state.alert_messages.append("Unable to open webcam.")
        return

    last_face_seen = time.time()
    face_missing_threshold = 3.0  # seconds without face -> violation
    eye_missing_threshold = 2.0   # seconds without eyes -> violation

    while st.session_state.proctoring:
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.1)
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, 1.3, 5)

        # draw rectangles for preview
        for (x, y, w, h) in faces:
            cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)
            roi_gray = gray[y:y+h, x:x+w]
            roi_color = frame[y:y+h, x:x+w]
            eyes = eye_cascade.detectMultiScale(roi_gray)
            for (ex, ey, ew, eh) in eyes:
                cv2.rectangle(roi_color, (ex, ey), (ex+ew, ey+eh), (255, 0, 0), 2)

        # detection logic
        if len(faces) == 0:
            if time.time() - last_face_seen > face_missing_threshold:
                record_violation(username, "No face detected", frame=frame)
                last_face_seen = time.time()
        else:
            last_face_seen = time.time()
            # check eyes in first face
            (x, y, w, h) = faces[0]
            roi_gray = gray[y:y+h, x:x+w]
            eyes = eye_cascade.detectMultiScale(roi_gray)
            if len(eyes) == 0:
                # small debounce to avoid false positives
                if time.time() - last_face_seen > eye_missing_threshold:
                    record_violation(username, "Eyes not detected / possibly closed", frame=frame)

        # update preview (store as base64 HTML for main thread)
        _, jpeg = cv2.imencode('.jpg', frame)
        b64 = base64.b64encode(jpeg.tobytes()).decode()
        img_html = f"<img src='data:image/jpg;base64,{b64}' width='640' />"
        st.session_state.last_preview = img_html

        time.sleep(0.12)

    cap.release()

# -----------------------
# UI: Login / Signup / Controls
# -----------------------
st.title("Online Exam Proctoring")

menu = st.sidebar.selectbox("Menu", ["Login", "Signup", "About"])

if menu == "Signup":
    st.header("Create an account")
    new_user = st.text_input("Username", key="su_user")
    new_pass = st.text_input("Password", type="password", key="su_pass")
    if st.button("Create account"):
        if not new_user or not new_pass:
            st.error("Provide both username and password.")
        else:
            ok, err = create_user_db(new_user.strip(), new_pass)
            if ok:
                st.success("Account created. Please login.")
            else:
                st.error(f"Error creating account: {err}")

elif menu == "Login":
    st.header("Login")
    username = st.text_input("Username", key="li_user")
    password = st.text_input("Password", type="password", key="li_pass")
    if st.button("Login"):
        if not username or not password:
            st.error("Provide both username and password.")
        else:
            ok, err = verify_user_db(username.strip(), password)
            if ok:
                st.session_state.logged_in = True
                st.session_state.username = username.strip()
                st.success("Logged in.")
            else:
                st.error(f"Login failed: {err}")

    if st.session_state.logged_in:
        st.info(f"Signed in as **{st.session_state.username}**")

        st.subheader("Proctoring Controls")
        col1, col2, col3 = st.columns([1,1,2])
        with col1:
            if st.button("Start Proctoring"):
                if not st.session_state.proctoring:
                    st.session_state.proctoring = True
                    t = threading.Thread(target=camera_loop, args=(st.session_state.username,), daemon=True)
                    t.start()
                    st.success("Proctoring started.")
                else:
                    st.info("Proctoring already running.")
        with col2:
            if st.button("Stop Proctoring"):
                if st.session_state.proctoring:
                    st.session_state.proctoring = False
                    st.success("Proctoring stopped.")
                else:
                    st.info("Proctoring is not running.")
        with col3:
            st.markdown("**Violations**")
            st.write(st.session_state.violation_count)
            if st.session_state.alert_messages:
                for m in reversed(st.session_state.alert_messages[-10:]):
                    st.write("- " + m)

        st.markdown("**Live Preview**")
        preview_placeholder = st.empty()
        if st.session_state.last_preview:
            preview_placeholder.markdown(st.session_state.last_preview, unsafe_allow_html=True)
        else:
            preview_placeholder.info("Camera preview will appear here after starting proctoring.")

        st.markdown("**Tab / Window visibility detection**")
        if JS_AVAILABLE:
            js_code = """
            const callback = () => ({hidden: document.hidden, ts: Date.now()});
            document.addEventListener('visibilitychange', () => {
                const val = callback();
                val;
            });
            ({hidden: document.hidden, ts: Date.now()});
            """
            try:
                js_val = st_javascript(js_code, key=f"tabvis_{st.session_state.username}")
                if js_val and isinstance(js_val, dict) and js_val.get("hidden", False):
                    # tab switched away
                    # try to decode last preview to frame
                    frame = None
                    if st.session_state.last_preview:
                        try:
                            html = st.session_state.last_preview
                            b64 = html.split("base64,")[1].split("'")[0]
                            jpg = base64.b64decode(b64)
                            arr = np.frombuffer(jpg, dtype=np.uint8)
                            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                        except Exception:
                            frame = None
                    record_violation(st.session_state.username, "Tab switched / window hidden", frame=frame)
                    st.success("Tab switch detected and recorded.")
            except Exception as e:
                st.error("Tab detection JS error: " + str(e))
        else:
            st.info("Tab detection requires 'streamlit_javascript' in requirements to be enabled.")

        # Download last screenshot
        if st.session_state.last_screenshot and os.path.exists(st.session_state.last_screenshot):
            with open(st.session_state.last_screenshot, "rb") as f:
                st.download_button("Download last screenshot", data=f, file_name=os.path.basename(st.session_state.last_screenshot), mime="image/png")

elif menu == "About":
    st.header("About")
    st.markdown("""
    **What this app does**
    - Signup / Login using Railway Postgres (DATABASE_URL).
    - Start / Stop proctoring with live webcam preview.
    - Detects: missing face, missing eyes (possible closed eyes), and tab/window switches.
    - On detection: shows alert, saves screenshot, and inserts a log row into the `logs` table.
    **Notes**
    - Passwords are hashed with bcrypt.
    - This is a prototype: Haar cascades are simple detectors and can produce false positives/negatives.
    - Ensure you have consent from test takers before recording webcam/screenshots.
    """)

# Ensure proctoring stops when user navigates away (best-effort)
if not st.session_state.proctoring:
    pass
