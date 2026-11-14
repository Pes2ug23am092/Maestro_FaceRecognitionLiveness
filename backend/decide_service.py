"""POST /decide pipeline (integrated):  nonce -> Tier 1 liveness (B) -> Rekognition (quality/labels/identity, A)
-> fusion (D, thresholds.v2) -> context policy (schedule/zone/calendar) -> audit.   Fails closed on any error.

Response shape is a superset of A's original (outcome/liveness/similarity/tier/reasons/mode/timings/event_id) so the
Streamlit UI keeps working; `detail` carries the evidence for the UI/report.
"""
import hashlib
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import boto3

from backend import liveness_service as live
from backend import policy_engine
from backend.campus_adapter import load_policy_seed, user_from_item, zone_risk
from backend.config import (TIER1_QUALITY_MIN, COLLECTION_ID, FACE_QUALITY_MIN_BRIGHTNESS, FACE_QUALITY_MIN_SHARPNESS, REGION,
                            TABLE_DECISIONS, TABLE_USERS)
from backend.fusion import decide as fusion_decide
from backend.fusion import load_policy_thresholds
from backend import lockout_service as lockout
from backend.session_service import validate_and_consume_nonce

ROOT = Path(__file__).resolve().parents[1]
_TH_PATH = ROOT / "config" / "thresholds.v2.json"
_th_cache = {"mtime": 0.0, "value": None}


def thresholds():
    """Hot-reload: edit config/thresholds.v2.json while the server runs (no restart); falls back to the last good copy."""
    try:
        m = _TH_PATH.stat().st_mtime
        if m != _th_cache["mtime"]:
            _th_cache.update(mtime=m, value=load_policy_thresholds(_TH_PATH))
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] thresholds reload failed, using last good: {e}")
        if _th_cache["value"] is None:
            raise
    return _th_cache["value"]


RISK_LEVEL = {"low": "lobby", "med": "office", "high": "server_room"}
MAX_FRAMES = 12

