"""HTTP and image-processing helpers for the kiosk frontend."""

import os
import secrets
from typing import Protocol

import cv2
import numpy as np
import requests

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
MAX_IMAGE_BYTES = 150 * 1024
REQUEST_TIMEOUT_SECONDS = 2.0
FACE_CASCADE_PATH = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")


class CameraImage(Protocol):
    """The subset of Streamlit's camera upload API used by this module."""

    def getvalue(self) -> bytes: ...


def _mock_nonce() -> str:
    return f"mock-{secrets.token_urlsafe(24)}"


def _base_url(api_base_url: str | None = None) -> str:
    base_url = (api_base_url or API_BASE_URL).strip().rstrip("/")
    if not base_url:
        raise ValueError("The API base URL cannot be empty.")
    return base_url


def get_session_nonce(
    offline: bool = False,
    api_base_url: str | None = None,
    allow_mock_fallback: bool | None = None,
) -> str:
    """Fetch a session nonce, or use a generated mock nonce when permitted."""
    if offline:
        return _mock_nonce()

    if allow_mock_fallback is None:
        allow_mock_fallback = os.getenv("KIOSK_ALLOW_MOCK_NONCE", "false").strip().lower() in {
            "1",
            "true",
            "yes",
        }

    try:
        response = requests.post(
            f"{_base_url(api_base_url)}/session",
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException:
        if allow_mock_fallback:
            return _mock_nonce()
        raise

    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("The session API response must be a JSON object.")

    nonce = payload.get("nonce") or payload.get("session_nonce")
    if not isinstance(nonce, str) or not nonce.strip():
        raise ValueError("The session API response did not contain a valid nonce.")
    return nonce.strip()


def preprocess_image(camera_image: CameraImage) -> bytes:
    """Crop around the largest detected face when possible, then fit JPEG under 150 KB."""
    encoded_input = np.frombuffer(camera_image.getvalue(), dtype=np.uint8)
    frame = cv2.imdecode(encoded_input, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("The captured image could not be decoded.")

    height, width = frame.shape[:2]
    if width == 0 or height == 0:
        raise ValueError("The captured image has invalid dimensions.")

    classifier_type = getattr(cv2, "CascadeClassifier", None)
    if classifier_type is not None:
        cascade = classifier_type(FACE_CASCADE_PATH)
        if not cascade.empty():
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(40, 40),
            )
            if len(faces):
                x, y, face_width, face_height = max(
                    faces,
                    key=lambda face: int(face[2]) * int(face[3]),
                )
                padding_x = int(face_width * 0.35)
                padding_y = int(face_height * 0.45)
                left = max(0, int(x) - padding_x)
                top = max(0, int(y) - padding_y)
                right = min(width, int(x + face_width) + padding_x)
                bottom = min(height, int(y + face_height) + padding_y)
                frame = frame[top:bottom, left:right]

    if frame.shape[:2] == (height, width):
        # Center crop retains the likely selfie subject when face detection is unavailable.
        height, width = frame.shape[:2]
        crop_width = max(1, int(width * 0.8))
        crop_height = max(1, int(height * 0.9))
        left = (width - crop_width) // 2
        top = (height - crop_height) // 2
        frame = frame[top : top + crop_height, left : left + crop_width]

    for _ in range(12):
        for quality in (88, 78, 68, 58, 48, 38, 28):
            success, encoded_jpeg = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), quality],
            )
            if not success:
                raise ValueError("OpenCV failed to encode the captured image.")

            image_bytes = encoded_jpeg.tobytes()
            if len(image_bytes) < MAX_IMAGE_BYTES:
                return image_bytes

        height, width = frame.shape[:2]
        resized_dimensions = (max(1, int(width * 0.8)), max(1, int(height * 0.8)))
        if resized_dimensions == (width, height):
            break
        frame = cv2.resize(frame, resized_dimensions, interpolation=cv2.INTER_AREA)

    raise ValueError("The captured image could not be compressed below 150 KB.")


def _post_json_or_multipart(
    endpoint: str,
    *,
    api_base_url: str | None = None,
    data: dict[str, str] | None = None,
    image_bytes: bytes | None = None,
    image_field: str = "frame",
) -> dict[str, object]:
    request_options: dict[str, object] = {
        "data": data or {},
        "timeout": REQUEST_TIMEOUT_SECONDS,
    }
    if image_bytes is not None:
        request_options["files"] = {
            image_field: ("capture.jpg", image_bytes, "image/jpeg")
        }

    response = requests.post(
        f"{_base_url(api_base_url)}{endpoint}",
        **request_options,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"The {endpoint} API response must be a JSON object.")
    return payload


def send_decide_request(
    badge_id: str,
    nonce: str,
    image_bytes: bytes,
    api_base_url: str | None = None,
) -> dict[str, object]:
    """Send an authentication frame and context to the decision API."""
    return _post_json_or_multipart(
        "/decide",
        api_base_url=api_base_url,
        data={"badge_id": badge_id, "nonce": nonce},
        image_bytes=image_bytes,
    )


def send_challenge_response(
    badge_id: str,
    nonce: str,
    action: str,
    image_bytes: bytes,
    api_base_url: str | None = None,
) -> dict[str, object]:
    """Submit a Tier 2 action and its follow-up frame for verification."""
    return _post_json_or_multipart(
        "/challenge/verify",
        api_base_url=api_base_url,
        data={"badge_id": badge_id, "nonce": nonce, "action": action},
        image_bytes=image_bytes,
        image_field="response_frame",
    )


def validate_liveness_session(
    badge_id: str,
    session_id: str,
    image_bytes: bytes,
    api_base_url: str | None = None,
) -> dict[str, object]:
    """Submit a captured frame to the Tier 3 liveness session endpoint."""
    return _post_json_or_multipart(
        "/liveness/validate",
        api_base_url=api_base_url,
        data={"badge_id": badge_id, "session_id": session_id},
        image_bytes=image_bytes,
        image_field="liveness_frame",
    )


def verify_step_up_otp(
    badge_id: str,
    nonce: str,
    otp: str,
    api_base_url: str | None = None,
) -> dict[str, object]:
    """Validate a six-digit step-up code with the backend."""
    return _post_json_or_multipart(
        "/challenge/otp",
        api_base_url=api_base_url,
        data={"badge_id": badge_id, "nonce": nonce, "otp": otp},
    )
