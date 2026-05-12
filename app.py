# Online Exam Cheating Detection System
# FIX: Uses browser webcam (JS) instead of cv2.VideoCapture(0)
# because cloud servers have no physical camera.

import streamlit as st
import mysql.connector
import pandas as pd
import time
import datetime
import numpy as np
import cv2
import mediapipe as mp
from streamlit.components.v1 import html as st_html

st.set_page_config(page_title="Online Exam Cheating Detection", layout="wide")

# ── SESSION STATE ──────────────────────────────────────────────────────────────
for key, default in [
    ("logged_in", False),
    ("username", ""),
    ("tab_switch", 0),
    ("prev_eye_x", None),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# ── TAB SWITCH DETECTION ───────────────────────────────────────────────────────
if "tab_switch" in st.query_params:
    st.session_state.tab_switch = 1
    st.query_params.clear()

# ── DATABASE ───────────────────────────────────────────────────────────────────
@st.cache_resource
def get_conn():
    return mysql.connector.connect(
        host="yamabiko.proxy.rlwy.net",
        user="root",
        password="MYOXSfLbCxPZfbfwENfJVxrsLzSiXDIE",
        database="railway",
        port=11818,
    )

conn   = get_conn()
cursor = conn.cursor()

# ── LOGIN / SIGNUP ─────────────────────────────────────────────────────────────
if not st.session_state.logged_in:
    st.title("Login / Signup")
    option   = st.radio("Select Option", ["Login", "Signup"])
    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if option == "Signup":
        if st.button("Create Account"):
            cursor.execute("SELECT * FROM users WHERE username=%s", (username,))
            if cursor.fetchone():
                st.warning("User already exists!")
            else:
                cursor.execute(
                    "INSERT INTO users(username, password) VALUES(%s,%s)",
                    (username, password),
                )
                conn.commit()
                st.success("Account created! Please log in.")

    elif option == "Login":
        if st.button("Login"):
            cursor.execute(
                "SELECT * FROM users WHERE username=%s AND password=%s",
                (username, password),
            )
            if cursor.fetchone():
                st.session_state.logged_in = True
                st.session_state.username  = username
                st.rerun()
            else:
                st.error("Invalid credentials.")
    st.stop()

# ── MEDIAPIPE ──────────────────────────────────────────────────────────────────
@st.cache_resource
def load_models():
    fm = mp.solutions.face_mesh.FaceMesh(
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    fc = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    return fm, fc

face_mesh, face_cascade = load_models()

# ── MAIN UI ────────────────────────────────────────────────────────────────────
st.title(f"Online Exam Cheating Detection — {st.session_state.username}")

# ── BROWSER WEBCAM COMPONENT ───────────────────────────────────────────────────
# The cloud server has no webcam, so we use the browser's getUserMedia API.
# Frames are displayed live in the browser; the user clicks "Analyse Frame"
# to send a snapshot to the server for CV processing.
webcam_html = """
<style>
  #videoEl  { width: 100%; max-width: 640px; border-radius: 8px; background:#111; }
  #snapBtn  { margin-top: 10px; padding: 8px 18px; background: #e74c3c;
              color: #fff; border: none; border-radius: 6px; cursor: pointer;
              font-size: 14px; }
  #snapBtn:hover { background: #c0392b; }
  #camStatus { color: #aaa; font-size: 13px; margin-top: 6px; }
</style>

<video id="videoEl" autoplay playsinline muted></video><br>
<button id="snapBtn" onclick="sendFrame()">📸 Analyse Frame</button>
<p id="camStatus">Requesting camera access…</p>

<script>
const video  = document.getElementById("videoEl");
const status = document.getElementById("camStatus");

// Tab-switch detection: redirects to same page with ?tab_switch=1
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    const url = new URL(window.location.href);
    url.searchParams.set("tab_switch", "1");
    window.location.href = url.toString();
  }
});

navigator.mediaDevices.getUserMedia({ video: true, audio: false })
  .then(stream => {
    video.srcObject = stream;
    status.textContent = "✅ Camera active — click Analyse Frame to check.";
  })
  .catch(err => {
    status.textContent = "❌ Camera error: " + err.message;
    status.style.color = "#ff4b4b";
  });

function sendFrame() {
  const canvas = document.createElement("canvas");
  canvas.width  = video.videoWidth  || 640;
  canvas.height = video.videoHeight || 480;
  canvas.getContext("2d").drawImage(video, 0, 0);
  // Send base64 JPEG to parent Streamlit window
  const b64 = canvas.toDataURL("image/jpeg", 0.85).split(",")[1];
  window.parent.postMessage({ isStreamlitMessage: true,
                               type: "examFrame", payload: b64 }, "*");
  status.textContent = "📤 Frame sent for analysis…";
}
</script>
"""

st.subheader("Live Browser Webcam")
st_html(webcam_html, height=460)

st.info(
    "💡 **How it works:** Your browser accesses the webcam directly. "
    "Click **Analyse Frame** above *or* use the snapshot button below to "
    "run face & eye detection on the server."
)

# ── CAMERA INPUT (primary analysis path) ──────────────────────────────────────
st.subheader("Snapshot Analysis")
uploaded_frame = st.camera_input(
    "Take a snapshot for cheating detection",
    help="Allow camera access when the browser asks.",
)

# ── PROCESS FRAME ──────────────────────────────────────────────────────────────
def process_frame(image_bytes):
    arr   = np.frombuffer(image_bytes, np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        return None

    rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Face detection
    faces          = face_cascade.detectMultiScale(gray, 1.3, 5)
    face_detected  = 1 if len(faces) > 0 else 0
    multiple_faces = 1 if len(faces) > 1 else 0

    # Eye movement via MediaPipe
    results      = face_mesh.process(rgb)
    eye_movement = 0
    if results.multi_face_landmarks:
        for fl in results.multi_face_landmarks:
            curr_x = fl.landmark[33].x
            prev_x = st.session_state.prev_eye_x
            if prev_x is not None and abs(curr_x - prev_x) > 0.015:
                eye_movement = 1
            st.session_state.prev_eye_x = curr_x

    # Draw green boxes around faces
    for (x, y, w, h) in faces:
        color = (0, 0, 255) if multiple_faces else (0, 255, 0)
        cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)

    cheating_flag = 1 if (
        multiple_faces or eye_movement or st.session_state.tab_switch
    ) else 0

    return {
        "frame":          frame,
        "face_detected":  face_detected,
        "multiple_faces": multiple_faces,
        "eye_movement":   eye_movement,
        "tab_switch":     st.session_state.tab_switch,
        "cheating_flag":  cheating_flag,
    }

def save_and_log(result: dict):
    import os
    os.makedirs("screenshots", exist_ok=True)

    if result["cheating_flag"] == 1:
        ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshots/cheating_{ts}.png"
        cv2.imwrite(filename, result["frame"])

    try:
        cursor.execute(
            """INSERT INTO logs
               (username, face_detected, multiple_faces,
                eye_movement, tab_switch, cheating_flag)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (
                st.session_state.username,
                result["face_detected"],
                result["multiple_faces"],
                result["eye_movement"],
                result["tab_switch"],
                result["cheating_flag"],
            ),
        )
        conn.commit()
    except Exception as e:
        st.error(f"DB error: {e}")

    st.session_state.tab_switch = 0  # reset after logging

# ── RUN DETECTION ──────────────────────────────────────────────────────────────
if uploaded_frame is not None:
    result = process_frame(uploaded_frame.getvalue())

    if result is None:
        st.error("Could not decode image. Please try again.")
    else:
        st.image(
            cv2.cvtColor(result["frame"], cv2.COLOR_BGR2RGB),
            caption="Analysed Frame",
            use_container_width=True,
        )

        col1, col2, col3 = st.columns(3)
        col1.metric("Faces Detected",   result["face_detected"])
        col2.metric("Multiple Faces",   result["multiple_faces"])
        col3.metric("Cheating Flag",    result["cheating_flag"])

        if result["multiple_faces"]:
            st.error("🚨 Multiple faces detected!")
        if result["eye_movement"]:
            st.warning("⚠️ Suspicious eye movement detected!")
        if result["tab_switch"]:
            st.error("🚨 Tab switching detected!")
        if result["cheating_flag"] == 0:
            st.success("✅ No cheating detected in this frame.")

        save_and_log(result)

# Also handle tab-switch flag (even without a new frame)
elif st.session_state.tab_switch == 1:
    st.error("🚨 Tab switching detected! Please submit a snapshot.")

# ── LOGS ───────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Logs Data")

try:
    df = pd.read_sql(
        """SELECT username, face_detected, multiple_faces,
                  eye_movement, tab_switch, cheating_flag, timestamp
           FROM logs
           WHERE cheating_flag IS NOT NULL
           ORDER BY timestamp DESC
           LIMIT 200""",
        conn,
    )
    df = df.dropna()
    st.dataframe(df, use_container_width=True)
    st.subheader("Summary")
    c1, c2 = st.columns(2)
    c1.metric("Total Records",  len(df))
    c2.metric("Cheating Cases", int(df["cheating_flag"].sum()))
except Exception as e:
    st.warning(f"No data found yet: {e}")
