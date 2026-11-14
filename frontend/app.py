"""Streamlit kiosk UI for face liveness and authentication decisions."""

import secrets
import time
from datetime import datetime
from html import escape
from typing import Any

import cv2
import requests
import streamlit as st

from components.challenges import (
    render_step_up_flow,
    render_tier2_challenge,
    render_tier3_liveness,
    reset_step_up_state,
)
from utils import (
    API_BASE_URL,
    get_session_nonce,
    preprocess_image,
    send_decide_request,
)

LATENCY_TARGET_SECONDS = 2.0
MOCK_RESPONSE_SECONDS = 1.05

DEMO_SCENARIOS: tuple[dict[str, Any], ...] = (
    {
        "name": "Genuine Allow",
        "decision": "ALLOW",
        "reason": "Live person detected and identity matched.",
        "confidence": 0.98,
    },
    {
        "name": "Wrong Person Deny",
        "decision": "DENY",
        "reason": "Identity mismatch: face does not match the supplied Badge ID.",
        "confidence": 0.95,
    },
    {
        "name": "Printed Photo",
        "decision": "DENY",
        "reason": "Spoof detected: printed-photo / paper-texture signature.",
        "confidence": 0.96,
    },
    {
        "name": "Screen Photo",
        "decision": "DENY",
        "reason": "Spoof detected: screen-photo replay and display reflection.",
        "confidence": 0.94,
    },
    {
        "name": "Video Replay",
        "decision": "REQUEST VERIFICATION",
        "reason": "Possible video replay; escalating to a randomized liveness challenge.",
        "confidence": 0.55,
    },
    {
        "name": "Mask",
        "decision": "DENY",
        "reason": "Spoof detected: mask-like facial presentation.",
        "confidence": 0.97,
    },
    {
        "name": "Low-Light Verify",
        "decision": "REQUEST VERIFICATION",
        "reason": "Insufficient image quality in low light; recapture or verify identity.",
        "confidence": 0.48,
    },
)
VALID_OUTCOMES = {"ALLOW", "REQUEST VERIFICATION", "DENY"}


def _normalize_decision(payload: dict[str, Any]) -> tuple[str, str, float | None]:
    """Validate a backend decision and normalize the fields consumed by the UI."""
    raw_decision = payload.get("decision", payload.get("outcome", ""))
    decision = str(raw_decision).strip().upper().replace("_", " ")
    if decision == "VERIFY":
        decision = "REQUEST VERIFICATION"
    if decision not in VALID_OUTCOMES:
        raise ValueError("The decision API returned an unsupported outcome.")

    spoof_type = payload.get("spoof_type")
    if spoof_type:
        reason = f"Spoof type detected: {str(spoof_type).replace('_', ' ').strip()}."
    else:
        raw_reason = (
            payload.get("failure_reason")
            or payload.get("reason")
            or payload.get("message")
            or ""
        )
        reason = str(raw_reason).strip()
    raw_confidence = payload.get("confidence")
    confidence: float | None = None
    if isinstance(raw_confidence, (int, float)):
        confidence = float(raw_confidence)
        if confidence > 1:
            confidence /= 100
        confidence = min(1.0, max(0.0, confidence))
    return decision, reason, confidence


def _latency_metric(elapsed_seconds: float) -> None:
    remaining = LATENCY_TARGET_SECONDS - elapsed_seconds
    st.metric(
        "Decision time",
        f"{elapsed_seconds:.2f} s",
        delta=f"{remaining:+.2f} s vs ≤ {LATENCY_TARGET_SECONDS:.1f} s target",
        delta_color="normal",
    )
    if elapsed_seconds > LATENCY_TARGET_SECONDS:
        st.warning("This attempt exceeded the 2.0-second latency budget.")


def _default_confidence(outcome: str) -> float | None:
    return {
        "ALLOW": 0.98,
        "REQUEST VERIFICATION": 0.55,
        "DENY": 0.95,
    }.get(outcome)


def _mock_decision(scenario: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": scenario["decision"],
        "message": scenario["reason"],
        "confidence": scenario["confidence"],
        "scenario": scenario["name"],
        "source": "simulation",
    }


