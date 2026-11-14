import os
import boto3
from fastapi import FastAPI, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from backend.session_service import create_session
from backend.decide_service import decide_access
from backend.enrolment_service import enroll_user
from backend.policy_service import get_policy_bundle_payload, issue_credential
from backend.sync_service import sync_audit_batch
from backend.otp_service import generate_otp_for_user, verify_otp
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

class EnrollRequest(BaseModel):
    badge_id: str
    image_base64: str
    group_id: Optional[str] = "GRP_EMPLOYEE"

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

@app.get("/health")
def health():
    return {"status": "healthy", "service": "kiosk-auth-backend", "region": REGION}

@app.post("/session")
def post_session():
    return create_session()

@app.post("/decide")
def post_decide(req: DecideRequest):
    code, payload = decide_access(
        nonce=req.nonce,
        frames=req.frames,
        badge_id=req.badge_id,
        zone_id=req.zone_id,
        kiosk_id=req.kiosk_id or "KIOSK_MAIN"
    )
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
    import base64
    try:
        img_bytes = base64.b64decode(req.image_base64)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image")
    res = enroll_user(req.badge_id, img_bytes, req.group_id or "GRP_EMPLOYEE")
    if not res["success"]:
        raise HTTPException(status_code=400, detail=res)
    return res

# Handler for AWS Lambda (if deploying via Mangum / API Gateway)
try:
    from mangum import Mangum
    handler = Mangum(app)
except ImportError:
    handler = None
