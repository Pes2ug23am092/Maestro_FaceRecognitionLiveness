"""Context-aware policy engine (plan section 3b) + offline rules (section 3c). Pure functions, no I/O.

    decide(bio, user, zone_id, now, seed, mode="ONLINE", offline=None) -> {"outcome", "reasons"}

bio = {"verdict": "SPOOF" | "MISMATCH" | "UNKNOWN" | "LOW" | "CONFIDENT"}
Most-restrictive-wins: DENY beats STEP_UP beats ALLOW. Windows: start inclusive, end exclusive, '23:59' = end of day.
A TypeScript twin for the browser edge agent must pass the same test vectors (backend/local/tests).
"""
from __future__ import annotations

from datetime import datetime, timedelta

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
REF_MONDAY = datetime(2026, 11, 16)


def parse_time(s: str | datetime) -> datetime:
    """'2026-11-17 11:00' | 'Tue 10:00' (reference week) | datetime."""
    if isinstance(s, datetime):
        return s
    s = s.strip()
    head = s.split()[0]
    if head in DAYS:
        hh, mm = s.split()[1].split(":")
        return REF_MONDAY + timedelta(days=DAYS.index(head), hours=int(hh), minutes=int(mm))
    return datetime.strptime(s, "%Y-%m-%d %H:%M")


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return 24 * 60 if hhmm == "23:59" else int(h) * 60 + int(m)


def in_window(windows: list[dict], now: datetime, day_override: str | None = None) -> bool:
    day, cur = day_override or DAYS[now.weekday()], now.hour * 60 + now.minute
    return any(day in w["days"] and _mins(w["start"]) <= cur < _mins(w["end"]) for w in windows)


def _result(outcome: str, *reasons: str) -> dict:
    return {"outcome": outcome, "reasons": list(reasons)}


def decide(bio: dict, user: dict | None, zone_id: str, now: datetime, seed: dict, mode: str = "ONLINE",
           offline: dict | None = None) -> dict:
    v = bio["verdict"]
    if v == "SPOOF":
        return _result("DENY", "SPOOF_DETECTED")
    if user is None or v == "UNKNOWN":
        return _result("DENY", "USER_UNKNOWN")
    if v == "MISMATCH":
        return _result("DENY", "ID_MISMATCH")
    zone = next((z for z in seed["zones"] if z["id"] == zone_id), None)
    if zone is None:
        return _result("DENY", "ZONE_NOT_AUTHORISED")

    if mode == "OFFLINE" and not (offline or {}).get("bundle_valid", False):
        return _result("DENY", "BUNDLE_EXPIRED")

    if user["status"] != "active":
        return _result("DENY", "USER_REVOKED")
    d = now.strftime("%Y-%m-%d")
    if (user.get("valid_from") and d < user["valid_from"]) or (user.get("valid_to") and d > user["valid_to"]):
        return _result("DENY", "USER_EXPIRED")

    groups = set(user["groups"])
    eff_day = None  # calendar events may make a date follow another day's schedule (e.g. holiday -> Sunday)
    for ev in seed.get("calendar", []):
        if ev["date"] != d:
            continue
        if ev.get("treat_as_day"):
            eff_day = ev["treat_as_day"]
        if "allow_groups" in ev and not groups & set(ev["allow_groups"]):
            return _result("DENY", "CALENDAR_BLOCK")
        allowed = ev.get("restrict", {}).get(zone_id)
        if allowed is not None and not groups & set(allowed):
            return _result("DENY", "CALENDAR_BLOCK")

    rows = [s for s in seed["schedules"] if s["zone"] == zone_id and s["group"] in groups]
    rows = [s for s in rows if not s.get("requires_grant") or zone_id in user.get("grants", [])]
    if not rows:
        return _result("DENY", "ZONE_NOT_AUTHORISED")
    # offline can never admit to a zone that is not offline-allowed (checked before the window logic: stricter)
    if mode == "OFFLINE" and not zone.get("offline_allowed", False):
        return _result("DENY", "OFFLINE_HIGH_RISK_DENY")
    permitted_now = any(in_window(s["windows"], now, eff_day) for s in rows)

    if not permitted_now:
        if v == "LOW":
            return _result("DENY", "LOW_CONF_OUT_OF_HOURS")
        return _result("STEP_UP", "OUT_OF_HOURS_STEPUP")

    # offline can only be stricter
    if mode == "OFFLINE":
        if v == "LOW":
            return _result("DENY", "LOW_CONFIDENCE")
        if zone.get("offline_requires_stepup"):
            return _result("STEP_UP", "OFFLINE_MED_RISK_STEPUP")
        return _result("ALLOW", "OFFLINE_OK_LOW_RISK")

    if v == "LOW":
        return _result("STEP_UP", "LOW_CONFIDENCE")
    if zone.get("second_factor"):
        return _result("STEP_UP", "HIGH_RISK_2FA")
    if "visitor" in groups and zone["risk"] != "low" and zone_id != "Z-GATE":
        return _result("STEP_UP", "VISITOR_ESCORT_STEPUP")
    return _result("ALLOW", "OK_IN_SCHEDULE")
