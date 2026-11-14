"""Webcam data collection for the evaluation + training sets (consented subjects only!).

    python collect/capture.py --subject S01 --lighting bright --out ../data/raw

Keys (focus the video window):
  1 live   2 print   3 screen_photo   4 video_replay   5 mask
  p  = set the prop/device label (prompts in the terminal, e.g. "A4 glossy", "iPhone 13")
  l  = cycle lighting label (bright / dim / backlit / mixed)
  SPACE = capture a burst of N frames (default 8, ~10 fps)      q = quit

Saves FULL frames (so the crop strategy can change later) + manifest.csv. Never commit data/ to git.
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

import cv2

CLASSES = {ord("1"): "live", ord("2"): "print", ord("3"): "screen_photo", ord("4"): "video_replay", ord("5"): "mask"}
LIGHTS = ["bright", "dim", "backlit", "mixed"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True, help="anonymous id, e.g. S01 (no real names)")
    ap.add_argument("--out", default="../data/raw")
    ap.add_argument("--lighting", default="bright", choices=LIGHTS)
    ap.add_argument("--burst", type=int, default=8)
    ap.add_argument("--camera", type=int, default=0)
    a = ap.parse_args()

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manifest = out / "manifest.csv"
    new = not manifest.exists()
    f = open(manifest, "a", newline="")
    w = csv.writer(f)
    if new:
        w.writerow(["subject", "cls", "lighting", "prop", "burst_id", "frame_idx", "file"])

    cap = cv2.VideoCapture(a.camera, cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cls, prop, light = "live", "none", a.lighting
    counts: dict[str, int] = {}
    print(__doc__)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        view = frame.copy()
        cv2.putText(view, f"{a.subject} | {cls} | {light} | {prop} | bursts:{counts.get(cls, 0)}", (10, 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        cv2.imshow("capture", view)
        k = cv2.waitKey(1) & 0xFF
        if k == ord("q"):
            break
        if k in CLASSES:
            cls = CLASSES[k]
        elif k == ord("l"):
            light = LIGHTS[(LIGHTS.index(light) + 1) % len(LIGHTS)]
        elif k == ord("p"):
            prop = input("prop/device label: ").strip().replace(",", " ") or "none"
        elif k == 32:
            burst_id = f"{int(time.time())}"
            d = out / a.subject / cls
            d.mkdir(parents=True, exist_ok=True)
            for i in range(a.burst):
                ok, fr = cap.read()
                if not ok:
                    break
                p = d / f"{burst_id}_{i}.jpg"
                cv2.imwrite(str(p), fr, [cv2.IMWRITE_JPEG_QUALITY, 92])
                w.writerow([a.subject, cls, light, prop, burst_id, i, p.relative_to(out).as_posix()])
                time.sleep(0.08)
            f.flush()
            counts[cls] = counts.get(cls, 0) + 1
            print(f"saved burst {burst_id} [{cls}] total {counts}")
    f.close()
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
