"""Capture real screenshots of the DEPLOYED kiosk website (docs/screenshots/*.png).

    (AWS creds in env)  python tools/capture_screenshots.py --casia <CASIA extracted dir> --url https://<api>/kiosk

How it works: Chrome (Playwright) runs the page with a FAKE camera fed from public CASIA-FASD test frames converted to a .y4m
video (live face for the genuine runs, a printed-photo clip and a video-replay clip for the attack runs). The whole flow is real:
requests go to API Gateway -> Lambda -> Rekognition/DynamoDB. The video preview is blurred in every screenshot (dataset faces are
not published). Faces enrolled for the demo are snapshotted before and removed/restored afterwards, so real enrolments are untouched.
"""
import argparse
import glob
import re
import sys
import tempfile
import time
from pathlib import Path

import boto3
import cv2
import numpy as np
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--casia", required=True)
ap.add_argument("--url", required=True)
ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "docs" / "screenshots"))
a = ap.parse_args()
OUT = Path(a.out); OUT.mkdir(parents=True, exist_ok=True)
TMP = Path(tempfile.mkdtemp())
R, COLL = "us-east-1", "kiosk-face-collection-v1"


def y4m(pattern: str, name: str, jitter: bool = False) -> str:
    """CASIA frames -> 640x480 .y4m the fake camera loops. jitter=True: ONE known-good frame with small per-frame shift and sensor
    noise (a still face that Rekognition reliably matches, but not a perfectly rigid image like a photo)."""
    # CASIA's train_img and test_img folders REUSE subject numbers for different people: use ONE folder, or the fake camera
    # alternates between two people (found the hard way: half the frames "mismatched" the enrolled face).
    files = sorted(glob.glob(f"{a.casia}/train_img/train_img/color/{pattern}"), key=lambda p: int(re.search(r"avi_(\d+)_", p).group(1)))
    assert files, pattern
    rng = np.random.default_rng(7)
    frames = []
    for i in range(len(files) if not jitter else 60):
        f = files[i % len(files)]
        img = cv2.imread(f); h, w = img.shape[:2]; s = 480 / h
        img = cv2.resize(img, (int(w * s), 480))
        if jitter:
            M = np.float32([[1, 0, rng.uniform(-1, 1)], [0, 1, rng.uniform(-1, 1)]])
            img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), borderMode=cv2.BORDER_REPLICATE)
            img = np.clip(img.astype(np.float32) + rng.normal(0, 1.2, img.shape), 0, 255).astype(np.uint8)
        canvas = np.full((480, 640, 3), 40, np.uint8); x = (640 - img.shape[1]) // 2
        canvas[:, x:x + img.shape[1]] = img[:, :640 - x if x < 0 else img.shape[1]]
        frames.append(cv2.cvtColor(canvas, cv2.COLOR_BGR2YUV_I420).tobytes())
    p = TMP / f"{name}.y4m"
    with open(p, "wb") as fh:
        fh.write(b"YUV4MPEG2 W640 H480 F10:1 Ip A1:1 C420jpeg\n")
        for i in range(60):
            fh.write(b"FRAME\n" + frames[i % len(frames)])
    return str(p)


LIVE = y4m("5_HR_1.avi_*_real.jpg", "live")  # real consecutive frames of ONE person (natural motion)
PRINT = y4m("5_HR_2.avi_*_fake.jpg", "print")
REPLAY = y4m("5_HR_4.avi_*_fake.jpg", "replay")

# snapshot state so the demo enrolment can be undone
rek = boto3.client("rekognition", region_name=R)
ddb = boto3.resource("dynamodb", region_name=R)
faces_before = {f["FaceId"] for f in rek.list_faces(CollectionId=COLL, MaxResults=1000)["Faces"]}
users_before = {u["badge_id"]: u.get("primary_face_id") for u in ddb.Table("kiosk_users").scan()["Items"]}


def clear_locks():
    for badge in users_before:
        ddb.Table("kiosk_sessions").delete_item(Key={"nonce": f"LOCK#{badge}"})


def new_page(p, video):
    b = p.chromium.launch(channel="chrome", headless=True, args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                                                f"--use-file-for-fake-video-capture={video}"])
    pg = b.new_context(viewport={"width": 1400, "height": 1000}).new_page()
    pg.goto(a.url, wait_until="networkidle", timeout=120000)
    pg.wait_for_function("document.querySelector('#badge').options.length>0", timeout=120000)
    pg.add_style_tag(content="video{filter:blur(24px) !important}")  # privacy: dataset faces are not published
    pg.wait_for_timeout(1500)
    return b, pg


