"""Can AWS services ALONE (no custom model) reach 99% liveness accuracy?  Measure it, on the same held-out images.

    python eval/aws_only_accuracy.py --crops <data/crops dir> --model models/artifacts/liveness_cnn.onnx

Inputs: public held-out TEST crops (CASIA-FASD: live / print / video replay; CelebA-Spoof: live / spoof). Every method sees
the same 160px context crops. Methods:
  aws_labels_orig   Rekognition DetectLabels: spoof if a phone/tablet/screen/paper/poster/photo label >= 70%  (A's original rule)
  aws_labels_narrow same, only objects held in front of the face (phone/tablet/paper/poster/frame/photograph) >= 70%
  aws_labels_any    spoof if ANY of: any of the original labels >= 50%  (most aggressive label rule)
  b_cnn             our MobileNetV3 liveness model, spoof_prob >= 0.5
  b_cnn+labels      b_cnn OR aws_labels_narrow
AWS Rekognition Face Liveness (the managed Tier 3) cannot be run headless: it needs the browser video widget, so it is NOT measured.
Metrics: APCER = attack accepted as live, BPCER = live rejected, accuracy; Wilson 95% CIs.
"""
import argparse
import csv
import math
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
import cv2
import numpy as np
import onnxruntime as ort

ORIG = {"mobile phone", "cell phone", "phone", "electronics", "tablet computer", "screen", "monitor", "display",
        "paper", "poster", "photograph", "picture frame"}
NARROW = {"mobile phone", "cell phone", "phone", "tablet computer", "paper", "poster", "picture frame", "photograph"}
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def wilson(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p, d = k / n, 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def fmt(k, n):
    lo, hi = wilson(k, n)
    return f"{100 * k / max(n, 1):.1f}% [{100 * lo:.1f}-{100 * hi:.1f}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crops", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", default="docs/AWS_ONLY_ACCURACY.md")
    a = ap.parse_args()
    crops = Path(a.crops)
    rows = [r for r in csv.DictReader(open(crops / "crops_manifest.csv")) if r["split"] == "test"]
    rek = boto3.client("rekognition", region_name="us-east-1")
    sess = ort.InferenceSession(a.model, providers=["CPUExecutionProvider"])

    def labels(r):
        b = cv2.imencode(".jpg", cv2.imread(str(crops / r["crop"])), [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()
        for _ in range(4):
            try:
                return {l["Name"].lower(): l["Confidence"] for l in rek.detect_labels(Image={"Bytes": b}, MaxLabels=25, MinConfidence=40)["Labels"]}
            except Exception:  # throttling / transient
                import time
                time.sleep(1)
        return None

    with ThreadPoolExecutor(8) as ex:
        labs = list(ex.map(labels, rows))
    print("rekognition calls done:", sum(l is not None for l in labs), "/", len(rows))

    res = defaultdict(lambda: defaultdict(lambda: [0, 0]))  # method -> (source, kind) -> [errors, n]
    for i in range(0, len(rows), 64):
        chunk = rows[i:i + 64]
        x = np.stack([((cv2.cvtColor(cv2.imread(str(crops / r["crop"])), cv2.COLOR_BGR2RGB).astype(np.float32) / 255 - MEAN) / STD)
                      .transpose(2, 0, 1) for r in chunk]).astype(np.float32)
        lg = sess.run(None, {"input": x})[0]
        p = np.exp(lg - lg.max(1, keepdims=True))
        p /= p.sum(1, keepdims=True)
        for r, spoof_p, lab in zip(chunk, 1 - p[:, 0], labs[i:i + 64]):
            if lab is None:
                continue
            is_spoof = r["cls"] != "live"
            pred = {
                "aws_labels_orig": any(lab.get(n, 0) >= 70 for n in ORIG),
                "aws_labels_narrow": any(lab.get(n, 0) >= 70 for n in NARROW),
                "aws_labels_any": any(lab.get(n, 0) >= 50 for n in ORIG),
                "b_cnn": spoof_p >= 0.5,
                "b_cnn+labels": spoof_p >= 0.5 or any(lab.get(n, 0) >= 70 for n in NARROW),
            }
            for m, flagged in pred.items():
                err = (not flagged) if is_spoof else flagged  # attack accepted / live rejected
                for src in ("ALL", r["source"]):
                    res[m][(src, "attack" if is_spoof else "live")][0] += int(err)
                    res[m][(src, "attack" if is_spoof else "live")][1] += 1

    lines = ["# Can AWS alone reach 99 %? Measured on held-out public test crops", "",
             "Same images for every method. Attack error = APCER (attack accepted as live). Live error = BPCER (live rejected).", ""]
    for src in ("ALL", "casia", "celeba"):
        lines += [f"## {src}", "", "| Method | Attack accepted (APCER) | Live rejected (BPCER) | Accuracy |", "|---|---|---|---|"]
        for m in ("aws_labels_orig", "aws_labels_narrow", "aws_labels_any", "b_cnn", "b_cnn+labels"):
            ea, na = res[m][(src, "attack")]
            el, nl = res[m][(src, "live")]
            acc = 1 - (ea + el) / max(na + nl, 1)
            lines.append(f"| {m} | {fmt(ea, na)} (n={na}) | {fmt(el, nl)} (n={nl}) | {100 * acc:.1f}% |")
        lines.append("")
    lines += ["Rekognition Face Liveness (managed Tier 3) is NOT in this table: it needs the browser video widget and cannot be run "
              "headless, so its accuracy is unmeasured here and any vendor figure must not be quoted as ours."]
    Path(a.out).write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
