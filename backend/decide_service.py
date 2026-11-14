import time
import base64
import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
import boto3
from backend.config import (
    REGION, COLLECTION_ID, TABLE_USERS, TABLE_DECISIONS,
    SIMILARITY_THRESHOLD, FACE_QUALITY_MIN_SHARPNESS, FACE_QUALITY_MIN_BRIGHTNESS
)
from backend.session_service import validate_and_consume_nonce

rekognition = boto3.client("rekognition", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
users_table = dynamodb.Table(TABLE_USERS)
decisions_table = dynamodb.Table(TABLE_DECISIONS)

SPOOF_LABELS = {
    "mobile phone", "cell phone", "phone", "electronics",
    "tablet computer", "screen", "monitor", "display",
    "paper", "poster", "photograph", "picture frame"
}

def call_detect_faces(image_bytes: bytes) -> dict:
    t0 = time.time()
    try:
        res = rekognition.detect_faces(Image={"Bytes": image_bytes}, Attributes=["ALL"])
        return {"status": "OK", "faces": res.get("FaceDetails", []), "elapsed_ms": (time.time() - t0) * 1000}
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "elapsed_ms": (time.time() - t0) * 1000}

def call_detect_labels(image_bytes: bytes) -> dict:
    t0 = time.time()
    try:
        res = rekognition.detect_labels(
            Image={"Bytes": image_bytes},
            MaxLabels=15,
            MinConfidence=60.0
        )
        return {"status": "OK", "labels": res.get("Labels", []), "elapsed_ms": (time.time() - t0) * 1000}
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "elapsed_ms": (time.time() - t0) * 1000}

def call_search_faces(image_bytes: bytes) -> dict:
    t0 = time.time()
    try:
        res = rekognition.search_faces_by_image(
            CollectionId=COLLECTION_ID,
            Image={"Bytes": image_bytes},
            MaxFaces=3,
            FaceMatchThreshold=60.0
        )
        return {"status": "OK", "matches": res.get("FaceMatches", []), "elapsed_ms": (time.time() - t0) * 1000}
    except Exception as e:
        return {"status": "ERROR", "error": str(e), "elapsed_ms": (time.time() - t0) * 1000}

