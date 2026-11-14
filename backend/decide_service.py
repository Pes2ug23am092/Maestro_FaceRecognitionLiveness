"""POST /decide pipeline (integrated, latency-optimised):

   in parallel:  nonce check (DynamoDB) | lockout check | user lookup | Tier 1 liveness (B) -> Rekognition SearchFaces
   then:         fusion (D, thresholds.v2) -> schedule/zone/calendar policy -> audit (background)       Fails closed.

Latency notes (measured on real AWS): the old sequential version spent ~0.3-0.5 s on the nonce write, ~0.1-0.2 s on lockout,
and 3 Rekognition calls (DetectFaces ALL + DetectLabels + SearchFaces) that occasionally took ~3 s. Now only SearchFaces
is called (quality comes from Tier 1; the label rule was ineffective and caused a false deny), and DynamoDB work overlaps
with the model. Set USE_REK_EXTRAS=1 to re-enable DetectFaces/DetectLabels.
Response shape is a superset of A's original so the Streamlit UI keeps working; `detail` carries the evidence.
"""
import hashlib
import os
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path

import boto3
from botocore.config import Config

from backend import liveness_service as live
from backend import lockout_service as lockout
from backend import policy_engine
from backend.campus_adapter import load_policy_seed, user_from_item, zone_risk
from backend.config import (COLLECTION_ID, FACE_QUALITY_MIN_BRIGHTNESS, FACE_QUALITY_MIN_SHARPNESS, REGION,
                            TABLE_DECISIONS, TABLE_USERS, TIER1_QUALITY_MIN)
from backend.fusion import decide as fusion_decide
from backend.fusion import load_policy_thresholds
from backend.session_service import validate_and_consume_nonce

ROOT = Path(__file__).resolve().parents[1]
_TH_PATH = ROOT / "config" / "thresholds.v2.json"
_th_cache = {"mtime": 0.0, "value": None}
RISK_LEVEL = {"low": "lobby", "med": "office", "high": "server_room"}
MAX_FRAMES = 12
USE_REK_EXTRAS = os.getenv("USE_REK_EXTRAS", "0") == "1"
ON_LAMBDA = bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))  # Lambda may freeze after responding: do writes synchronously there

_cfg = Config(retries={"max_attempts": 2, "mode": "standard"}, connect_timeout=5, read_timeout=8, max_pool_connections=32,
              tcp_keepalive=True)
rekognition = boto3.client("rekognition", region_name=REGION, config=_cfg)
dynamodb = boto3.resource("dynamodb", region_name=REGION, config=_cfg)
users_table = dynamodb.Table(TABLE_USERS)
decisions_table = dynamodb.Table(TABLE_DECISIONS)
_pool = ThreadPoolExecutor(max_workers=12)

# Only used when USE_REK_EXTRAS=1 (objects held in front of the face)
SPOOF_LABELS = {"mobile phone", "cell phone", "phone", "tablet computer", "paper", "poster", "picture frame", "photograph"}


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
        CollectionId=COLLECTION_ID, Image={"Bytes": img}, MaxFaces=10, FaceMatchThreshold=60.0).get("FaceMatches", [])})


def search_hedged(jpeg: bytes, hedge_after: float = 0.9) -> dict:
    """Tail-latency hedge: one slow/new connection can add 1.5-2.5 s. If the search has not answered after `hedge_after`
    seconds, send an identical second search on another connection and take the first SUCCESSFUL answer."""
    f1 = _pool.submit(call_search_faces, jpeg)
    done, _ = wait([f1], timeout=hedge_after)
    if done and f1.result().get("status") == "OK":
        return f1.result()
    pending = {f1, _pool.submit(call_search_faces, jpeg)}
    last = None
    while pending:
        done, pending = wait(pending, return_when=FIRST_COMPLETED)
        for f in done:
            last = f.result()
            if last.get("status") == "OK":
                return last
    return last or {"status": "ERROR", "matches": []}


def _get_user(badge_id: str):
    return users_table.get_item(Key={"badge_id": badge_id}).get("Item")


def _verdict(fd) -> str:
    r = set(fd.reasons)
    if r & {"hard_spoof_veto", "phone_or_paper_veto", "spoof_score_veto", "liveness_below_deny_threshold"}:
        return "SPOOF"
    if "identity_below_deny_threshold" in r:
        return "MISMATCH"
    return {"ALLOW": "CONFIDENT", "STEP_UP": "LOW"}.get(fd.outcome, "SPOOF")


