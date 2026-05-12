import streamlit as st
from streamlit_webrtc import webrtc_streamer, VideoTransformerBase
import cv2

st.title("Camera Test")

class VideoProcessor(VideoTransformerBase):

    def transform(self, frame):

        img = frame.to_ndarray(format="bgr24")

        cv2.putText(
            img,
            "Camera Working",
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            1,
            (0, 255, 0),
            2
        )

        return img

webrtc_streamer(
    key="camera",
    video_processor_factory=VideoProcessor,
    media_stream_constraints={
        "video": True, "audio": False
    }
)
