"""Plan rule: 3 failed biometric attempts per badge -> lockout (DynamoDB-backed, works across Lambda instances).
Counts only biometric failures (spoof, mismatch, unknown face, unclear scan), NOT policy denials (wrong door, holiday)."""
import time

import boto3

from backend.config import REGION, TABLE_SESSIONS

MAX_ATTEMPTS, LOCK_SECONDS = 3, 60
COUNTED = {"SPOOF_DETECTED", "ID_MISMATCH", "LOW_CONFIDENCE", "USER_UNKNOWN", "CHALLENGE_FAILED"}
_t = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_SESSIONS)


def _key(badge: str) -> dict:
    return {"nonce": "LOCK#" + badge}


def is_locked(badge: str) -> bool:
    item = _t.get_item(Key=_key(badge), ConsistentRead=True).get("Item")
    return bool(item and int(item.get("locked_until", 0)) > time.time())


def counts(reasons: list[str]) -> bool:
    return bool(set(reasons) & COUNTED)


def record_failure(badge: str) -> bool:
    """Returns True if this failure triggers the lockout."""
    now = int(time.time())
    n = int(_t.update_item(Key=_key(badge), UpdateExpression="ADD fails :one SET #t = :ttl, used = :f",
                           ExpressionAttributeNames={"#t": "ttl"},
                           ExpressionAttributeValues={":one": 1, ":ttl": now + 600, ":f": True},
                           ReturnValues="UPDATED_NEW")["Attributes"]["fails"])
    if n >= MAX_ATTEMPTS:
        _t.update_item(Key=_key(badge), UpdateExpression="SET locked_until = :u, fails = :z",
                       ExpressionAttributeValues={":u": now + LOCK_SECONDS, ":z": 0})
        print(f"[SECURITY ALERT] badge hash locked out for {LOCK_SECONDS}s after {MAX_ATTEMPTS} biometric failures")
        return True
    return False


def clear(badge: str) -> None:
    _t.delete_item(Key=_key(badge))
