import time
import hashlib
import random
import boto3
from backend.config import REGION, TABLE_USERS

dynamodb = boto3.resource("dynamodb", region_name=REGION)
users_table = dynamodb.Table(TABLE_USERS)

# In-memory or DynamoDB attempt cache
# key: (badge_id, kiosk_id) -> {"hash": str, "expires_at": int, "attempts": int, "locked": bool}
_OTP_STORE = {}

def generate_otp_for_user(badge_id: str, kiosk_id: str = "KIOSK_MAIN") -> dict:
    code = f"{random.randint(100000, 999999)}"
    now = int(time.time())
    expires_at = now + 300  # 5 minutes
    salt = "kiosk-otp-salt-2026"
    code_hash = hashlib.sha256(f"{code}:{salt}".encode("utf-8")).hexdigest()

    key = f"{badge_id}:{kiosk_id}"
    _OTP_STORE[key] = {
        "hash": code_hash,
        "expires_at": expires_at,
        "attempts": 0,
        "locked": False
    }

    return {
        "badge_id": badge_id,
        "kiosk_id": kiosk_id,
        "code_for_demo": code,  # Provided for demo/testing without email gating
        "expires_at": expires_at
    }

def verify_otp(badge_id: str, kiosk_id: str, submitted_code: str) -> dict:
    now = int(time.time())
    key = f"{badge_id}:{kiosk_id}"
    record = _OTP_STORE.get(key)

    if not record:
        return {"status": "INVALID", "message": "No active OTP session found.", "attempts_left": 0}

    if record.get("locked"):
        return {"status": "LOCKED_OUT", "message": "Too many failed attempts. Identity locked.", "attempts_left": 0}

    if record["expires_at"] < now:
        return {"status": "INVALID", "message": "OTP expired.", "attempts_left": 0}

    salt = "kiosk-otp-salt-2026"
    submitted_hash = hashlib.sha256(f"{submitted_code}:{salt}".encode("utf-8")).hexdigest()

    if submitted_hash == record["hash"]:
        # Success! Clear record
        del _OTP_STORE[key]
        return {"status": "VERIFIED", "message": "OTP verified successfully."}
    else:
        record["attempts"] += 1
        attempts_left = max(0, 3 - record["attempts"])
        if record["attempts"] >= 3:
            record["locked"] = True
            print(f"[SECURITY ALERT] Badge {badge_id} LOCKED OUT after 3 failed OTP attempts on {kiosk_id}!")
            return {"status": "LOCKED_OUT", "message": "3 failed attempts. Account locked out.", "attempts_left": 0}
        return {"status": "INVALID", "message": f"Incorrect OTP code. {attempts_left} attempts remaining.", "attempts_left": attempts_left}
