"""Tiered liveness and step-up verification UI components."""

import secrets
from collections.abc import Callable
from typing import Any

import requests
import streamlit as st

import cv2
from utils import (
    preprocess_image,
    send_challenge_response,
    validate_liveness_session,
    verify_step_up_otp,
)

CHALLENGES = (
    ("blink_twice", "Blink twice, then capture the follow-up frame."),
    ("turn_head_left", "Turn your head to the left, then capture the follow-up frame."),
    ("turn_head_right", "Turn your head to the right, then capture the follow-up frame."),
    ("flash_color", "Look toward the highlighted color prompt, then capture a frame."),
)
CHALLENGE_COLORS = ("#ef4444", "#22c55e", "#3b82f6", "#facc15")
MAX_STEP_UP_ATTEMPTS = 3


def _is_verified(payload: dict[str, Any]) -> bool:
    if payload.get("verified") is True or payload.get("success") is True:
        return True
    status = str(payload.get("status", "")).strip().lower()
    return status in {"verified", "passed", "allow", "success"}


def reset_step_up_state() -> None:
    """Clear the limited-attempt state after a new primary authentication attempt."""
    st.session_state.otp_attempt_count = 0
    st.session_state.otp_locked = False
    st.session_state.otp_verified = False
    st.session_state.challenge_result = None
    st.session_state.liveness_result = None


def render_tier2_challenge(
    badge_id: str,
    nonce: str,
    api_base_url: str,
    offline: bool,
    challenge_id: str,
) -> None:
    """Show a random action prompt and collect a follow-up camera frame."""
    st.markdown("#### Tier 2 · Nonce action challenge")
    instruction_key = f"tier2_instruction_{challenge_id}"
    if instruction_key not in st.session_state:
        st.session_state[instruction_key] = secrets.choice(CHALLENGES)

    action, instruction = st.session_state[instruction_key]
    st.info(f"**Action:** {instruction}")
    if action == "flash_color":
        color_key = f"tier2_color_{challenge_id}"
        if color_key not in st.session_state:
            st.session_state[color_key] = secrets.choice(CHALLENGE_COLORS)
        st.markdown(
            "<div style='height: 5rem; border-radius: .6rem; "
            f"background: {st.session_state[color_key]};'></div>",
            unsafe_allow_html=True,
        )
    completed = st.checkbox(
        "I completed the requested action",
        key=f"tier2_completed_{challenge_id}",
    )
    follow_up = st.camera_input(
        "Capture the challenge response",
        key=f"tier2_camera_{challenge_id}",
    )
    if st.button(
        "Verify Tier 2 response",
        key=f"tier2_submit_{challenge_id}",
        disabled=not completed or follow_up is None,
    ):
        try:
            frame_bytes = preprocess_image(follow_up)
            if offline:
                result: dict[str, Any] = {
                    "verified": True,
                    "message": "Mock Tier 2 action accepted.",
                }
            else:
                result = send_challenge_response(
                    badge_id,
                    nonce,
                    action,
                    frame_bytes,
                    api_base_url,
                )
            st.session_state.challenge_result = result
        except (ValueError, OSError, cv2.error, requests.RequestException) as error:
            st.error(f"Tier 2 verification failed: {error}")

    result = st.session_state.get("challenge_result")
    if result:
        if _is_verified(result):
            st.success(str(result.get("message") or "Tier 2 challenge passed."))
        else:
            st.error(str(result.get("message") or "Tier 2 challenge was not verified."))


