import streamlit as st
import cv2
import mysql.connector
import pandas as pd
import time
import os
import datetime
import av
from streamlit.components.v1 import html
from streamlit_webrtc import webrtc_streamer, VideoTransformerBase


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

# MEDIAPIPE

# FACE DETECTION
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

# UI
st.title(f"Online Exam Cheating Detection - {st.session_state.username}")

start = st.button("Start Monitoring")
stop = st.button("Stop")

frame_window = st.image([])

# START CAMERA
class VideoProcessor(VideoTransformerBase):

            self.last_capture_time = current_time

        # =========================
        # MYSQL STORAGE
        # =========================
        try:

            cursor.execute(
                '''
                INSERT INTO logs
                (
                    username,
                    face_detected,
                    multiple_faces,
                    tab_switch,
                    cheating_flag
                )
                VALUES(%s, %s, %s, %s, %s, %s)
                ''',
                (
                    st.session_state.username,
                    face_detected,
                    multiple_faces,
                    tab_switch,
                    cheating_flag
                )
            )

            conn.commit()

        except Exception as e:
            print(e)

        # =========================
        # FACE RECTANGLES
        # =========================
        for (x, y, w, h) in faces:

            cv2.rectangle(
                img,
                (x, y),
                (x + w, y + h),
                (255, 0, 0),
                2
            )

        st.session_state.tab_switch = 0

        return img


# =========================
# START ONLINE MONITORING
# =========================

if start:

    st.warning("Allow camera permission in browser")

    webrtc_streamer(
        key="online-exam-monitor",
        video_processor_factory=VideoProcessor,
        media_stream_constraints={
            "video": True,
            "audio": False
        },
        async_processing=True
    )

# CLEAN LOGS
st.subheader("Logs Data")

try:

    df = pd.read_sql(
        '''
        SELECT
            username,
            face_detected,
            multiple_faces,
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
