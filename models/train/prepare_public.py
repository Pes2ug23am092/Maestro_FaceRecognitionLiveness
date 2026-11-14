"""Public datasets -> 160px context crops + crops_manifest.csv (same format as prepare_data.py).

    python train/prepare_public.py casia  --src ../data/downloads/casia/x --out ../data/crops
    python train/prepare_public.py celeba --src ../data/downloads/celeba --out ../data/crops --max-per-class 600

Datasets (research use; see docs/datasets.md for sources and licence notes):
  casia : CASIA-FASD frames (HF akahana/anti-spoofing-casiafasd). Clip id -> attack type:
          1,2,HR_1 = live | 3,4,HR_2 = print (warped photo) | 5,6,HR_3 = print (cut photo) | 7,8,HR_4 = video_replay (tablet)
  celeba: CelebA-Spoof shard(s) (HF Ar4ikov/celebA_spoof) using its Class label and face bbox.

The `source` column lets us measure CROSS-DATASET generalisation (train on one source, test on another).
Splits are subject/image-disjoint per source by hash; 'test' is locked as everywhere else.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from liveness.face_crop import FaceFinder, context_crop  # noqa: E402

CASIA_CLIP = {"1": "live", "2": "live", "HR_1": "live", "3": "print", "4": "print", "HR_2": "print",
              "5": "print", "6": "print", "HR_3": "print", "7": "video_replay", "8": "video_replay",
              "HR_4": "video_replay"}


def split_of(key: str, val=0.15, test=0.2) -> str:
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16) % 1000 / 1000.0
    return "test" if h < test else ("val" if h < test + val else "train")


def casia_split(subject_id: str) -> str:
    """Balanced, deterministic subject-level split for CASIA (50 subjects): 60% train / 20% val / 20% test."""
    k = int(subject_id) % 5
    return "test" if k == 0 else ("val" if k == 1 else "train")


def write_manifest(out: Path, rows: list[dict]):
    mp = out / "crops_manifest.csv"
    exist = []
    if mp.exists():
        exist = list(csv.DictReader(open(mp)))
        replaced = {r["source"] for r in rows}  # re-running a source replaces all its old rows
        exist = [r for r in exist if r.get("source") not in replaced]
    allrows = exist + rows
    fields = ["subject", "cls", "lighting", "prop", "burst_id", "frame_idx", "file", "split", "crop", "source"]
    with open(mp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in allrows:
            w.writerow({k: r.get(k, "") for k in fields})
    print("manifest rows:", len(allrows), Counter((r.get("source", "own"), r["split"], r["cls"]) for r in allrows))


def run_casia(src: Path, out: Path):
    finder = FaceFinder()
    rows, miss = [], 0
    for part in ("train_img", "test_img"):
        d = src / part / part / "color"
        for p in sorted(d.glob("*.jpg")):
            m = re.match(r"^(\d+)_((?:HR_)?\d+)\.avi_(\d+)_(real|fake)\.jpg$", p.name)
            if not m or m.group(2) not in CASIA_CLIP:
                continue
            subj, clip = m.group(1), m.group(2)
            cls = CASIA_CLIP[clip]
            img = cv2.imread(str(p))
            det = finder.detect(img)
            if det is None:
                miss += 1
                continue
            sp = casia_split(subj)
            cp = out / sp / cls / ("casia_" + p.stem + ".jpg")
            cp.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(cp), context_crop(img, det["box"]), [cv2.IMWRITE_JPEG_QUALITY, 95])
            rows.append({"subject": "casia_" + subj, "cls": cls, "file": p.name, "split": sp,
                         "crop": cp.relative_to(out).as_posix(), "source": "casia"})
    print("casia no-face:", miss)
    write_manifest(out, rows)


def run_celeba(src: Path, out: Path, max_per_class: int):
    import pyarrow.parquet as pq

    finder = FaceFinder()
    rows, per = [], Counter()
    for pf in sorted(src.glob("*.parquet")):
        t = pq.ParquetFile(pf)
        for batch in t.iter_batches(batch_size=64):
            for rec in batch.to_pylist():
                label = str(rec["Class"])
                cls = CELEBA_MAP.get(label)
                if cls is None or per[cls] >= max_per_class:
                    continue
                img = cv2.imdecode(np.frombuffer(rec["Filepath"]["bytes"], np.uint8), 1)
                if img is None:
                    continue
                bb = rec["Bbox"]
                H, W = img.shape[:2]
                if bb and len(bb) >= 4 and bb[2] > 0 and bb[3] > 0:
                    # CelebA-Spoof bbox is relative to a 'real_h/real_w' scale; use detector instead for safety
                    pass
                det = finder.detect(img)
                if det is None:
                    continue
                key = f"celeba_{pf.stem}_{per[cls]}_{cls}"
                sp = split_of(key)
                cp = out / sp / cls / (key + ".jpg")
                cp.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(cp), context_crop(img, det["box"]), [cv2.IMWRITE_JPEG_QUALITY, 95])
                rows.append({"subject": key, "cls": cls, "file": key, "split": sp,
                             "crop": cp.relative_to(out).as_posix(), "source": "celeba"})
                per[cls] += 1
            if all(per[c] >= max_per_class for c in set(CELEBA_MAP.values())):
                break
    print("celeba per class:", dict(per))
    write_manifest(out, rows)


CELEBA_MAP: dict[str, str] = {}  # filled after inspecting the dataset's Class values (see below)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["casia", "celeba"])
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", default="../data/crops")
    ap.add_argument("--max-per-class", type=int, default=600)
    ap.add_argument("--celeba-map", default="", help="e.g. 'live=live,spoof=print'")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.which == "casia":
        run_casia(Path(a.src), out)
    else:
        CELEBA_MAP.update(dict(kv.split("=") for kv in a.celeba_map.split(",") if kv))
        run_celeba(Path(a.src), out, a.max_per_class)