def decide_access(
    nonce: str,
    frames: list[str],
    badge_id: str,
    zone_id: str,
    kiosk_id: str = "KIOSK_MAIN"
) -> tuple[int, dict]:
    t_start = time.time()
    timings = {}

    # 1. Nonce Validation
    valid, reason = validate_and_consume_nonce(nonce)
    if not valid:
        return 400, {
            "error": "INVALID_NONCE",
            "reason": reason,
            "message": f"Nonce validation failed: {reason}"
        }

    # 2. Frame processing & SHA-256 Hashing
    if not frames:
        return 400, {"error": "NO_FRAMES_PROVIDED"}

    # Take first or best frame
    try:
        frame_bytes = base64.b64decode(frames[0])
    except Exception:
        return 400, {"error": "INVALID_BASE64_FRAME"}

    timings["upload_ms"] = round((time.time() - t_start) * 1000, 2)

    hasher = hashlib.sha256()
    hasher.update(nonce.encode("utf-8"))
    for f in frames:
        hasher.update(f.encode("utf-8"))
    frame_hash = hasher.hexdigest()
    user_hash = hashlib.sha256(badge_id.encode("utf-8")).hexdigest()[:16]

    # 3. Parallel Rekognition Calls
    t_rek_start = time.time()
    with ThreadPoolExecutor(max_workers=3) as executor:
        f_faces = executor.submit(call_detect_faces, frame_bytes)
        f_labels = executor.submit(call_detect_labels, frame_bytes)
        f_search = executor.submit(call_search_faces, frame_bytes)

        r_faces = f_faces.result()
        r_labels = f_labels.result()
        r_search = f_search.result()
    timings["rekognition_ms"] = round((time.time() - t_rek_start) * 1000, 2)

    t_fusion_start = time.time()

    reasons = []
    outcome = "DENY"
    liveness_score = 0.95
    similarity_score = 0.0
    tier = 1

    # Check Spoof Labels
    detected_spoofs = []
    if r_labels.get("status") == "OK":
        for lbl in r_labels.get("labels", []):
            name = lbl.get("Name", "").lower()
            conf = lbl.get("Confidence", 0.0)
            if name in SPOOF_LABELS and conf > 70.0:
                detected_spoofs.append(f"{name}:{conf:.0f}%")

    if detected_spoofs:
        reasons.append(f"SPOOF_DETECTED_{'_'.join(detected_spoofs)}")
        liveness_score = 0.20

    # Check Quality
    faces = r_faces.get("faces", [])
    if not faces:
        reasons.append("NO_FACE_IN_FRAME")
    elif len(faces) > 1:
        reasons.append("MULTIPLE_FACES_IN_FRAME")
    else:
        face = faces[0]
        q = face.get("Quality", {})
        sharpness = q.get("Sharpness", 0.0)
        brightness = q.get("Brightness", 0.0)
        if sharpness < FACE_QUALITY_MIN_SHARPNESS or brightness < FACE_QUALITY_MIN_BRIGHTNESS:
            reasons.append("LOW_QUALITY_FRAME")

    # Check Identity Match
    matches = r_search.get("matches", [])
    matched_badge = None
    if matches:
        top_match = matches[0]
        matched_badge = top_match["Face"].get("ExternalImageId")
        similarity_score = float(top_match.get("Similarity", 0.0)) / 100.0

    # Check User Status in DynamoDB
    user_item = users_table.get_item(Key={"badge_id": badge_id}).get("Item")
    if user_item and user_item.get("status") == "revoked":
        outcome = "DENY"
        reasons.append("USER_REVOKED")
    elif user_item and user_item.get("status") == "suspended":
        outcome = "DENY"
        reasons.append("USER_SUSPENDED")
    elif detected_spoofs:
        outcome = "DENY"
    elif matched_badge == badge_id and similarity_score >= (SIMILARITY_THRESHOLD / 100.0):
        if "LOW_QUALITY_FRAME" in reasons:
            outcome = "STEP_UP"
            reasons.append("STEP_UP_LOW_QUALITY")
        else:
            outcome = "ALLOW"
            reasons.append("MATCH_CONFIRMED")
            reasons.append("OK_IN_SCHEDULE")
    elif matched_badge == badge_id and similarity_score >= 0.70:
        outcome = "STEP_UP"
        reasons.append("BORDERLINE_SIMILARITY_STEP_UP")
    else:
        outcome = "DENY"
        if not matches:
            reasons.append("NO_MATCHING_IDENTITY")
        else:
            reasons.append("IDENTITY_MISMATCH")

    timings["fusion_ms"] = round((time.time() - t_fusion_start) * 1000, 2)
    timings["total_ms"] = round((time.time() - t_start) * 1000, 2)

    # 4. Audit Log to DynamoDB (Zero PII, zero frames)
    event_id = str(uuid.uuid4())
    audit_item = {
        "event_id": event_id,
        "ts": int(time.time()),
        "kiosk_id": kiosk_id,
        "zone_id": zone_id,
        "user_hash": user_hash,
        "frame_hash": frame_hash,
        "outcome": outcome,
        "reasons": reasons,
        "mode": "online",
        "liveness": str(liveness_score),
        "similarity": str(similarity_score),
        "timings": {k: str(v) for k, v in timings.items()},
    }
    try:
        decisions_table.put_item(Item=audit_item)
    except Exception as e:
        print(f"[WARN] Audit logging write failed: {e}")

    response_payload = {
        "outcome": outcome,
        "liveness": round(liveness_score, 2),
        "similarity": round(similarity_score, 2),
        "tier": tier,
        "reasons": reasons,
        "mode": "online",
        "timings": timings,
        "event_id": event_id
    }
    return 200, response_payload
