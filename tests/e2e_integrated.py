"""End-to-end test of the INTEGRATED backend against REAL AWS (Rekognition + DynamoDB) with public CASIA-FASD frames.

    (server running)  python tests/e2e_integrated.py --casia <path to CASIA extracted dir> [--api http://127.0.0.1:8000]

Uses a dedicated test badge USER_BTEST (cleaned up at the end) so A's data in the shared collection is not touched.
Subjects are held-out CASIA people (subject id % 5 == 0 = locked test split of the liveness model).
"""
import argparse, base64, os, re, sys, time
from collections import defaultdict
from pathlib import Path

import boto3
import cv2
import requests

ap = argparse.ArgumentParser()
ap.add_argument("--casia", required=True)
ap.add_argument("--api", default="http://127.0.0.1:8000")
a = ap.parse_args()
API, BADGE = a.api, "USER_BTEST"
COLL, TABLE = "kiosk-face-collection-v1", "kiosk_users"
KIND = {"1": "genuine", "2": "genuine", "HR_1": "genuine", "3": "print", "4": "print", "HR_2": "print", "5": "cut_print",
        "6": "cut_print", "HR_3": "cut_print", "7": "video_replay", "8": "video_replay", "HR_4": "video_replay"}


def b64(p, w=640):
    img = cv2.imread(str(p))
    if img.shape[1] > w:
        img = cv2.resize(img, (w, int(img.shape[0] * w / img.shape[1])))
    return base64.b64encode(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes()).decode()


clips = defaultdict(list)
for part in ("train_img", "test_img"):
    for p in sorted((Path(a.casia) / part / part / "color").glob("*.jpg")):
        m = re.match(r"^(\d+)_((?:HR_)?\d+)\.avi_(\d+)_(real|fake)\.jpg$", p.name)
        if m and int(m.group(1)) % 5 == 0 and m.group(2) in KIND:
            clips[(m.group(1), m.group(2))].append(p)
subs = sorted({s for s, _ in clips}, key=int)
S, OTHER = subs[0], subs[1]
print("held-out subjects used:", S, "(enrolled)", OTHER, "(impostor)")
rek, ddb = boto3.client("rekognition", region_name="us-east-1"), boto3.resource("dynamodb", region_name="us-east-1")


def enrol():
    for clip in ("HR_1", "1", "2"):
        for p in clips.get((S, clip), []):
            r = requests.post(API + "/enroll", json={"badge_id": BADGE, "image_base64": b64(p), "group_id": "GRP_EMPLOYEE"})
            if r.ok:
                print(f"enrolled from {p.name}: ok"); return True
            print(f"  enrol attempt {p.name}: {str(r.json().get('detail', r.text))[:160]}")
    return False


def decide(frames, zone="ZONE_ENTRY_MAIN", now="Tue 10:00", badge=BADGE, reset=True):
    if reset:  # lockout (3 failures) is tested separately at the end
        ddb.Table("kiosk_sessions").delete_item(Key={"nonce": "LOCK#" + badge})
    n = requests.post(API + "/session").json()["nonce"]
    t0 = time.perf_counter()
    r = requests.post(API + "/decide", json={"nonce": n, "frames": frames, "badge_id": badge, "zone_id": zone, "now": now})
    return r.json(), (time.perf_counter() - t0) * 1000


rows = []
try:
    if not enrol():
        print("COULD NOT ENROL a test face (Rekognition quality gate); aborting"); sys.exit(2)
    time.sleep(2)  # collection consistency
    live2 = [b64(p) for p in (clips.get((S, "2")) or clips[(S, "HR_1")])[:6]]
    live_hr = [b64(p) for p in clips.get((S, "HR_1"), [])[:6]]
    cases = [("genuine (clip 2)", live2, {}), ("genuine (HR clip)", live_hr, {})]
    for clip, name in (("3", "print"), ("5", "cut print"), ("7", "video replay"), ("HR_2", "print HR"), ("HR_4", "video replay HR")):
        if (S, clip) in clips:
            cases.append((name, [b64(p) for p in clips[(S, clip)][:6]], {}))
    cases.append(("impostor (other person)", [b64(p) for p in clips[(OTHER, "1")][:6]], {}))
    cases.append(("genuine, door not authorised", live_hr, {"zone": "ZONE_SERVER_ROOM"}))
    cases.append(("genuine, out of hours 23:30", live_hr, {"now": "Tue 23:30"}))
    cases.append(("genuine, audit day (admin only)", live_hr, {"now": "2026-11-20 10:00"}))
    for name, fr, kw in cases:
        if not fr:
            continue
        res, ms = decide(fr, **kw)
        d = res.get("detail") or {}
        if not rows:
            print("first call timings:", res.get("timings"))
        rows.append((name, res.get("outcome"), ",".join(res.get("reasons", [])), res.get("liveness"), res.get("similarity"),
                     d.get("spoof_prob"), round(ms), (res.get("timings") or {}).get("total_ms")))
    print(f"\n{'case':34s} {'outcome':8s} {'reasons':30s} live   sim    spoof  ms  server_ms")
    for r in rows:
        print(f"{r[0]:34s} {str(r[1]):8s} {r[2][:30]:30s} {r[3]}  {r[4]}  {r[5]}  {r[6]}  {r[7]}")
    # lockout rule: 3 biometric failures -> locked; even the genuine user is refused until the lock expires
    imp = [b64(p) for p in clips[(OTHER, "1")][:6]]
    seq = [decide(imp, reset=(i == 0))[0].get("reasons") for i in range(3)]
    after = decide(live_hr, reset=False)[0].get("reasons")
    print("\nlockout: 3 impostor tries ->", seq, "| then genuine ->", after)
    assert after == ["ATTEMPTS_EXCEEDED"], "lockout did not engage"
finally:
    try:
        ddb.Table("kiosk_sessions").delete_item(Key={"nonce": "LOCK#" + BADGE})
    except Exception:
        pass
    try:  # cleanup: remove ONLY the test badge's faces + user row
        ids = [f["FaceId"] for f in rek.list_faces(CollectionId=COLL, MaxResults=500)["Faces"] if f.get("ExternalImageId") == BADGE]
        if ids:
            rek.delete_faces(CollectionId=COLL, FaceIds=ids)
        ddb.Table(TABLE).delete_item(Key={"badge_id": BADGE})
        print(f"\ncleanup: removed {len(ids)} test face(s) and the test user row")
    except Exception as e:
        print("cleanup problem:", e)
