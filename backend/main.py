import os
import base64
import secrets
import boto3
from fastapi import FastAPI, HTTPException, Request, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Any

from backend.session_service import create_session
from backend.decide_service import decide_access
from backend.enrolment_service import enroll_user
from backend.policy_service import get_policy_bundle_payload, issue_credential
from backend.sync_service import sync_audit_batch
from backend.otp_service import generate_otp_for_user, verify_otp
from backend.tier2_service import issue_challenge, verify_tier2
from backend.kiosk_page import router as kiosk_router
from backend.config import REGION

app = FastAPI(
    title="HID Face Liveness & Authentication Kiosk Backend",
    version="1.0.0",
    description="Cloud Backend service for member A (Cloud/Backend Lead)."
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Models
class DecideRequest(BaseModel):
    nonce: str
    frames: List[str]
    badge_id: str
    zone_id: str
    kiosk_id: Optional[str] = "KIOSK_MAIN"
    now: Optional[str] = None  # demo clock, e.g. "Tue 23:30" or "2026-11-20 10:00"

class EnrollRequest(BaseModel):
    badge_id: str
    image_base64: str
    group_id: Optional[str] = None  # only used for NEW users; existing users keep their group/status

class OtpGenerateRequest(BaseModel):
    badge_id: str
    kiosk_id: Optional[str] = "KIOSK_MAIN"

class OtpVerifyRequest(BaseModel):
    badge_id: str
    kiosk_id: Optional[str] = "KIOSK_MAIN"
    otp_code: str

class CredentialRequest(BaseModel):
    badge_id: str

class SyncEvent(BaseModel):
    event_id: str
    seq: int
    ts: int
    badge_id: Optional[str] = None
    user_hash: Optional[str] = None
    outcome: str
    prev_hash: Optional[str] = ""
    hash: Optional[str] = ""

class SyncBatchRequest(BaseModel):
    kiosk_id: str
    batch_id: str
    events: List[SyncEvent]

@app.on_event("startup")
def _warm():
    import os
    import threading
    from backend.decide_service import keep_warm_loop, warm_up
    print("[startup] warm-up:", warm_up())
    if not os.getenv("AWS_LAMBDA_FUNCTION_NAME"):  # idle AWS connections go cold; keep them warm on a long-lived server
        threading.Thread(target=keep_warm_loop, daemon=True).start()


@app.get("/health")
def health():
    return {"status": "healthy", "service": "kiosk-auth-backend", "region": REGION}

@app.post("/session")
def post_session():
    return create_session()

@app.post("/decide")
async def post_decide(request: Request):
    content_type = request.headers.get("content-type", "")
    
    if "multipart/form-data" in content_type:
        form = await request.form()
        badge_id = str(form.get("badge_id", ""))
        nonce = str(form.get("nonce", ""))
        zone_id = str(form.get("zone_id", "ZONE_ENTRY_MAIN"))
        kiosk_id = str(form.get("kiosk_id", "KIOSK_MAIN"))
        frame_file = form.get("frame")
        if not frame_file or not hasattr(frame_file, "read"):
            raise HTTPException(status_code=400, detail="Missing 'frame' image in multipart data")
        image_bytes = await frame_file.read()
        frame_b64 = base64.b64encode(image_bytes).decode("utf-8")
        frames = [frame_b64]
        now = str(form.get("now") or "") or None
    else:
        body = await request.json()
        req = DecideRequest(**body)
        nonce = req.nonce
        frames = req.frames
        badge_id = req.badge_id
        zone_id = req.zone_id
        kiosk_id = req.kiosk_id or "KIOSK_MAIN"
        now = req.now

    code, payload = decide_access(
        nonce=nonce,
        frames=frames,
        badge_id=badge_id,
        zone_id=zone_id,
        kiosk_id=kiosk_id,
        now=now,
    )

    if code == 200:
        # Augment payload with fields expected by Member C's Streamlit _normalize_decision
        outcome_map = {
            "ALLOW": "ALLOW",
            "STEP_UP": "REQUEST VERIFICATION",
            "DENY": "DENY"
        }
        payload["decision"] = outcome_map.get(payload.get("outcome", "DENY"), "DENY")
        reasons_list = payload.get("reasons", [])
        payload["reason"] = ", ".join(reasons_list) if reasons_list else "Access evaluation completed"
        payload["message"] = payload["reason"]
        payload["confidence"] = payload.get("liveness", 0.95)

    if code != 200:
        raise HTTPException(status_code=code, detail=payload)
    return payload

@app.get("/policy-bundle")
def get_policy_bundle():
    return get_policy_bundle_payload()

@app.post("/credential")
def post_credential(req: CredentialRequest):
    return issue_credential(req.badge_id)

@app.post("/sync")
def post_sync(req: SyncBatchRequest):
    events_dicts = [e.model_dump() for e in req.events]
    return sync_audit_batch(req.kiosk_id, req.batch_id, events_dicts)

@app.post("/generate-otp")
def post_generate_otp(req: OtpGenerateRequest):
    return generate_otp_for_user(req.badge_id, req.kiosk_id or "KIOSK_MAIN")

@app.post("/verify-otp")
def post_verify_otp(req: OtpVerifyRequest):
    res = verify_otp(req.badge_id, req.kiosk_id or "KIOSK_MAIN", req.otp_code)
    if res["status"] == "LOCKED_OUT":
        raise HTTPException(status_code=403, detail=res)
    return res

@app.post("/face-liveness-session")
def post_face_liveness_session():
    rek = boto3.client("rekognition", region_name=REGION)
    res = rek.create_face_liveness_session()
    return {"session_id": res["SessionId"]}

@app.post("/enroll")
def post_enroll(req: EnrollRequest):
    try:
        img_bytes = base64.b64decode(req.image_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image")
    res = enroll_user(req.badge_id, img_bytes, req.group_id)
    if not res["success"]:
        raise HTTPException(status_code=400, detail=res)
    try:  # first search right after indexing was measured at ~4 s: pay that here, not on the user's first scan
        from backend.decide_service import call_search_faces
        call_search_faces(img_bytes)
    except Exception:  # noqa: BLE001
        pass
    return res

# ---------------------------------------------------------------------------
# Multipart / Frontend-compatible endpoints (Streamlit uses multipart)
# ---------------------------------------------------------------------------




@app.post("/challenge")
def post_challenge():
    """Tier 2: issue a nonce-bound challenge (random colour-flash sequence + head-turn). Single use, expires."""
    return issue_challenge()


@app.post("/challenge/verify")
async def post_challenge_verify(request: Request):
    """Tier 2 verification. JSON {challenge_id, frames[], t_ms[], badge_id, zone_id, now?} (timestamped burst).
    The legacy single-frame multipart call can NOT prove a colour sync or a head turn, so it fails closed."""
    if "multipart/form-data" in request.headers.get("content-type", ""):
        return {"verified": False, "status": "failed",
                "message": "Tier 2 needs a timestamped multi-frame burst (JSON). A single frame cannot prove a head turn "
                           "or colour sync. Use /kiosk or POST JSON to /challenge/verify."}
    body = await request.json()
    code, payload = verify_tier2(body)
    if code != 200:
        raise HTTPException(status_code=code, detail=payload)
    return payload


@app.post("/challenge/otp")
async def post_challenge_otp(
    badge_id: str = Form(...),
    nonce: str = Form(...),
    otp: str = Form(...),
):
    """Step-up OTP verification endpoint (form-data compatible)."""
    res = verify_otp(badge_id, "KIOSK_MAIN", otp)
    if res["status"] == "LOCKED_OUT":
        raise HTTPException(status_code=403, detail=res)
    is_verified = res["status"] == "VERIFIED"
    return {
        "verified": is_verified,
        "status": "verified" if is_verified else "invalid",
        "message": res.get("message", ""),
        "attempts_left": res.get("attempts_left", 0),
    }


@app.post("/liveness/validate")
async def post_liveness_validate(
    badge_id: str = Form(...),
    session_id: str = Form(...),
    liveness_frame: UploadFile = File(...),
):
    """Tier 3 liveness: Rekognition Face Liveness session result. FAILS CLOSED on any error (never a demo pass)."""
    rek = boto3.client("rekognition", region_name=REGION)
    try:
        res = rek.get_face_liveness_session_results(SessionId=session_id)
    except Exception as e:  # noqa: BLE001
        return {"verified": False, "status": "failed", "confidence": 0.0,
                "message": f"Face Liveness session could not be verified ({type(e).__name__})."}
    confidence = res.get("Confidence", 0.0)
    status = res.get("Status", "FAILED")
    verified = status == "SUCCEEDED" and confidence >= 90.0
    return {"verified": verified, "status": "verified" if verified else "failed", "confidence": confidence,
            "liveness_status": status,
            "message": f"Tier 3 liveness confidence: {confidence:.1f}% ({'PASSED' if verified else 'FAILED'})"}


@app.get("/face-liveness-session/{session_id}")
def get_liveness_session_results(session_id: str):
    """Retrieve Rekognition Face Liveness session results by ID."""
    rek = boto3.client("rekognition", region_name=REGION)
    try:
        res = rek.get_face_liveness_session_results(SessionId=session_id)
        return {
            "session_id": session_id,
            "confidence": res.get("Confidence", 0.0),
            "status": res.get("Status"),
        }
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Session not found or expired: {e}")

app.include_router(kiosk_router)

# Handler for AWS Lambda (if deploying via Mangum / API Gateway)
try:
    from mangum import Mangum
    _mangum = Mangum(app)

    def handler(event, context):
        """Lambda entry: API Gateway HTTP API events go to FastAPI; an EventBridge schedule just keeps the instance + connections warm."""
        if isinstance(event, dict) and event.get("source") == "aws.events":
            from backend.decide_service import warm_pool
            warm_pool()
            return {"warmed": True}
        return _mangum(event, context)
except ImportError:
    handler = None