def evaluate(bgr, badge_id: str, zone_id: str, now: str | None, timings: dict, tier2_passed: bool | None = None) -> dict:
    """Biometric + policy evaluation shared by /decide (tier2_passed=None) and Tier 2 verification (True)."""
    f_user = _pool.submit(_get_user, badge_id)  # overlaps with the model

    t0 = time.time()
    t1 = live.score_frames(bgr)
    timings["liveness_ms"] = round((time.time() - t0) * 1000, 2)

    best_jpeg = live.encode_jpeg(bgr[t1["best_frame"]], 85)
    t0 = time.time()
    f_search = _pool.submit(search_hedged, best_jpeg)
    f_faces = _pool.submit(call_detect_faces, best_jpeg) if USE_REK_EXTRAS else None
    f_labels = _pool.submit(call_detect_labels, best_jpeg) if USE_REK_EXTRAS else None
    r_search = f_search.result()
    item = f_user.result()
    timings["rekognition_ms"] = round((time.time() - t0) * 1000, 2)

    t0 = time.time()
    phone_paper, rek_quality_ok, multi_face = 0.0, True, False
    if USE_REK_EXTRAS:
        r_faces, r_labels = f_faces.result(), f_labels.result()
        faces = r_faces.get("faces", [])
        multi_face = r_faces.get("status") == "OK" and len(faces) != 1
        q = faces[0].get("Quality", {}) if len(faces) == 1 else {}
        rek_quality_ok = bool(q) and q.get("Sharpness", 0) >= FACE_QUALITY_MIN_SHARPNESS \
            and q.get("Brightness", 0) >= FACE_QUALITY_MIN_BRIGHTNESS
        phone_paper = max([lbl["Confidence"] / 100 for lbl in r_labels.get("labels", [])
                           if lbl.get("Name", "").lower() in SPOOF_LABELS] or [0.0])
    quality_ok = t1["quality"]["face_found"] and t1["quality"]["overall"] >= TIER1_QUALITY_MIN and rek_quality_ok

    # best match FOR THE CLAIMED BADGE (one face may be enrolled under several badges)
    mine = [m for m in r_search.get("matches", []) if m["Face"].get("ExternalImageId") == badge_id]
    similarity = max((float(m["Similarity"]) for m in mine), default=0.0) / 100

    user = user_from_item(item)
    zone_level = zone_risk(zone_id)

    if not t1["quality"]["face_found"] or multi_face:
        verdict, fusion_reasons = "LOW", ["no_single_face"]  # recapture, never "spoof"
    else:
        fd = fusion_decide(spoof_prob=min(1.0, max(0.0, t1["spoof_prob"])),
                           signals={"hard_spoof_veto": bool(t1["veto"]["any"]), "phone_paper_confidence": phone_paper},
                           quality=bool(quality_ok), similarity=similarity, thresholds=thresholds(),
                           risk_level=RISK_LEVEL[zone_level], tier2_passed=tier2_passed)
        verdict, fusion_reasons = _verdict(fd), list(fd.reasons)

    when = policy_engine.parse_time(now) if now else datetime.now()
    res = policy_engine.decide({"verdict": verdict}, user, zone_id, when, load_policy_seed())
    timings["fusion_ms"] = round((time.time() - t0) * 1000, 2)
    return {"outcome": res["outcome"], "reasons": res["reasons"], "verdict": verdict, "fusion_reasons": fusion_reasons,
            "t1": t1, "similarity": similarity, "phone_paper": phone_paper, "zone_level": zone_level}


def _write_audit(item: dict) -> None:
    try:
        decisions_table.put_item(Item=item)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Audit logging write failed: {e}")


def _bookkeeping(badge_id: str, outcome: str, reasons: list[str]) -> None:
    try:
        if outcome == "ALLOW":
            lockout.clear(badge_id)
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] lockout bookkeeping failed: {e}")


def _bg(fn, *a):
    if ON_LAMBDA:
        fn(*a)
    else:
        _pool.submit(fn, *a)


