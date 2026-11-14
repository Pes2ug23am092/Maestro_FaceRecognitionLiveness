"""Step-up OTP, stored in DynamoDB (kiosk_sessions, key 'OTP#<badge>:<kiosk>') so it works across Lambda instances.
Only a salted hash of the code is stored. 3 wrong attempts -> locked for LOCK_SECONDS (the old in-memory version locked forever).
`code_for_demo` is returned only when OTP_DEMO=1 (default on for the demo; in production the code goes out via SES/SNS)."""
import hashlib
import os
import secrets
import time

import boto3

from backend.config import REGION, TABLE_SESSIONS

_t = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_SESSIONS)
SALT = os.getenv("OTP_SALT", "kiosk-otp-salt-2026")
OTP_TTL, LOCK_SECONDS, MAX_ATTEMPTS = 300, 300, 3
DEMO = os.getenv("OTP_DEMO", "1") == "1"


def _key(badge_id: str, kiosk_id: str) -> dict:
    return {"nonce": f"OTP#{badge_id}:{kiosk_id}"}


def _hash(code: str) -> str:
    return hashlib.sha256(f"{code}:{SALT}".encode("utf-8")).hexdigest()


def generate_otp_for_user(badge_id: str, kiosk_id: str = "KIOSK_MAIN") -> dict:
    code = f"{secrets.randbelow(900000) + 100000}"
    now = int(time.time())
    _t.put_item(Item={**_key(badge_id, kiosk_id), "hash": _hash(code), "expires_at": now + OTP_TTL, "ttl": now + OTP_TTL + 600,
                      "attempts": 0, "locked_until": 0, "used": True})
    out = {"badge_id": badge_id, "kiosk_id": kiosk_id, "expires_at": now + OTP_TTL}
    if DEMO:
        out["code_for_demo"] = code  # demo only: no email/SMS gating
    return out


def verify_otp(badge_id: str, kiosk_id: str, submitted_code: str) -> dict:
    now = int(time.time())
    rec = _t.get_item(Key=_key(badge_id, kiosk_id), ConsistentRead=True).get("Item")
    if not rec:
        return {"status": "INVALID", "message": "No active OTP session found.", "attempts_left": 0}
    if int(rec.get("locked_until", 0)) > now:
        return {"status": "LOCKED_OUT", "message": "Too many failed attempts. Identity locked.", "attempts_left": 0}
    if int(rec["expires_at"]) < now:
        return {"status": "INVALID", "message": "OTP expired.", "attempts_left": 0}
    if secrets.compare_digest(_hash(submitted_code), rec["hash"]):
        _t.delete_item(Key=_key(badge_id, kiosk_id))
        return {"status": "VERIFIED", "message": "OTP verified successfully."}
    n = int(_t.update_item(Key=_key(badge_id, kiosk_id), UpdateExpression="ADD attempts :one",
                           ExpressionAttributeValues={":one": 1}, ReturnValues="UPDATED_NEW")["Attributes"]["attempts"])
    if n >= MAX_ATTEMPTS:
        _t.update_item(Key=_key(badge_id, kiosk_id), UpdateExpression="SET locked_until = :u",
                       ExpressionAttributeValues={":u": now + LOCK_SECONDS})
        print(f"[SECURITY ALERT] Badge {badge_id} LOCKED OUT after {MAX_ATTEMPTS} failed OTP attempts on {kiosk_id}!")
        return {"status": "LOCKED_OUT", "message": "3 failed attempts. Account locked out.", "attempts_left": 0}
    left = MAX_ATTEMPTS - n
    return {"status": "INVALID", "message": f"Incorrect OTP code. {left} attempts remaining.", "attempts_left": left}
