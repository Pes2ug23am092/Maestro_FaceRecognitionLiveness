"""Evaluate the exported ONNX model on a split, per source and per attack class, with Wilson 95% CIs.

    python train/eval_cnn.py --split val                 # use for threshold work
    python train/eval_cnn.py --split test --thr 0.5      # LOCKED test: run once, after thresholds are frozen

Metrics (ISO/IEC 30107-3 style): APCER = spoof accepted as live, BPCER = live rejected, ACER = mean.
HID slide metrics: accuracy, FAR (spoof accepted), FRR (live rejected).
Decision here is single-pass binary (spoof_prob >= thr -> spoof). Fusion / step-up band is evaluated in /eval.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from liveness.schema import ATTACK_CLASSES  # noqa: E402
from train.train_cnn import MEAN, STD  # noqa: E402

ART = Path(__file__).resolve().parent.parent / "artifacts"


def wilson(k: int, n: int, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def fmt(k, n):
    if n == 0:
        return "n/a"
    lo, hi = wilson(k, n)
    return f"{100 * k / n:5.2f}% [{100 * lo:.2f}-{100 * hi:.2f}] (n={n})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crops", default="../data/crops")
    ap.add_argument("--split", default="val", choices=["val", "test"])
    ap.add_argument("--model", default=str(ART / "liveness_cnn.int8.onnx"))
    ap.add_argument("--thr", type=float, default=0.5)
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    crops = Path(a.crops)
    s = ort.InferenceSession(a.model, providers=["CPUExecutionProvider"])
    rows = [r for r in csv.DictReader(open(crops / "crops_manifest.csv")) if r["split"] == a.split]
    stats = defaultdict(lambda: [0, 0])  # key -> [errors, n]
    for i in range(0, len(rows), 64):
        chunk = rows[i:i + 64]
        x = np.stack([((cv2.cvtColor(cv2.imread(str(crops / r["crop"])), cv2.COLOR_BGR2RGB).astype(np.float32) / 255 - MEAN)
                       / STD).transpose(2, 0, 1) for r in chunk]).astype(np.float32)
        lg = s.run(None, {"input": x})[0]
        p = np.exp(lg - lg.max(1, keepdims=True))
        p /= p.sum(1, keepdims=True)
        spoof = 1 - p[:, 0]
        for r, sp in zip(chunk, spoof):
            is_spoof = r["cls"] != "live"
            err = (sp < a.thr) if is_spoof else (sp >= a.thr)
            src = r.get("source") or "own"
            coarse = "live" if r["cls"] == "live" else "spoof"
            keys = [("ALL", coarse), (src, coarse)]
            if r["cls"] not in ("live", "spoof"):
                keys.append((src, r["cls"]))  # per-attack-type breakdown (only when the type is known)
            for key in keys:
                stats[key][0] += int(err)
                stats[key][1] += 1
    out = {}
    print(f"split={a.split} thr={a.thr} model={Path(a.model).name}")
    for src in ["ALL"] + sorted({k[0] for k in stats if k[0] != "ALL"}):
        live, spoof = stats.get((src, "live"), [0, 0]), stats.get((src, "spoof"), [0, 0])
        n_all = live[1] + spoof[1]
        acc = 1 - (live[0] + spoof[0]) / max(n_all, 1)
        acer = ((spoof[0] / max(spoof[1], 1)) + (live[0] / max(live[1], 1))) / 2
        print(f"[{src}] accuracy {100 * acc:.2f}% | FAR/APCER {fmt(*spoof)} | FRR/BPCER {fmt(*live)} | ACER {100 * acer:.2f}%")
        out[src] = {"accuracy": acc, "apcer": spoof, "bpcer": live, "acer": acer}
        for cls in ATTACK_CLASSES[1:]:
            if (src, cls) in stats:
                print(f"      {cls:13s} APCER {fmt(*stats[(src, cls)])}")
    if a.json:
        json.dump(out, open(a.json, "w"), indent=2)


if __name__ == "__main__":
    main()
