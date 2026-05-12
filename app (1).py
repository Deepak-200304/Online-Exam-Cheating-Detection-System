# Online Exam Cheating Detection System
# Fixes: BGR->RGB for st.image(), Start/Stop buttons, cloud webcam via browser

import streamlit as st
import mysql.connector
import pandas as pd
import datetime
import numpy as np
import cv2
import mediapipe as mp
from streamlit.components.v1 import html as st_html

st.set_page_config(page_title="Online Exam Cheating Detection", layout="wide")

# SESSION STATE
for key, default in [
    ("logged_in",   False),
    ("username",    ""),
    ("tab_switch",  0),
    ("prev_eye_x",  None),
    ("monitoring",  False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

# TAB SWITCH
if "tab_switch" in st.query_params:
    st.session_state.tab_switch = 1
    st.query_params.clear()

# DATABASE
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

# LOGIN / SIGNUP
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
                cursor.execute("INSERT INTO users(username, password) VALUES(%s,%s)", (username, password))
                conn.commit()
                st.success("Account created! Please log in.")

    elif option == "Login":
        if st.button("Login"):
            cursor.execute("SELECT * FROM users WHERE username=%s AND password=%s", (username, password))
            if cursor.fetchone():
                st.session_state.logged_in = True
                st.session_state.username  = username
                st.rerun()
            else:
                st.error("Invalid credentials.")
    st.stop()

# MODELS
@st.cache_resource
def load_models():
    fm = mp.solutions.face_mesh.FaceMesh(
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    fc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    return fm, fc

face_mesh, face_cascade = load_models()

# HEADER
st.title(f"Online Exam Cheating Detection — {st.session_state.username}")

# START / STOP BUTTONS
col_s, col_e, _ = st.columns([1, 1, 5])
with col_s:
    if st.button("▶ Start Monitoring", disabled=st.session_state.monitoring, use_container_width=True):
        st.session_state.monitoring  = True
        st.session_state.prev_eye_x  = None
        st.rerun()
with col_e:
    if st.button("⏹ Stop Monitoring", disabled=not st.session_state.monitoring, use_container_width=True):
        st.session_state.monitoring = False
        st.rerun()

if st.session_state.monitoring:
    st.success("🟢 Monitoring ACTIVE — capture a snapshot below to analyse.")
else:
    st.warning("🔴 Monitoring STOPPED — press Start Monitoring to begin.")

st.divider()

# MONITORING SECTION
if st.session_state.monitoring:

    webcam_html = """
    <style>
      #videoEl { width:100%; max-width:640px; border-radius:8px; background:#111; }
      #camStatus { color:#aaa; font-size:13px; margin-top:6px; }
    </style>
    <video id="videoEl" autoplay playsinline muted></video>
    <p id="camStatus">Requesting camera access...</p>
    <script>
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
          const url = new URL(window.location.href);
          url.searchParams.set("tab_switch", "1");
          window.location.href = url.toString();
        }
      });
      navigator.mediaDevices.getUserMedia({ video: true, audio: false })
        .then(stream => {
          document.getElementById("videoEl").srcObject = stream;
          document.getElementById("camStatus").textContent = "Camera active";
        })
        .catch(err => {
          const s = document.getElementById("camStatus");
          s.textContent = "Camera error: " + err.message;
          s.style.color = "#ff4b4b";
        });
    </script>
    """

    col_cam, col_snap = st.columns([3, 2])
    with col_cam:
        st.subheader("Live Feed (Browser)")
        st_html(webcam_html, height=420)
    with col_snap:
        st.subheader("Snapshot Analysis")
        uploaded_frame = st.camera_input("Capture frame", label_visibility="collapsed")

    # PROCESS FRAME
    def process_frame(image_bytes):
        arr   = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)  # BGR
        if frame is None:
            return None

        # FIX: convert to RGB immediately — used for both display and MediaPipe
        rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        faces          = face_cascade.detectMultiScale(gray, 1.3, 5)
        face_detected  = 1 if len(faces) > 0 else 0
        multiple_faces = 1 if len(faces) > 1 else 0

        results      = face_mesh.process(rgb)
        eye_movement = 0
        if results.multi_face_landmarks:
            for fl in results.multi_face_landmarks:
                curr_x = fl.landmark[33].x
                prev_x = st.session_state.prev_eye_x
                if prev_x is not None and abs(curr_x - prev_x) > 0.015:
                    eye_movement = 1
                st.session_state.prev_eye_x = curr_x

        # Draw boxes on RGB frame directly (safe for st.image)
        for (x, y, w, h) in faces:
            color = (255, 0, 0) if multiple_faces else (0, 200, 0)
            cv2.rectangle(rgb, (x, y), (x+w, y+h), color, 2)

        cheating_flag = 1 if (multiple_faces or eye_movement or st.session_state.tab_switch) else 0

        return {
            "frame_rgb":     rgb,           # RGB — ready for st.image(), no conversion needed
            "face_detected": face_detected,
            "multiple_faces":multiple_faces,
            "eye_movement":  eye_movement,
            "tab_switch":    st.session_state.tab_switch,
            "cheating_flag": cheating_flag,
        }

    def save_and_log(result):
        import os
        os.makedirs("screenshots", exist_ok=True)
        if result["cheating_flag"] == 1:
            ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            bgr = cv2.cvtColor(result["frame_rgb"], cv2.COLOR_RGB2BGR)
            cv2.imwrite(f"screenshots/cheating_{ts}.png", bgr)
        try:
            cursor.execute(
                "INSERT INTO logs (username, face_detected, multiple_faces, eye_movement, tab_switch, cheating_flag) VALUES (%s,%s,%s,%s,%s,%s)",
                (st.session_state.username, result["face_detected"], result["multiple_faces"],
                 result["eye_movement"], result["tab_switch"], result["cheating_flag"]),
            )
            conn.commit()
        except Exception as e:
            st.error(f"DB error: {e}")
        st.session_state.tab_switch = 0

    if uploaded_frame is not None:
        result = process_frame(uploaded_frame.getvalue())
        if result is None:
            st.error("Could not decode image — please try again.")
        else:
            st.divider()
            r1, r2 = st.columns([2, 2])
            with r1:
                # frame_rgb is already RGB — no TypeError
                st.image(result["frame_rgb"], caption="Analysed Frame", use_container_width=True)
            with r2:
                st.subheader("Detection Results")
                m1, m2, m3 = st.columns(3)
                m1.metric("Faces",      result["face_detected"])
                m2.metric("Multi-Face", result["multiple_faces"])
                m3.metric("Cheat Flag", result["cheating_flag"])
                st.divider()
                if result["multiple_faces"]:
                    st.error("🚨 Multiple faces detected!")
                if result["eye_movement"]:
                    st.warning("⚠️ Suspicious eye movement!")
                if result["tab_switch"]:
                    st.error("🚨 Tab switching detected!")
                if result["cheating_flag"] == 0:
                    st.success("✅ No cheating detected.")
            save_and_log(result)

    elif st.session_state.tab_switch == 1:
        st.error("🚨 Tab switching detected! Capture a frame to log it.")

else:
    st.info("Press **▶ Start Monitoring** above to activate the webcam and analysis.")

# LOGS
st.divider()
st.subheader("Logs Data")
try:
    df = pd.read_sql(
        "SELECT username, face_detected, multiple_faces, eye_movement, tab_switch, cheating_flag, timestamp FROM logs WHERE cheating_flag IS NOT NULL ORDER BY timestamp DESC LIMIT 200",
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