def render_tier3_liveness(
    badge_id: str,
    session_id: str,
    api_base_url: str,
    offline: bool,
) -> None:
    """Render a provider-neutral container for a high-security liveness session."""
    st.markdown("#### Tier 3 · High-security liveness")
    with st.container(border=True):
        st.markdown("**Liveness stream session**")
        st.caption(
            "Provider-neutral integration point for an AWS Rekognition / Amplify video "
            "stream widget. The demo validates a captured frame; connect the deployed "
            "provider SDK here for continuous video validation."
        )
        frame = st.camera_input(
            "Capture a Tier 3 liveness frame",
            key=f"tier3_camera_{session_id}",
        )
        if st.button(
            "Validate Tier 3 session",
            key=f"tier3_submit_{session_id}",
            disabled=frame is None,
        ):
            try:
                frame_bytes = preprocess_image(frame)
                if offline:
                    result: dict[str, Any] = {
                        "verified": True,
                        "message": "Mock Tier 3 liveness session validated.",
                    }
                else:
                    result = validate_liveness_session(
                        badge_id,
                        session_id,
                        frame_bytes,
                        api_base_url,
                    )
                st.session_state.liveness_result = result
            except (ValueError, OSError, cv2.error, requests.RequestException) as error:
                st.error(f"Tier 3 liveness validation failed: {error}")

    result = st.session_state.get("liveness_result")
    if result:
        if _is_verified(result):
            st.success(str(result.get("message") or "Tier 3 liveness check passed."))
        else:
            st.error(str(result.get("message") or "Tier 3 liveness check failed."))


def render_step_up_flow(
    badge_id: str,
    nonce: str,
    api_base_url: str,
    offline: bool,
    recapture_handler: Callable[[Any], None],
    attempt_id: str,
) -> None:
    """Render a six-digit OTP form and capped recapture flow."""
    st.markdown("#### OTP or recapture")
    if st.session_state.get("otp_verified", False):
        st.success("Step-up verification passed.")
        return
    attempts = int(st.session_state.get("otp_attempt_count", 0))
    locked = bool(st.session_state.get("otp_locked", False))
    if locked or attempts >= MAX_STEP_UP_ATTEMPTS:
        st.session_state.otp_locked = True
        st.error("Step-up is locked after three unsuccessful attempts. Contact an operator.")
        return

    if offline:
        st.caption("Offline demo OTP: `123456`")
    otp = st.text_input(
        "Six-digit one-time passcode",
        type="password",
        max_chars=6,
        key=f"step_up_otp_{attempt_id}",
    )
    if st.button("Verify one-time passcode", key=f"verify_otp_{attempt_id}"):
        if not otp.isascii() or not otp.isdigit() or len(otp) != 6:
            attempts += 1
            st.session_state.otp_attempt_count = attempts
            if attempts >= MAX_STEP_UP_ATTEMPTS:
                st.session_state.otp_locked = True
                st.error("Enter exactly six digits. Step-up is now locked after three attempts.")
            else:
                st.warning(
                    f"Enter exactly six digits. "
                    f"{MAX_STEP_UP_ATTEMPTS - attempts} attempts remain."
                )
        else:
            try:
                if offline:
                    verified = secrets.compare_digest(otp, "123456")
                    result: dict[str, Any] = {"verified": verified}
                else:
                    result = verify_step_up_otp(
                        badge_id,
                        nonce,
                        otp,
                        api_base_url,
                    )
                    verified = _is_verified(result)

                if verified:
                    st.session_state.otp_verified = True
                    st.success(str(result.get("message") or "Step-up verification passed."))
                else:
                    attempts += 1
                    st.session_state.otp_attempt_count = attempts
                    if attempts >= MAX_STEP_UP_ATTEMPTS:
                        st.session_state.otp_locked = True
                        st.error("Incorrect code. Step-up is now locked after three attempts.")
                    else:
                        st.error(f"Incorrect code. {MAX_STEP_UP_ATTEMPTS - attempts} attempts remain.")
            except requests.RequestException as error:
                st.error(f"OTP verification service is unavailable: {error}")

    attempts = int(st.session_state.get("otp_attempt_count", 0))
    st.caption(f"OTP attempts used: {attempts}/{MAX_STEP_UP_ATTEMPTS}")
    st.markdown("**Or recapture your face**")
    recapture = st.camera_input(
        f"Recapture for authentication (attempt {attempts + 1})",
        key=f"recapture_camera_{attempt_id}_{attempts}",
    )
    if st.button(
        "Authenticate recaptured frame",
        key=f"recapture_submit_{attempt_id}_{attempts}",
        disabled=recapture is None or attempts >= MAX_STEP_UP_ATTEMPTS,
    ):
        st.session_state.otp_attempt_count = attempts + 1
        if attempts + 1 >= MAX_STEP_UP_ATTEMPTS:
            st.session_state.otp_locked = True
        recapture_handler(recapture)
