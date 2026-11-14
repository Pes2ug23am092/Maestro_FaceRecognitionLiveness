"""Decision-time check against the running server: N warm requests + one after an idle gap.
Client time = request sent -> verdict received (capture and the /session call are NOT included, same as the kiosk page)."""
import argparse, base64, glob, statistics, time
import cv2, requests

ap = argparse.ArgumentParser()
ap.add_argument("--casia", required=True); ap.add_argument("--api", default="http://127.0.0.1:8000")
ap.add_argument("--n", type=int, default=15); ap.add_argument("--idle", type=int, default=40)
a = ap.parse_args()
ps = sorted(glob.glob(a.casia + "/*/*/color/5_HR_1.avi_*_real.jpg"))[:6]
frames = [base64.b64encode(cv2.imencode(".jpg", cv2.resize(cv2.imread(p), (480, 360)), [cv2.IMWRITE_JPEG_QUALITY, 82])[1].tobytes()).decode() for p in ps]
print("payload KB:", sum(len(f) for f in frames) // 1000)

def one(tag):
    n = requests.post(a.api + "/session").json()["nonce"]
    t0 = time.perf_counter()
    r = requests.post(a.api + "/decide", json={"nonce": n, "frames": frames, "badge_id": f"USER_PROBE_{time.time_ns()}", "zone_id": "ZONE_ENTRY_MAIN", "now": "Tue 10:00"}).json()
    ms = (time.perf_counter() - t0) * 1000
    t = r.get("timings", {})
    print(f"{tag:10s} client {ms:6.0f} ms | server {t.get('total_ms', 0):6.0f} | liveness {t.get('liveness_ms', 0):4.0f} | rek {t.get('rekognition_ms', 0):5.0f} | "
          f"ddb-wait {t.get('checks_wait_ms', 0):4.0f} | {r.get('outcome')} {r.get('reasons')}")
    return ms

xs = [one(f"warm {i+1}") for i in range(a.n)]
print(f"warm: p50 {statistics.median(xs):.0f} ms, p95 {sorted(xs)[int(.95 * len(xs)) - 1]:.0f} ms, max {max(xs):.0f} ms, <=2000ms: {sum(x <= 2000 for x in xs)}/{len(xs)}")
time.sleep(a.idle)
one(f"after {a.idle}s")