def _shift_demo_scenario(step: int) -> None:
    names = [scenario["name"] for scenario in DEMO_SCENARIOS]
    current_name = st.session_state.get("demo_scenario_select", names[0])
    current_index = names.index(current_name) if current_name in names else 0
    st.session_state.demo_scenario_select = names[(current_index + step) % len(names)]


def _refresh_nonce(offline: bool, api_base_url: str) -> None:
    try:
        st.session_state.session_nonce = get_session_nonce(
            offline=offline,
            api_base_url=api_base_url,
        )
        st.session_state.nonce_base_url = api_base_url
        st.session_state.nonce_mode = offline
        st.session_state.session_nonce_error = ""
    except (ValueError, requests.RequestException) as error:
        st.session_state.session_nonce = ""
        st.session_state.nonce_base_url = api_base_url
        st.session_state.nonce_mode = offline
        st.session_state.session_nonce_error = str(error)


def _authenticate(
    camera_image: Any,
    badge_id: str,
    offline: bool,
    api_base_url: str,
    scenario: dict[str, Any],
) -> None:
    """Run one capture decision and persist the result for the next Streamlit rerun."""
    started_at = datetime.now().astimezone()
    start_counter = time.perf_counter()
    if st.session_state.get("otp_verified", False):
        reset_step_up_state()

    outcome = "DENY"
    reason = ""
    confidence: float | None = None
    response_payload: dict[str, Any] = {}
    scan_label = (
        "Running a simulated decision…"
        if offline
        else "Submitting capture to the configured decision API…"
    )
    with st.status(scan_label, expanded=True) as scan_status:
        scan_progress = st.progress(8, text="Preparing captured frame")
        try:
            image_bytes = preprocess_image(camera_image)
            scan_progress.progress(32, text="Frame optimized")
            nonce = st.session_state.get("session_nonce", "")
            if offline:
                scan_progress.progress(58, text="Loading selected illustrative scenario")
                time.sleep(MOCK_RESPONSE_SECONDS)
                response_payload = _mock_decision(scenario)
            else:
                if not nonce:
                    nonce = get_session_nonce(api_base_url=api_base_url)
                scan_progress.progress(58, text="Waiting for decision API response")
                response_payload = send_decide_request(
                    badge_id,
                    nonce,
                    image_bytes,
                    api_base_url=api_base_url,
                )
            scan_progress.progress(82, text="Decision received · finalizing session")
            outcome, reason, confidence = _normalize_decision(response_payload)
        except (ValueError, OSError, cv2.error, requests.RequestException) as error:
            reason = f"Authentication could not be completed: {error}"
        scan_status.update(
            label=f"Scan complete · {outcome}",
            state="complete",
            expanded=True,
        )
        scan_progress.progress(100, text="Verification complete")

    elapsed_seconds = time.perf_counter() - start_counter
    if confidence is None:
        confidence = response_payload.get("confidence")
        if isinstance(confidence, (int, float)):
            confidence = float(confidence)
            if confidence > 1:
                confidence /= 100
            confidence = min(1.0, max(0.0, confidence))
        else:
            confidence = _default_confidence(outcome)
    if outcome == "ALLOW":
        reset_step_up_state()

    st.session_state.last_attempt = {
        "attempt_id": secrets.token_hex(8),
        "started_at": started_at.isoformat(timespec="seconds"),
        "outcome": outcome,
        "reason": reason or "No additional decision detail was provided.",
        "confidence": confidence,
        "source": "simulation" if offline else "api",
        "scenario": str(response_payload.get("scenario") or ""),
        "elapsed_seconds": elapsed_seconds,
        "session_id": str(
            response_payload.get("session_id")
            or response_payload.get("liveness_session_id")
            or st.session_state.get("session_nonce", "")
        ),
        "challenge_type": str(
            response_payload.get("challenge_type")
            or response_payload.get("tier")
            or ""
        ).lower(),
    }

    # A nonce is single-use, so refresh it after every primary/recapture decision.
    _refresh_nonce(offline, api_base_url)
    st.rerun()