def scan(pg, badge, zone, clock):
    pg.select_option("#badge", badge); pg.select_option("#zone", zone); pg.fill("#clock", clock)
    pg.click("#go")
    pg.wait_for_function("document.querySelector('#outcome').textContent!=='SCANNING…' && !document.querySelector('#go').disabled", timeout=90000)
    pg.wait_for_timeout(600)


def shot(pg, name, full=False):
    pg.screenshot(path=str(OUT / name), full_page=full); print("saved", name, flush=True)


try:
    with sync_playwright() as p:
        # ---------------- run 1: genuine live face
        b, pg = new_page(p, LIVE)
        shot(pg, "01-kiosk-home-and-access-rules.png", full=True)
        pg.click("#enrolall")
        pg.wait_for_function("['ENROLLED','NOT ENROLLED'].includes(document.querySelector('#outcome').textContent)", timeout=120000)
        pg.wait_for_timeout(500); shot(pg, "02-enrol-under-all-demo-badges.png")
        # Rekognition indexing is eventually consistent: wait, then probe until the enrolled face is found, then clear the lock
        time.sleep(4)
        for _ in range(6):
            scan(pg, "USER_003", "ZONE_ENTRY_MAIN", "Tue 10:00")
            if re.search(r"Identity match\s*1\.00", pg.inner_text("#kv")):
                break
            time.sleep(3)
        clear_locks()
        scan(pg, "USER_003", "ZONE_ENTRY_MAIN", "Tue 10:00"); shot(pg, "03-allow-genuine-employee-entrance.png")
        scan(pg, "USER_003", "ZONE_OFFICE_B", "Tue 23:30"); shot(pg, "04-stepup-out-of-hours.png")
        scan(pg, "USER_003", "ZONE_SERVER_ROOM", "Tue 10:00"); shot(pg, "05-deny-door-not-authorised.png")
        scan(pg, "USER_003", "ZONE_ENTRY_MAIN", "2026-11-20 10:00"); shot(pg, "06-deny-audit-day.png")
        scan(pg, "USER_001", "ZONE_SERVER_ROOM", "Wed 03:00"); shot(pg, "07-stepup-high-risk-second-factor.png")
        pg.click("#otpreq"); pg.wait_for_function("/code is \\d{6}/.test(document.querySelector('#otphint').textContent)", timeout=60000)
        code = re.search(r"code is (\d{6})", pg.inner_text("#otphint")).group(1)
        pg.fill("#otp", code); pg.click("#otpbtn"); pg.wait_for_timeout(2500); shot(pg, "08-otp-verified-allow.png")
        pg.click("#runall")
        pg.wait_for_function("document.querySelector('#why').textContent.includes('Scenario run complete')", timeout=300000)
        pg.wait_for_timeout(500); shot(pg, "09-scenario-run-all.png", full=True)
        b.close()

        # ---------------- run 2: printed photo, then repeated failures -> lockout
        clear_locks()
        b, pg = new_page(p, PRINT)
        scan(pg, "USER_003", "ZONE_ENTRY_MAIN", "Tue 10:00"); shot(pg, "10-deny-spoof-printed-photo.png")
        for _ in range(3):
            scan(pg, "USER_003", "ZONE_ENTRY_MAIN", "Tue 10:00")
        shot(pg, "11-deny-lockout-after-3-failures.png"); b.close()

        # ---------------- run 3: video replay
        clear_locks()
        b, pg = new_page(p, REPLAY)
        scan(pg, "USER_002", "ZONE_OFFICE_A", "Tue 10:00"); shot(pg, "12-deny-spoof-video-replay.png"); b.close()
finally:
    # undo demo enrolment: remove the faces added by this run, restore each badge's previous face id, clear locks/otp rows
    added = [f["FaceId"] for f in rek.list_faces(CollectionId=COLL, MaxResults=1000)["Faces"] if f["FaceId"] not in faces_before]
    for i in range(0, len(added), 50):
        rek.delete_faces(CollectionId=COLL, FaceIds=added[i:i + 50])
    for badge, fid in users_before.items():
        if fid is not None:
            ddb.Table("kiosk_users").update_item(Key={"badge_id": badge}, UpdateExpression="SET primary_face_id = :f", ExpressionAttributeValues={":f": fid})
    for badge in users_before:
        for k in (f"LOCK#{badge}", f"OTP#{badge}:KIOSK_MAIN"):
            ddb.Table("kiosk_sessions").delete_item(Key={"nonce": k})
    print(f"cleanup: removed {len(added)} demo faces, restored user records")
