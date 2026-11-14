"""Face detection + context crop (OpenCV YuNet, falls back to Haar).

The CNN needs *context* around the face (phone bezel, paper edge, hands), so we crop a box
CONTEXT_SCALE times larger than the detected face, not a tight crop.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
YUNET_PATH = ARTIFACTS / "face_detection_yunet_2023mar.onnx"
CONTEXT_SCALE = 2.0
CNN_INPUT = 160


class FaceFinder:
    def __init__(self, score_threshold: float = 0.7):
        self._yunet = None
        self._haar = None
        self._size = None
        if YUNET_PATH.exists():
            self._yunet = cv2.FaceDetectorYN.create(str(YUNET_PATH), "", (320, 320), score_threshold, 0.3, 5)
        else:
            self._haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    def detect(self, bgr: np.ndarray):
        """Return largest face as dict(box=(x,y,w,h), landmarks=5x2 or None, score) or None."""
        h, w = bgr.shape[:2]
        if self._yunet is not None:
            if self._size != (w, h):
                self._yunet.setInputSize((w, h))
                self._size = (w, h)
            _, faces = self._yunet.detect(bgr)
            if faces is None or len(faces) == 0:
                return None
            f = max(faces, key=lambda r: r[2] * r[3])
            x, y, bw, bh = [float(v) for v in f[:4]]
            lm = np.array(f[4:14], dtype=np.float32).reshape(5, 2)  # re, le, nose, rm, lm
            return {"box": (x, y, bw, bh), "landmarks": lm, "score": float(f[14])}
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        rects = self._haar.detectMultiScale(gray, 1.1, 5, minSize=(60, 60))
        if len(rects) == 0:
            return None
        x, y, bw, bh = max(rects, key=lambda r: r[2] * r[3])
        return {"box": (float(x), float(y), float(bw), float(bh)), "landmarks": None, "score": 1.0}


def context_crop(bgr: np.ndarray, box, scale: float = CONTEXT_SCALE, out: int = CNN_INPUT) -> np.ndarray:
    """Square crop centred on the face, `scale` x the face size, edge-padded, resized to out x out."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    side = max(w, h) * scale
    x0, y0 = int(round(cx - side / 2)), int(round(cy - side / 2))
    s = int(round(side))
    pad = max(0, -x0, -y0, x0 + s - bgr.shape[1], y0 + s - bgr.shape[0])
    if pad:
        bgr = cv2.copyMakeBorder(bgr, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        x0 += pad
        y0 += pad
    crop = bgr[y0:y0 + s, x0:x0 + s]
    return cv2.resize(crop, (out, out), interpolation=cv2.INTER_AREA)


def face_region(bgr: np.ndarray, box, shrink: float = 0.8) -> np.ndarray:
    """Tight skin region (face box shrunk a bit) used by texture / glare cues."""
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    w2, h2 = w * shrink, h * shrink
    x0, y0 = max(0, int(cx - w2 / 2)), max(0, int(cy - h2 / 2))
    x1, y1 = min(bgr.shape[1], int(cx + w2 / 2)), min(bgr.shape[0], int(cy + h2 / 2))
    return bgr[y0:y1, x0:x1]