rekognition = boto3.client("rekognition", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
users_table = dynamodb.Table(TABLE_USERS)
decisions_table = dynamodb.Table(TABLE_DECISIONS)

# Rekognition labels meaning an object HELD IN FRONT of the face. "Screen/Monitor/TV/Electronics" are deliberately NOT here:
# a monitor behind a genuine user is common and caused false denials (seen on CASIA clip 2). Screen replays are handled
# by the liveness model (video_replay/screen_photo classes) instead, and printed photos are mostly NOT labelled paper at all.
SPOOF_LABELS = {"mobile phone", "cell phone", "phone", "tablet computer", "paper", "poster", "picture frame", "photograph"}


def _timed(fn):
    t0 = time.time()
    try:
        return {"status": "OK", **fn(), "elapsed_ms": (time.time() - t0) * 1000}
    except Exception as e:  # noqa: BLE001
        return {"status": "ERROR", "error": type(e).__name__, "elapsed_ms": (time.time() - t0) * 1000}


def call_detect_faces(img: bytes) -> dict:
    return _timed(lambda: {"faces": rekognition.detect_faces(Image={"Bytes": img}, Attributes=["ALL"]).get("FaceDetails", [])})


def call_detect_labels(img: bytes) -> dict:
    return _timed(lambda: {"labels": rekognition.detect_labels(Image={"Bytes": img}, MaxLabels=15, MinConfidence=60.0).get("Labels", [])})


def call_search_faces(img: bytes) -> dict:
    return _timed(lambda: {"matches": rekognition.search_faces_by_image(
        CollectionId=COLLECTION_ID, Image={"Bytes": img}, MaxFaces=3, FaceMatchThreshold=60.0).get("FaceMatches", [])})


def _verdict(fd) -> str:
    r = set(fd.reasons)
    if r & {"hard_spoof_veto", "phone_or_paper_veto", "spoof_score_veto", "liveness_below_deny_threshold"}:
        return "SPOOF"
    if "identity_below_deny_threshold" in r:
        return "MISMATCH"
    return {"ALLOW": "CONFIDENT", "STEP_UP": "LOW"}.get(fd.outcome, "SPOOF")


def evaluate(bgr, badge_id: str, zone_id: str, now: str | None, timings: dict, tier2_passed: bool | None = None) -> dict:
    """Biometric + policy evaluation shared by /decide (tier2_passed=None) and Tier 2 verification (True)."""
    # ---- Tier 1 liveness on the whole burst (B)
    t0 = time.time()
    t1 = live.score_frames(bgr)
    timings["liveness_ms"] = round((time.time() - t0) * 1000, 2)

    # ---- Rekognition on the BEST frame (quality, labels, 1:N search) in parallel + user lookup
    best_jpeg = live.encode_jpeg(bgr[t1["best_frame"]])
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_faces, f_labels = ex.submit(call_detect_faces, best_jpeg), ex.submit(call_detect_labels, best_jpeg)
        f_search = ex.submit(call_search_faces, best_jpeg)
        f_user = ex.submit(lambda: users_table.get_item(Key={"badge_id": badge_id}).get("Item"))
        r_faces, r_labels, r_search, item = f_faces.result(), f_labels.result(), f_search.result(), f_user.result()
    timings["rekognition_ms"] = round((time.time() - t0) * 1000, 2)

    t0 = time.time()
    faces = r_faces.get("faces", [])
    q = faces[0].get("Quality", {}) if len(faces) == 1 else {}
    rek_quality_ok = bool(q) and q.get("Sharpness", 0) >= FACE_QUALITY_MIN_SHARPNESS \
        and q.get("Brightness", 0) >= FACE_QUALITY_MIN_BRIGHTNESS
    quality_ok = t1["quality"]["face_found"] and t1["quality"]["overall"] >= TIER1_QUALITY_MIN and rek_quality_ok
    phone_paper = max([lbl["Confidence"] / 100 for lbl in r_labels.get("labels", [])
                       if lbl.get("Name", "").lower() in SPOOF_LABELS] or [0.0])
    matches = r_search.get("matches", [])
    matched_badge = matches[0]["Face"].get("ExternalImageId") if matches else None
    similarity = float(matches[0]["Similarity"]) / 100 if matches and matched_badge == badge_id else 0.0

    user = user_from_item(item)
    zone_level = zone_risk(zone_id)

    # ---- biometric verdict: no face -> recapture (never "spoof"); else D's fusion
    if not t1["quality"]["face_found"] or (r_faces.get("status") == "OK" and len(faces) != 1):
        verdict, fusion_reasons = "LOW", ["no_single_face"]
    else:
        fd = fusion_decide(spoof_prob=min(1.0, max(0.0, t1["spoof_prob"])),
                           signals={"hard_spoof_veto": bool(t1["veto"]["any"]), "phone_paper_confidence": phone_paper},
                           quality=bool(quality_ok), similarity=similarity, thresholds=thresholds(),
                           risk_level=RISK_LEVEL[zone_level], tier2_passed=tier2_passed)
        verdict, fusion_reasons = _verdict(fd), list(fd.reasons)

    # ---- context policy (schedule / zone / calendar / user status)
    when = policy_engine.parse_time(now) if now else datetime.now()
    res = policy_engine.decide({"verdict": verdict}, user, zone_id, when, load_policy_seed())
    outcome, reasons = res["outcome"], res["reasons"]
    timings["fusion_ms"] = round((time.time() - t0) * 1000, 2)
    return {"outcome": outcome, "reasons": reasons, "verdict": verdict, "fusion_reasons": fusion_reasons, "t1": t1,
            "similarity": similarity, "phone_paper": phone_paper, "zone_level": zone_level}


def decide_access(nonce: str, frames: list[str], badge_id: str, zone_id: str, kiosk_id: str = "KIOSK_MAIN",
                  now: str | None = None) -> tuple[int, dict]:
    t_start = time.time()
    timings: dict = {}

    valid, reason = validate_and_consume_nonce(nonce)
    if not valid:
        return 400, {"error": "INVALID_NONCE", "reason": reason, "message": f"Nonce validation failed: {reason}"}
    if lockout.is_locked(badge_id):
        return 200, {"outcome": "DENY", "reasons": ["ATTEMPTS_EXCEEDED"], "liveness": None, "similarity": None, "tier": 1,
                     "mode": "online", "timings": {"total_ms": round((time.time() - t_start) * 1000, 2)}, "verdict": "LOCKED"}
    if not frames:
        return 400, {"error": "NO_FRAMES_PROVIDED"}
    bgr = [f for f in (live.decode_b64(x) for x in frames[:MAX_FRAMES]) if f is not None]
    if not bgr:
        return 400, {"error": "INVALID_BASE64_FRAME"}
    timings["upload_ms"] = round((time.time() - t_start) * 1000, 2)

    h = hashlib.sha256(nonce.encode())
    for f in frames[:MAX_FRAMES]:
        h.update(f.encode())
    frame_hash, user_hash = h.hexdigest(), hashlib.sha256(badge_id.encode()).hexdigest()[:16]

    try:
        ev = evaluate(bgr, badge_id, zone_id, now, timings)
        outcome, reasons, verdict, fusion_reasons = ev["outcome"], ev["reasons"], ev["verdict"], ev["fusion_reasons"]
        t1, similarity, phone_paper, zone_level = ev["t1"], ev["similarity"], ev["phone_paper"], ev["zone_level"]
    except Exception as e:  # noqa: BLE001  fail closed
        print(f"[ERROR] decide failed closed: {type(e).__name__}: {e}")
        outcome, reasons, verdict, fusion_reasons = "DENY", ["INTERNAL_ERROR_FAIL_CLOSED"], "ERROR", []
        t1 = {"spoof_prob": 1.0, "quality": {"overall": 0.0}, "signals": {}, "attack_probs": None}
        similarity, phone_paper, zone_level = 0.0, 0.0, "high"

    try:  # plan: 3 failed biometric attempts -> lockout; a success clears the counter
        if outcome == "ALLOW":
            lockout.clear(badge_id)
        elif outcome in ("DENY", "STEP_UP") and lockout.counts(reasons) and lockout.record_failure(badge_id):
            outcome, reasons = "DENY", ["ATTEMPTS_EXCEEDED"]
    except Exception as e:  # noqa: BLE001  never let bookkeeping change a deny into an allow
        print(f"[WARN] lockout bookkeeping failed: {e}")

    timings["total_ms"] = round((time.time() - t_start) * 1000, 2)
    liveness = round(1 - float(t1["spoof_prob"]), 4)
    event_id = str(uuid.uuid4())
    try:
        decisions_table.put_item(Item={
            "event_id": event_id, "ts": int(time.time()), "kiosk_id": kiosk_id, "zone_id": zone_id,
            "user_hash": user_hash, "frame_hash": frame_hash, "outcome": outcome, "reasons": reasons,
            "verdict": verdict, "mode": "online", "liveness": str(liveness), "similarity": str(round(similarity, 4)),
            "model": live.MODEL_NAME or "cues-only", "timings": {k: str(v) for k, v in timings.items()}})
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Audit logging write failed: {e}")

    return 200, {
        "outcome": outcome, "liveness": liveness, "similarity": round(similarity, 4), "tier": 1,
        "reasons": reasons, "mode": "online", "timings": timings, "event_id": event_id, "verdict": verdict,
        "detail": {"spoof_prob": round(float(t1["spoof_prob"]), 4), "quality": round(float(t1["quality"]["overall"]), 3),
                   "phone_paper_confidence": round(phone_paper, 3), "fusion_reasons": fusion_reasons,
                   "risk_level": zone_level, "attack_probs": t1.get("attack_probs"),
                   "signals": {k: round(v, 3) for k, v in t1.get("signals", {}).items() if isinstance(v, float)}},
    }


def warm_up() -> dict:
    """Pre-open the Rekognition connections and run the model once, so the FIRST real request is not the slow one."""
    import numpy as np
    t0 = time.time()
    img = np.full((160, 160, 3), 128, np.uint8)
    jpg = live.encode_jpeg(img)
    live.score_frames([img])
    for fn in (call_detect_faces, call_detect_labels, call_search_faces):
        fn(jpg)
    return {"warm_ms": round((time.time() - t0) * 1000)}