def decide_access(nonce: str, frames: list[str], badge_id: str, zone_id: str, kiosk_id: str = "KIOSK_MAIN",
                  now: str | None = None) -> tuple[int, dict]:
    t_start = time.time()
    timings: dict = {}
    if not frames:
        return 400, {"error": "NO_FRAMES_PROVIDED"}
    bgr = [f for f in (live.decode_b64(x) for x in frames[:MAX_FRAMES]) if f is not None]
    if not bgr:
        return 400, {"error": "INVALID_BASE64_FRAME"}
    timings["decode_ms"] = round((time.time() - t_start) * 1000, 2)

    # DynamoDB checks run WHILE the model and Rekognition work (no side effects in evaluate())
    f_nonce = _pool.submit(validate_and_consume_nonce, nonce)
    f_lock = _pool.submit(lockout.is_locked, badge_id)

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

    t0 = time.time()
    valid, reason = f_nonce.result()
    if not valid:  # a replayed / expired nonce: discard everything computed above
        return 400, {"error": "INVALID_NONCE", "reason": reason, "message": f"Nonce validation failed: {reason}"}
    if f_lock.result():
        return 200, {"outcome": "DENY", "reasons": ["ATTEMPTS_EXCEEDED"], "liveness": None, "similarity": None, "tier": 1,
                     "mode": "online", "timings": {"total_ms": round((time.time() - t_start) * 1000, 2)}, "verdict": "LOCKED"}
    timings["checks_wait_ms"] = round((time.time() - t0) * 1000, 2)  # time still waiting for the parallel DynamoDB checks

    try:  # failures must update the counter synchronously (the 3rd failure answers ATTEMPTS_EXCEEDED); success clears async
        if outcome in ("DENY", "STEP_UP") and lockout.counts(reasons) and lockout.record_failure(badge_id):
            outcome, reasons = "DENY", ["ATTEMPTS_EXCEEDED"]
    except Exception as e:  # noqa: BLE001  never let bookkeeping change a deny into an allow
        print(f"[WARN] lockout bookkeeping failed: {e}")
    _bg(_bookkeeping, badge_id, outcome, reasons)

    timings["total_ms"] = round((time.time() - t_start) * 1000, 2)
    liveness = round(1 - float(t1["spoof_prob"]), 4)
    event_id = str(uuid.uuid4())
    _bg(_write_audit, {
        "event_id": event_id, "ts": int(time.time()), "kiosk_id": kiosk_id, "zone_id": zone_id,
        "user_hash": user_hash, "frame_hash": frame_hash, "outcome": outcome, "reasons": reasons,
        "verdict": verdict, "mode": "online", "liveness": str(liveness), "similarity": str(round(similarity, 4)),
        "model": live.MODEL_NAME or "cues-only", "timings": {k: str(v) for k, v in timings.items()}})

    return 200, {
        "outcome": outcome, "liveness": liveness, "similarity": round(similarity, 4), "tier": 1,
        "reasons": reasons, "mode": "online", "timings": timings, "event_id": event_id, "verdict": verdict,
        "detail": {"spoof_prob": round(float(t1["spoof_prob"]), 4), "quality": round(float(t1["quality"]["overall"]), 3),
                   "phone_paper_confidence": round(phone_paper, 3), "fusion_reasons": fusion_reasons,
                   "risk_level": zone_level, "attack_probs": t1.get("attack_probs"),
                   "signals": {k: round(v, 3) for k, v in t1.get("signals", {}).items() if isinstance(v, float)}},
    }


_face_id = {"id": None}


def _rek_warm_search():
    """Exercise the REAL collection-search path (a no-face image returns early and warms nothing): search by an existing FaceId."""
    if _face_id["id"] is None:
        faces = rekognition.list_faces(CollectionId=COLLECTION_ID, MaxResults=1).get("Faces", [])
        _face_id["id"] = faces[0]["FaceId"] if faces else ""
    if _face_id["id"]:
        rekognition.search_faces(CollectionId=COLLECTION_ID, FaceId=_face_id["id"], MaxFaces=1, FaceMatchThreshold=90)


def warm_pool() -> None:
    """Open/refresh SEVERAL parallel connections per service. Measured on the demo laptop: a brand-new TLS connection to AWS
    costs ~1.5 s, a warm one ~0.3 s, and /decide uses 3 DynamoDB + 1 Rekognition call concurrently, so each needs a warm connection."""
    jobs = [lambda: _get_user("USER_001")] * 3 + [lambda: lockout.is_locked("_warm")] * 2         + [lambda: validate_and_consume_nonce("_warm")] * 3 + [_rek_warm_search] * 2
    for f in [_pool.submit(j) for j in jobs]:
        try:
            f.result(timeout=20)
        except Exception:  # noqa: BLE001
            pass


def warm_up() -> dict:
    """Run the model once and warm all AWS connections, so the FIRST real request is not the slow one."""
    import numpy as np
    t0 = time.time()
    live.score_frames([np.full((160, 160, 3), 128, np.uint8)])
    warm_pool()
    return {"warm_ms": round((time.time() - t0) * 1000)}


def keep_warm_loop(interval_s: int = 12) -> None:
    """Idle connections to AWS go cold; keep them fresh in the background on a long-lived (non-Lambda) server."""
    while True:
        time.sleep(interval_s)
        warm_pool()
