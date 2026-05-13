# Deployment-ready Online Exam Cheating Detection System
# Updated for online deployment, login/signup, alerts, screenshots,
# improved eye detection and tab switch detection.

import streamlit as st
import cv2
import mediapipe as mp
import mysql.connector
import pandas as pd
import time
import os
import datetime
import av
import numpy as np
from streamlit.components.v1 import html
from streamlit_webrtc import webrtc_streamer, VideoProcessorBase, RTCConfiguration

st.set_page_config(page_title="Online Exam Cheating Detection", layout="wide")

# SESSION STATE
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "username" not in st.session_state:
    st.session_state.username = ""

if "tab_switch" not in st.session_state:
    st.session_state.tab_switch = 0

# TAB SWITCH DETECTION
tab_js = '''
<script>
document.addEventListener("visibilitychange", function() {
    if(document.hidden){
        const currentUrl = window.location.href;
        fetch(currentUrl + "?tab_switch=true");
    }
});
</script>
'''

html(tab_js, height=0)

query_params = st.query_params

if "tab_switch" in query_params:
    st.session_state.tab_switch = 1

# DATABASE CONNECTION
# Replace localhost credentials with cloud DB credentials before deployment.

conn = mysql.connector.connect(
    host="yamabiko.proxy.rlwy.net",
    user="root",
    password="MYOXSfLbCxPZfbfwENfJVxrsLzSiXDIE",
    database="railway",
    port= 11818
)

cursor = conn.cursor()

# LOGIN / SIGNUP
if not st.session_state.logged_in:

    st.title("Login / Signup")

    option = st.radio("Select Option", ["Login", "Signup"])

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    # SIGNUP
    if option == "Signup":

        if st.button("Create Account"):

            cursor.execute(
                "SELECT * FROM users WHERE username=%s",
                (username,)
            )

            existing_user = cursor.fetchone()

            if existing_user:
                st.warning("User already exists!")

            else:
                cursor.execute(
                    "INSERT INTO users(username, password) VALUES(%s, %s)",
                    (username, password)
                )

                conn.commit()

                st.success("Account Created Successfully!")

    # LOGIN
    if option == "Login":

        if st.button("Login"):

            cursor.execute(
                "SELECT * FROM users WHERE username=%s AND password=%s",
                (username, password)
            )

            user = cursor.fetchone()

            if user:

                st.session_state.logged_in = True
                st.session_state.username = username

                st.success(f"Welcome {username}")

                st.rerun()

            else:
                st.error("Invalid Credentials")

    st.stop()

# UI
st.title(f"Online Exam Cheating Detection - {st.session_state.username}")

# WEBRTC CONFIGURATION (STUN servers for cloud deployment)
RTC_CONFIGURATION = RTCConfiguration(
    {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
)

# VIDEO PROCESSOR
class CheatDetectionProcessor(VideoProcessorBase):

    def __init__(self):
        self.face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh = self.mp_face_mesh.FaceMesh(
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.prev_eye_x = None
        self.last_capture_time = 0
        self.capture_interval = 5

        # Shared state readable from main thread
        self.face_detected = 0
        self.multiple_faces = 0
        self.eye_movement = 0
        self.cheating_flag = 0
        self.last_frame = None

    def recv(self, frame: av.VideoFrame) -> av.VideoFrame:

        img = frame.to_ndarray(format="bgr24")

        img = cv2.flip(img, 1)

        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        # FACE DETECTION
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        faces = self.face_cascade.detectMultiScale(gray, 1.3, 5)

        self.face_detected = 1 if len(faces) > 0 else 0

        self.multiple_faces = 1 if len(faces) > 1 else 0

        # EYE DETECTION
        results = self.face_mesh.process(rgb)

        self.eye_movement = 0

        if results.multi_face_landmarks:

            for face_landmarks in results.multi_face_landmarks:

                left_eye = face_landmarks.landmark[33]

                current_eye_x = left_eye.x

                if self.prev_eye_x is not None:

                    if abs(current_eye_x - self.prev_eye_x) > 0.015:
                        self.eye_movement = 1

                self.prev_eye_x = current_eye_x

        # TAB SWITCH (read from session state via flag)
        tab_switch = st.session_state.get("tab_switch", 0)

        # CHEATING LOGIC
        self.cheating_flag = 1 if (
            self.multiple_faces == 1
            or self.eye_movement == 1
            or tab_switch == 1
        ) else 0

        # SCREENSHOT
        current_time = time.time()

        if self.cheating_flag == 1 and (
            current_time - self.last_capture_time > self.capture_interval
        ):

            if not os.path.exists("screenshots"):
                os.makedirs("screenshots")

            filename = f"screenshots/cheating_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.png"

            cv2.imwrite(filename, img)

            self.last_capture_time = current_time

        # STORE IN MYSQL
        try:

            db_conn = mysql.connector.connect(
                host="yamabiko.proxy.rlwy.net",
                user="root",
                password="MYOXSfLbCxPZfbfwENfJVxrsLzSiXDIE",
                database="railway",
                port=11818
            )

            db_cursor = db_conn.cursor()

            db_cursor.execute(
                '''
                INSERT INTO logs
                (
                    username,
                    face_detected,
                    multiple_faces,
                    eye_movement,
                    tab_switch,
                    cheating_flag
                )
                VALUES(%s, %s, %s, %s, %s, %s)
                ''',
                (
                    st.session_state.username,
                    self.face_detected,
                    self.multiple_faces,
                    self.eye_movement,
                    tab_switch,
                    self.cheating_flag
                )
            )

            db_conn.commit()
            db_cursor.close()
            db_conn.close()

        except Exception:
            pass

        # FACE BOX
        for (x, y, w, h) in faces:

            cv2.rectangle(
                img,
                (x, y),
                (x + w, y + h),
                (255, 0, 0),
                2
            )

        self.last_frame = img

        # RESET TAB SWITCH
        st.session_state.tab_switch = 0

        return av.VideoFrame.from_ndarray(img, format="bgr24")


# START WEBRTC STREAMER
ctx = webrtc_streamer(
    key="cheating-detection",
    video_processor_factory=CheatDetectionProcessor,
    rtc_configuration=RTC_CONFIGURATION,
    media_stream_constraints={"video": True, "audio": False},
)

# ALERTS (shown while streamer is active)
if ctx.state.playing and ctx.video_processor:

    processor = ctx.video_processor

    if processor.multiple_faces:
        st.error("Multiple Faces Detected!")

    if processor.eye_movement:
        st.warning("Suspicious Eye Movement Detected!")

    if st.session_state.tab_switch:
        st.error("Tab Switching Detected!")

# CLEAN LOGS
st.subheader("Logs Data")

try:

    df = pd.read_sql(
        '''
        SELECT
            username,
            face_detected,
            multiple_faces,
            eye_movement,
            tab_switch,
            cheating_flag,
            timestamp
        FROM logs
        WHERE cheating_flag IS NOT NULL
        ''',
        conn
    )

    df = df.dropna()

    st.dataframe(df, use_container_width=True)

    st.subheader("Summary")

    st.write("Total Records:", len(df))

    st.write(
        "Cheating Cases:",
        int(df["cheating_flag"].sum())
    )

except Exception as e:

    st.warning(f"No data found yet: {e}")