def _render_escalation(
    attempt: dict[str, Any],
    badge_id: str,
    api_base_url: str,
    offline: bool,
) -> None:
    paths = ("Tier 2 - Nonce challenge", "Tier 3 - Liveness session", "OTP / Recapture")
    suggested_path = attempt.get("challenge_type", "")
    default_path = 1 if "tier3" in suggested_path or "tier 3" in suggested_path else 0
    selected_path = st.radio(
        "Choose a step-up verification path",
        paths,
        index=default_path,
        horizontal=True,
        key=f"step_up_path_{attempt['attempt_id']}",
    )
    nonce = str(st.session_state.get("session_nonce", ""))
    session_id = str(attempt.get("session_id") or nonce or attempt["attempt_id"])

    if selected_path == paths[0]:
        render_tier2_challenge(
            badge_id,
            nonce,
            api_base_url,
            offline,
            attempt["attempt_id"],
        )
    elif selected_path == paths[1]:
        render_tier3_liveness(
            badge_id,
            session_id,
            api_base_url,
            offline,
        )
    else:
        render_step_up_flow(
            badge_id,
            nonce,
            api_base_url,
            offline,
            lambda image: _authenticate(
                image,
                badge_id,
                offline,
                api_base_url,
                DEMO_SCENARIOS[0],
            ),
            attempt["attempt_id"],
        )


def _render_attempt(
    attempt: dict[str, Any],
    badge_id: str,
    api_base_url: str,
    offline: bool,
) -> None:
    st.caption(f"Attempt started · {attempt['started_at']}")
    outcome = attempt["outcome"]
    reason = attempt["reason"]
    confidence = attempt.get("confidence")
    confidence_value = (
        max(0.0, min(1.0, float(confidence))) if confidence is not None else None
    )
    is_simulated = attempt.get("source") == "simulation"
    if outcome == "ALLOW":
        card_class, icon, title, subtitle = (
            "allow",
            "✓",
            "Access granted",
            "Identity and liveness verified",
        )
    elif outcome == "REQUEST VERIFICATION":
        card_class, icon, title, subtitle = (
            "verify",
            "!",
            "Verification required",
            "A step-up security check is needed",
        )
    else:
        card_class, icon, title, subtitle = (
            "deny",
            "×",
            "Access denied",
            "The security check did not pass",
        )

    confidence_markup = ""
    if confidence_value is not None:
        confidence_label = "Illustrative confidence" if is_simulated else "Confidence"
        confidence_markup = (
            f'<div class="confidence-row"><span>{confidence_label}</span>'
            f'<strong>{confidence_value * 100:.1f}%</strong></div>'
            '<div class="confidence-track"><span style="width: '
            f'{confidence_value * 100:.1f}%"></span></div>'
        )
    source = str(attempt.get("source", "api"))
    scenario = str(attempt.get("scenario", ""))
    source_label = (
        f"SIMULATED · {scenario}"
        if is_simulated and scenario
        else "SIMULATED RESULT"
        if is_simulated
        else "LIVE DECISION API"
    )
    source_class = "simulated" if is_simulated else "live"
    st.markdown(
        f'<section class="outcome-card {card_class}">'
        f'<div class="result-source {source_class}">{escape(source_label)}</div>'
        f'<div class="outcome-icon">{icon}</div>'
        f'<div class="outcome-copy"><div class="outcome-kicker">'
        f'{escape(outcome)}</div><h3>{escape(title)}</h3>'
        f'<p>{escape(subtitle)}</p></div>'
        f'<div class="outcome-reason">{escape(reason)}</div>'
        f'{confidence_markup}</section>',
        unsafe_allow_html=True,
    )

    _latency_metric(float(attempt["elapsed_seconds"]))

    if outcome == "REQUEST VERIFICATION":
        _render_escalation(attempt, badge_id, api_base_url, offline)
    elif outcome == "ALLOW":
        st.button(
            "Next User",
            key=f"reset_{attempt['attempt_id']}",
            type="primary",
            on_click=_reset_kiosk,
        )
    else:
        st.button(
            "Retry authentication",
            key=f"retry_{attempt['attempt_id']}",
            type="primary",
            on_click=_reset_kiosk,
        )


