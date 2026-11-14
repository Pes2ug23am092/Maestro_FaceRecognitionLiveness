"""manifest.csv (from capture.py) -> subject-disjoint splits + 160px context crops.

    python train/prepare_data.py --raw ../data/raw --out ../data/crops

Subjects are assigned to train/val/test by a stable hash, so a subject NEVER appears in two splits.
The TEST split is locked: train/export scripts never read it (only eval/ does).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from liveness.face_crop import FaceFinder, context_crop  # noqa: E402


def split_of(subject: str, val=0.2, test=0.2) -> str:
    h = int(hashlib.sha256(subject.encode()).hexdigest(), 16) % 1000 / 1000.0
    return "test" if h < test else ("val" if h < test + val else "train")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="../data/raw")
    ap.add_argument("--out", default="../data/crops")
    a = ap.parse_args()
    raw, out = Path(a.raw), Path(a.out)
    finder = FaceFinder()
    rows, skipped = [], 0
    with open(raw / "manifest.csv") as f:
        for r in csv.DictReader(f):
            img = cv2.imread(str(raw / r["file"]))
            if img is None:
                continue
            d = finder.detect(img)
            if d is None:
                skipped += 1
                continue
            sp = split_of(r["subject"])
            p = out / sp / r["cls"] / (Path(r["file"]).stem + ".jpg")
            p.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(p), context_crop(img, d["box"]), [cv2.IMWRITE_JPEG_QUALITY, 95])
            rows.append({**r, "split": sp, "crop": p.relative_to(out).as_posix()})
    with open(out / "crops_manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    from collections import Counter

    print("crops:", len(rows), "no-face skipped:", skipped)
    print(Counter((r["split"], r["cls"]) for r in rows))


if __name__ == "__main__":
    main()
