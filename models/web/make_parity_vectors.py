"""Write models/web/parity_vectors.json: a few 160x160 crops (PNG, base64) + expected probabilities from onnxruntime.
C checks that liveness_web.js reproduces them (tolerance 1e-3), proving browser == Lambda == Python.
    python models/web/make_parity_vectors.py
"""
import base64
import csv
import json
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

ROOT = Path(__file__).resolve().parents[2]
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
rows = [r for r in csv.DictReader(open(ROOT / "data" / "crops" / "crops_manifest.csv")) if r["split"] == "test"]
pick = []
for cls in ("live", "print", "video_replay", "spoof"):
    pick += [r for r in rows if r["cls"] == cls][:2]
sess = ort.InferenceSession(str(ROOT / "models" / "artifacts" / "liveness_cnn.onnx"), providers=["CPUExecutionProvider"])
vecs = []
for r in pick:
    img = cv2.imread(str(ROOT / "data" / "crops" / r["crop"]))
    png = base64.b64encode(cv2.imencode(".png", img)[1].tobytes()).decode()  # lossless so JS sees identical pixels
    x = ((cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255 - MEAN) / STD).transpose(2, 0, 1)[None]
    lg = sess.run(None, {"input": x})[0][0]
    p = np.exp(lg - lg.max())
    p /= p.sum()
    vecs.append({"label": r["cls"], "png_b64": png, "expected_probs": [float(v) for v in p],
                 "classes": ["live", "print", "screen_photo", "video_replay", "mask"]})
(ROOT / "models" / "web" / "parity_vectors.json").write_text(json.dumps(vecs))
print(len(vecs), "vectors written")