def _reset_kiosk() -> None:
    """Return the stage to ready and force Streamlit to create a fresh camera widget."""
    st.session_state.pop("last_attempt", None)
    st.session_state.pop("demo_result", None)
    st.session_state.camera_epoch = int(st.session_state.get("camera_epoch", 0)) + 1
    reset_step_up_state()


st.set_page_config(
    page_title="Face Liveness & Authentication Kiosk",
    page_icon="🛡️",
    layout="wide",
)
st.markdown(
    """
    <style>
    :root { color-scheme: light; }
    html, body, [data-testid="stAppViewContainer"], [data-testid="stMain"] {
        background: #f8fafc !important; color: #0f172a;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }
    [data-testid="stAppViewContainer"] {
        background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%) !important;
    }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stMain"] > div { max-width: 1440px; padding-top: 1.1rem; }
    h1, h2, h3 { font-family: Inter, ui-sans-serif, system-ui, sans-serif !important; letter-spacing: -.025em; }
    h1 { font-size: clamp(1.9rem, 3vw, 2.6rem) !important; color: #0f172a; font-weight: 700; }
    h2, h3 { color: #0f172a; }
    p, label, [data-testid="stCaptionContainer"] { color: #64748b; }
    [data-testid="stSidebar"] {
        background: #fff; border-right: 1px solid #e2e8f0;
    }
    [data-testid="stMetric"], [data-testid="stCameraInput"],
    [data-testid="stStatusWidget"], [data-testid="stVerticalBlockBorderWrapper"] {
        background: #fff; border: 1px solid #e2e8f0;
        border-radius: 14px; box-shadow: 0 1px 3px rgba(15,23,42,.06);
    }
    [data-testid="stMetric"] { padding: .8rem 1rem; }
    [data-testid="stMetricLabel"] { color: #64748b; }
    [data-testid="stMetricValue"] { color: #0f172a; font-weight: 700; }
    [data-testid="stProgress"] > div > div { background: #2563eb; }
    [data-testid="stStatusWidget"] { color: #334155; }
    [data-testid="stStatusWidget"] summary { color: #1d4ed8; font-weight: 600; }
    .stButton > button {
        min-height: 2.75rem; border-radius: 9px; font-weight: 600;
        color: #334155; border: 1px solid #cbd5e1; background: #fff;
        transition: background .16s ease, box-shadow .16s ease, border-color .16s ease;
    }
    .stButton > button:hover {
        color: #1d4ed8; border-color: #93c5fd; background: #eff6ff;
        box-shadow: 0 2px 7px rgba(37,99,235,.10);
    }
    .stButton > button[kind="primary"] {
        background: #2563eb; color: #fff; border-color: #2563eb;
        box-shadow: 0 1px 2px rgba(15,23,42,.12);
    }
    .stButton > button[kind="primary"]:hover {
        background: #1d4ed8; color: #fff; border-color: #1d4ed8;
    }
    [data-testid="stCameraInput"] {
        position: relative; overflow: hidden; padding: 10px;
        background: #fff; border: 1px solid #cbd5e1;
    }
    [class*="st-key-kiosk_camera_"] [data-testid="stCameraInput"]::after {
        content: ""; position: absolute; pointer-events: none; z-index: 4;
        inset: 12% 17%;
        border: 1px solid transparent; border-radius: 46% 46% 40% 40%;
        background:
          linear-gradient(#2563eb,#2563eb) left top/22px 2px no-repeat,
          linear-gradient(#2563eb,#2563eb) left top/2px 22px no-repeat,
          linear-gradient(#2563eb,#2563eb) right top/22px 2px no-repeat,
          linear-gradient(#2563eb,#2563eb) right top/2px 22px no-repeat,
          linear-gradient(#2563eb,#2563eb) left bottom/22px 2px no-repeat,
          linear-gradient(#2563eb,#2563eb) left bottom/2px 22px no-repeat,
          linear-gradient(#2563eb,#2563eb) right bottom/22px 2px no-repeat,
          linear-gradient(#2563eb,#2563eb) right bottom/2px 22px no-repeat;
    }
    [class*="st-key-kiosk_camera_"] [data-testid="stCameraInput"]::before {
        content: ""; position: absolute; pointer-events: none; z-index: 5;
        left: 17%; right: 17%; height: 2px; top: 15%;
        background: #3b82f6; opacity: .55;
        animation: scanBeam 3.2s ease-in-out infinite alternate;
    }
    @keyframes scanBeam {
        from { transform: translateY(0); opacity: .2; }
        to { transform: translateY(300px); opacity: .65; }
    }
    .status-header {
        display:flex; justify-content:space-between; align-items:center; gap:1rem;
        margin: .2rem 0 1.25rem; padding: .85rem 1rem;
        border:1px solid #e2e8f0; border-radius:12px;
        background:#fff; box-shadow:0 1px 3px rgba(15,23,42,.05);
    }
    .brand-mark { color:#0f172a; font-weight:700; letter-spacing:.06em; font-size:.75rem; }
    .edge-status { display:flex; align-items:center; gap:.55rem; color:#64748b; font-size:.78rem; }
    .edge-dot { width:8px; height:8px; border-radius:50%; background:#16a34a; }
    .edge-dot.offline { background:#d97706; }
    .edge-dot.error { background:#e11d48; }
    .stage-heading { color:#0f172a; font:600 1.08rem Inter, ui-sans-serif, system-ui, sans-serif;
        margin:.4rem 0 .25rem; }
    .outcome-card {
        padding:1.15rem; border-radius:14px; margin:.45rem 0 1rem;
        background:#fff; border:1px solid #e2e8f0;
        box-shadow:0 1px 3px rgba(15,23,42,.06);
        animation:riseIn .22s ease both;
    }
    .outcome-card.allow { border-color:#a7f3d0; background:#f0fdf4; }
    .outcome-card.deny { border-color:#fecdd3; background:#fff1f2; }
    .outcome-card.verify { border-color:#fde68a; background:#fffbeb; }
    .outcome-card.idle { border-color:#bfdbfe; background:#eff6ff; }
    .result-source { display:inline-block; margin-bottom:.75rem; padding:.25rem .5rem;
        border-radius:999px; font-size:.62rem; font-weight:700; letter-spacing:.08em; }
    .result-source.simulated { background:#fef3c7; color:#92400e; }
    .result-source.live { background:#dcfce7; color:#166534; }
    .outcome-copy { display:inline-block; vertical-align:middle; margin-left:.7rem; }
    .outcome-icon { display:inline-grid; place-items:center; vertical-align:middle;
        width:38px; height:38px; border-radius:10px; font-size:1.3rem; font-weight:700; }
    .allow .outcome-icon { color:#047857; background:#d1fae5; }
    .deny .outcome-icon { color:#be123c; background:#ffe4e6; }
    .verify .outcome-icon { color:#b45309; background:#fef3c7; }
    .idle .outcome-icon { color:#1d4ed8; background:#dbeafe; }
    .outcome-kicker { font-size:.65rem; font-weight:700; letter-spacing:.12em;
        color:#64748b; text-transform:uppercase; }
    .outcome-copy h3 { margin:.12rem 0 0; font-size:1.18rem; color:#0f172a; }
    .outcome-copy p { margin:.12rem 0 0; font-size:.8rem; color:#475569; }
    .outcome-reason { border-top:1px solid rgba(100,116,139,.18);
        margin-top:.85rem; padding-top:.75rem; color:#334155; font-size:.87rem; }
    .confidence-row { display:flex; justify-content:space-between; margin-top:.85rem;
        color:#475569; font-size:.76rem; }
    .confidence-row strong { color:#0f172a; }
    .confidence-track { height:5px; background:#e2e8f0; border-radius:9px;
        margin-top:.4rem; overflow:hidden; }
    .confidence-track span { display:block; height:100%; border-radius:inherit;
        background:#059669; }
    .site-footer { display:flex; justify-content:space-between; gap:1rem; flex-wrap:wrap;
        margin-top:2rem; padding:1rem .25rem; border-top:1px solid #e2e8f0;
        color:#64748b; font-size:.72rem; }
    .site-footer strong { color:#334155; font-weight:600; }
    @keyframes riseIn { from { opacity:0; transform:translateY(4px); }
        to { opacity:1; transform:translateY(0); } }
    @media (prefers-reduced-motion: reduce) {
        *, *::before, *::after { animation-duration:.01ms !important; transition-duration:.01ms !important; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.sidebar.header("Kiosk Settings")
badge_id = st.sidebar.text_input("Badge ID", value="EMP-8942").strip()
api_base_url = st.sidebar.text_input("API base URL", value=API_BASE_URL).strip().rstrip("/")
offline_mode = st.sidebar.checkbox("Offline / Mock Demo Mode", value=True)
st.sidebar.caption(
    "Mock mode displays illustrative outcomes and sends no capture to the decision API."
)

if not api_base_url:
    st.sidebar.error("Enter a valid API base URL.")
elif (
    "session_nonce" not in st.session_state
    or st.session_state.get("nonce_base_url") != api_base_url
    or st.session_state.get("nonce_mode") != offline_mode
):
    try:
        st.session_state.session_nonce = get_session_nonce(
            offline=offline_mode,
            api_base_url=api_base_url,
        )
        st.session_state.nonce_base_url = api_base_url
        st.session_state.nonce_mode = offline_mode
        st.session_state.session_nonce_error = ""
    except (ValueError, requests.RequestException) as error:
        st.session_state.session_nonce = ""
        st.session_state.nonce_base_url = api_base_url
        st.session_state.nonce_mode = offline_mode
        st.session_state.session_nonce_error = str(error)

if st.session_state.get("session_nonce_error"):
    st.sidebar.warning(
        f"Session nonce unavailable: {st.session_state.session_nonce_error}"
    )
connection_error = bool(st.session_state.get("session_nonce_error"))
edge_class = "offline" if offline_mode else ("error" if connection_error else "")
edge_label = (
    "SIMULATION · outcomes are illustrative"
    if offline_mode
    else (
        "API session unavailable"
        if connection_error
        else "API session initialized · live API mode"
    )
)
st.markdown(
    '<header class="status-header"><div class="brand-mark">HID · ACCESS ASSURANCE</div>'
    f'<div class="edge-status"><span class="edge-dot {edge_class}"></span>'
    f'<span>{escape(edge_label)}</span><span>·</span><span>SESSION STATUS</span></div></header>',
    unsafe_allow_html=True,
)
st.title("Face Liveness & Authentication Kiosk")
st.caption(
    "Corporate identity verification · ≤ 2.0-second decision target · "
    + ("Presenter simulation" if offline_mode else "Configured decision API")
)
if "otp_attempt_count" not in st.session_state:
    reset_step_up_state()

scenario_names = [scenario["name"] for scenario in DEMO_SCENARIOS]
if "demo_scenario_select" not in st.session_state:
    st.session_state.demo_scenario_select = scenario_names[0]
if st.session_state.demo_scenario_select not in scenario_names:
    st.session_state.demo_scenario_select = scenario_names[0]

kiosk_tab, demo_tab = st.tabs(["Kiosk", "Demo & Rehearsal"])

with kiosk_tab:
    camera_column, result_column = st.columns([1.15, 1])
    with camera_column:
        st.markdown('<div class="stage-heading">Identity capture</div>', unsafe_allow_html=True)
        st.caption("Center your face inside the targeting frame.")
        camera_image = st.camera_input(
            "Position your face in the frame",
            key=f"kiosk_camera_{int(st.session_state.get('camera_epoch', 0))}",
        )
        authenticate_clicked = st.button(
            "Authenticate Kiosk Access",
            type="primary",
            disabled=(
                camera_image is None
                or not badge_id
                or not api_base_url
                or bool(st.session_state.get("otp_locked", False))
            ),
            use_container_width=True,
        )
        if st.session_state.get("otp_locked", False):
            st.error("Kiosk access is locked after three step-up failures.")
        if not badge_id:
            st.warning("Enter a Badge ID before authenticating.")

    with result_column:
        st.markdown('<div class="stage-heading">Security decision</div>', unsafe_allow_html=True)
        attempt = st.session_state.get("last_attempt")
        if attempt:
            _render_attempt(attempt, badge_id, api_base_url, offline_mode)
        else:
            st.markdown(
                '<section class="outcome-card idle">'
                '<div class="outcome-icon">◎</div>'
                '<div class="outcome-copy"><div class="outcome-kicker">SYSTEM READY</div>'
                '<h3>Ready to authenticate</h3>'
                '<p>Capture a face image to begin identity and liveness checks.</p>'
                '</div></section>',
                unsafe_allow_html=True,
            )

    if authenticate_clicked and camera_image is not None:
        _authenticate(
            camera_image,
            badge_id,
            offline_mode,
            api_base_url,
            DEMO_SCENARIOS[scenario_names.index(st.session_state.demo_scenario_select)],
        )

with demo_tab:
    st.subheader("Stage demo scenarios")
    st.caption(
        "Rehearse seven outcomes without a backend dependency. Each result is labeled "
        "as simulated, uses illustrative confidence, and does not send a camera frame."
    )
    previous_column, selection_column, next_column = st.columns([1, 3, 1])
    with previous_column:
        st.button(
            "← Previous",
            use_container_width=True,
            on_click=_shift_demo_scenario,
            args=(-1,),
        )
    with selection_column:
        selected_name = st.selectbox(
            "Scenario",
            scenario_names,
            key="demo_scenario_select",
        )
    with next_column:
        st.button(
            "Next →",
            use_container_width=True,
            on_click=_shift_demo_scenario,
            args=(1,),
        )

    selected_index = scenario_names.index(selected_name)
    selected_scenario = DEMO_SCENARIOS[selected_index]
    st.write(
        f"**Scenario {selected_index + 1}/{len(DEMO_SCENARIOS)}:** "
        f"{selected_scenario['name']}"
    )
    if st.button("Run demo scenario instantly", type="primary"):
        now = datetime.now().astimezone()
        start_counter = time.perf_counter()
        time.sleep(MOCK_RESPONSE_SECONDS)
        st.session_state.demo_result = {
            "attempt_id": secrets.token_hex(8),
            "started_at": now.isoformat(timespec="seconds"),
            "outcome": selected_scenario["decision"],
            "reason": selected_scenario["reason"],
            "confidence": selected_scenario["confidence"],
            "source": "simulation",
            "scenario": selected_scenario["name"],
            "session_id": "demo-session",
            "challenge_type": "",
            "elapsed_seconds": time.perf_counter() - start_counter,
        }

    demo_result = st.session_state.get("demo_result")
    if demo_result:
        st.divider()
        st.caption(f"Simulated attempt: {demo_result['started_at']} · no backend request")
        _render_attempt(demo_result, badge_id, api_base_url, offline=True)

attempt_for_footer = st.session_state.get("last_attempt") or st.session_state.get("demo_result")
latency_text = (
    f"{float(attempt_for_footer['elapsed_seconds']):.2f}s decision"
    if attempt_for_footer
    else "Decision latency ≤ 2.0s target"
)
footer_connection = (
    "Simulation only · no API request"
    if offline_mode
    else (
        "API session unavailable"
        if connection_error
        else "API session initialized · decision endpoint not health-checked"
    )
)
st.markdown(
    '<footer class="site-footer">'
    f'<span><strong>{escape(latency_text)}</strong> · target ≤ 2.0s</span>'
    f'<span>{escape(footer_connection)} · Streamlit · OpenCV · Requests</span>'
    '<span>Camera capture is submitted only in configured live API mode</span></footer>',
    unsafe_allow_html=True,
)
