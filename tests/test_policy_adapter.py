"""Policy engine on A's campus data (no AWS needed): schedule / zone / calendar / user status / offline rules."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import policy_engine as pe  # noqa: E402
from backend.campus_adapter import load_policy_seed, user_from_item, zone_risk  # noqa: E402

SEED = load_policy_seed()
U = {u["id"]: u for u in SEED["users"]}
t = pe.parse_time


def d(user, zone, when, verdict="CONFIDENT", mode="ONLINE", off=None):
    return pe.decide({"verdict": verdict}, U.get(user), zone, t(when), SEED, mode, off)


def test_employee_entry_in_and_out_of_hours():
    assert d("USER_003", "ZONE_ENTRY_MAIN", "Tue 10:00") == {"outcome": "ALLOW", "reasons": ["OK_IN_SCHEDULE"]}
    assert d("USER_003", "ZONE_ENTRY_MAIN", "Sat 23:30") == {"outcome": "ALLOW", "reasons": ["OK_IN_SCHEDULE"]}  # entrance open 24/7
    assert d("USER_003", "ZONE_OFFICE_B", "Tue 23:30")["reasons"] == ["OUT_OF_HOURS_STEPUP"]
    assert d("USER_003", "ZONE_OFFICE_B", "Tue 23:30", "LOW")["reasons"] == ["LOW_CONF_OUT_OF_HOURS"]
    assert d("USER_003", "ZONE_OFFICE_B", "Sat 11:00")["reasons"] == ["OUT_OF_HOURS_STEPUP"]  # weekday-only door


def test_zone_authorisation_and_user_status():
    assert d("USER_003", "ZONE_SERVER_ROOM", "Tue 10:00")["reasons"] == ["ZONE_NOT_AUTHORISED"]
    assert d("USER_REVOKED", "ZONE_ENTRY_MAIN", "Tue 10:00")["reasons"] == ["USER_REVOKED"]
    assert d("NOBODY", "ZONE_ENTRY_MAIN", "Tue 10:00")["reasons"] == ["USER_UNKNOWN"]


def test_spoof_beats_everything_and_low_confidence_steps_up():
    assert d("USER_001", "ZONE_SERVER_ROOM", "Tue 10:00", "SPOOF")["reasons"] == ["SPOOF_DETECTED"]
    assert d("USER_003", "ZONE_ENTRY_MAIN", "Tue 10:00", "LOW")["outcome"] == "STEP_UP"


def test_high_risk_door_needs_second_factor_and_is_never_offline():
    assert d("USER_001", "ZONE_SERVER_ROOM", "Tue 10:00")["reasons"] == ["HIGH_RISK_2FA"]
    r = d("USER_001", "ZONE_SERVER_ROOM", "Tue 10:00", mode="OFFLINE", off={"bundle_valid": True})
    assert r["outcome"] == "DENY" and r["reasons"] == ["OFFLINE_HIGH_RISK_DENY"]


def test_calendar_audit_day_is_admin_only():
    assert d("USER_003", "ZONE_ENTRY_MAIN", "2026-11-20 10:00")["reasons"] == ["CALENDAR_BLOCK"]
    assert d("USER_001", "ZONE_SERVER_ROOM", "2026-11-20 10:00")["outcome"] != "DENY"


def test_ddb_item_adapter_and_risk_mapping():
    u = user_from_item({"badge_id": "X", "group_id": "GRP_EMPLOYEE", "status": "active", "valid_to": "2026-12-31T00:00:00Z"})
    assert u["valid_to"] == "2026-12-31" and u["groups"] == ["GRP_EMPLOYEE"] and user_from_item(None) is None
    assert zone_risk("ZONE_ENTRY_MAIN") == "low" and zone_risk("ZONE_OFFICE_A") == "med"
    assert zone_risk("ZONE_SERVER_ROOM") == "high" and zone_risk("ZONE_DOES_NOT_EXIST") == "high"


def test_demo_scenarios_in_the_kiosk_page_match_policy():
    """The website's context scenarios (backend/kiosk.html) must produce their expected outcome for a confident match."""
    import re
    html = (Path(__file__).resolve().parents[1] / "backend" / "kiosk.html").read_text(encoding="utf-8")
    rows = re.findall(r'\{t:"[^"]+",b:"(\w+)",z:"(\w+)",c:"([^"]+)",e:"(\w+)",r:"(\w+)"\}', html)
    assert len(rows) >= 12
    for b, z, c, e, r in rows:
        got = d(b, z, c)
        assert got["outcome"] == e and r in got["reasons"], (b, z, c, e, r, got)
