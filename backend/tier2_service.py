"""Tier 2: nonce-bound colour-flash + head-turn challenge. Challenge state lives in the sessions table (single use, TTL),
so it works across Lambda instances. Passing Tier 2 feeds D's fusion as tier2_passed=True (never a bypass of identity)."""
import json
import time

import boto3

from backend import liveness_service as live
from backend.config import REGION, TABLE_SESSIONS
from backend.decide_service import evaluate
from backend.session_service import validate_and_consume_nonce

sessions = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_SESSIONS)
MAX_FRAMES = 40


def issue_challenge() -> dict:
    ch = live.make_challenge()
    now = int(time.time())
    exp = now + int(ch["ttl_s"])
    sessions.put_item(Item={"nonce": "CH-" + ch["id"], "created_at": now, "expires_at": exp, "ttl": exp, "used": False,
                            "challenge": json.dumps(ch)})
    return {k: ch[k] for k in ("id", "colors", "seg_ms", "action", "ttl_s")}


def verify_tier2(body: dict) -> tuple[int, dict]:
    cid = str(body.get("challenge_id", ""))
    frames, t_ms = body.get("frames") or [], body.get("t_ms") or []
    badge_id, zone_id = body.get("badge_id", ""), body.get("zone_id", "ZONE_ENTRY_MAIN")
    item = sessions.get_item(Key={"nonce": "CH-" + cid}).get("Item") if cid else None
    ok, why = validate_and_consume_nonce("CH-" + cid) if item else (False, "CHALLENGE_NOT_FOUND")  # atomic single use
    if not ok:
        return 400, {"outcome": "DENY", "reasons": ["CHALLENGE_EXPIRED"], "detail": why}
    ch = json.loads(item["challenge"])
    ch["issued_at"] = float(ch["issued_at"])
    bgr = [f for f in (live.decode_b64(x) for x in frames[:MAX_FRAMES]) if f is not None]
    if len(bgr) != len(t_ms[:MAX_FRAMES]):
        return 400, {"outcome": "DENY", "reasons": ["CHALLENGE_FAILED"], "detail": "frames/t_ms mismatch"}
    res = live.verify_challenge(ch, bgr, [float(t) for t in t_ms[:MAX_FRAMES]])
    if not res["passed"]:
        return 200, {"outcome": "DENY", "reasons": ["CHALLENGE_FAILED"], "challenge": res, "verified": False}
    timings: dict = {}
    ev = evaluate(bgr, badge_id, zone_id, body.get("now"), timings, tier2_passed=True)
    return 200, {"outcome": ev["outcome"], "reasons": ev["reasons"], "verdict": ev["verdict"], "verified": True,
                 "similarity": round(ev["similarity"], 4), "liveness": round(1 - float(ev["t1"]["spoof_prob"]), 4),
                 "challenge": res, "timings": timings, "tier": 2}
