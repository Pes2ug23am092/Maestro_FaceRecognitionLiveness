"""Adapter: A's campus data (DynamoDB / config/seed_campus.json shapes) -> the shape backend/policy_engine.py consumes.

A's shapes:  zone {zone_id, security_tier}, schedule {group_id, zone_id, days[MON..], start_time, end_time},
             user {badge_id, group_id, status, valid_from, valid_to}, calendar {date, access_override}.
Zone risk comes from security_tier: 1 -> low, 2 -> med, 3+ -> high. Offline rules follow the plan: low may be admitted
offline (with a valid credential), med requires step-up, high is never admitted offline; high also needs a 2nd factor online.
Schedules/zones/calendar are read from the seed JSON (cached); users come live from DynamoDB (status can change).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

SEED_PATH = Path(__file__).resolve().parents[1] / "config" / "seed_campus.json"
DAY = {"MON": "Mon", "TUE": "Tue", "WED": "Wed", "THU": "Thu", "FRI": "Fri", "SAT": "Sat", "SUN": "Sun"}
ADMIN_GROUPS = ["GRP_SYSADMIN", "GRP_SECURITY"]


def _zone(z: dict) -> dict:
    tier = int(z.get("security_tier", 1))
    return {"id": z["zone_id"], "name": z.get("name", z["zone_id"]),
            "risk": "low" if tier <= 1 else ("med" if tier == 2 else "high"),
            "second_factor": tier >= 3, "offline_allowed": tier <= 2, "offline_requires_stepup": tier == 2}


@lru_cache(maxsize=1)
def load_policy_seed() -> dict:
    raw = json.loads(SEED_PATH.read_bytes().decode("utf-8-sig"))
    schedules = [{"group": s["group_id"], "zone": s["zone_id"],
                  "windows": [{"days": [DAY[d] for d in s["days"]], "start": s["start_time"], "end": s["end_time"]}]}
                 for s in raw["schedules"]]
    calendar = []
    for ev in raw.get("calendar", []):
        ov = ev.get("access_override")
        if ov == "ADMIN_ONLY":
            calendar.append({"date": ev["date"], "allow_groups": ADMIN_GROUPS})
        elif ov == "WEEKEND_SCHEDULE":
            calendar.append({"date": ev["date"], "treat_as_day": "Sun"})
    return {"zones": [_zone(z) for z in raw["zones"]], "groups": [g["group_id"] for g in raw["groups"]],
            "schedules": schedules, "calendar": calendar, "users": [user_from_item(u) for u in raw["users"]]}


def user_from_item(item: dict | None) -> dict | None:
    """DynamoDB user item -> policy user dict (None if the badge does not exist)."""
    if not item:
        return None
    return {"id": item["badge_id"], "groups": [item.get("group_id", "GRP_EMPLOYEE")],
            "status": item.get("status", "active"), "grants": [],
            "valid_from": (item.get("valid_from") or "")[:10] or None,
            "valid_to": (item.get("valid_to") or "")[:10] or None}


def zone_risk(zone_id: str) -> str:
    z = next((z for z in load_policy_seed()["zones"] if z["id"] == zone_id), None)
    return z["risk"] if z else "high"  # unknown door: treat as high risk (strictest thresholds)
