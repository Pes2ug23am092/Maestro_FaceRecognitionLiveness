"""Classical anti-spoof cues (cues.v1). Cheap (<30 ms for a burst), CPU only, no training needed.

Each cue returns a RAW feature and a 0..1 score where HIGHER = MORE SPOOF-LIKE (quality: higher = better).
The mapping raw -> score uses logistic parameters in CALIBRATION. Defaults are hand-set guesses and are
UNCALIBRATED until `train/calibrate_cues.py` fits them on the validation split (plan Phase 9).
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

CAL_PATH = Path(__file__).resolve().parent.parent / "artifacts" / "cues_calibration.json"

# (mid, scale, direction): score = sigmoid(direction * (raw - mid) / scale)
DEFAULT_CALIBRATION = {
    "moire": {"mid": 6.0, "scale": 1.5, "dir": 1},          # raw = spectral peak / std of residual spectrum
    "glare": {"mid": 0.02, "scale": 0.015, "dir": 1},       # raw = fraction of near-white low-saturation px
    "flatness": {"mid": 0.10, "scale": 0.03, "dir": -1},    # raw = high-freq energy ratio (low => flat/reprocessed)
    "rigid_motion": {"mid": 0.35, "scale": 0.15, "dir": -1},  # raw = non-rigid flow residual (low => rigid)
    "screen_edge": {"mid": 2.0, "scale": 1.0, "dir": 1},    # raw = number of long straight border lines
}


def _load_calibration() -> dict:
    cal = {k: dict(v) for k, v in DEFAULT_CALIBRATION.items()}
    try:
        if CAL_PATH.exists():
            cal.update(json.loads(CAL_PATH.read_text()))
    except Exception:
        pass
    return cal


CALIBRATION = _load_calibration()


def _sig(raw: float, name: str) -> float:
    p = CALIBRATION[name]
    z = p["dir"] * (raw - p["mid"]) / p["scale"]
    return float(1.0 / (1.0 + np.exp(-np.clip(z, -30, 30))))


def _gray(bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


# ---------------------------------------------------------------- moire / pixel grid (screen attacks)
def moire_raw(face_bgr: np.ndarray) -> float:
    """Peak-to-spread ratio of the 2-D spectrum after removing the smooth radial trend.
    Screens/prints re-sampled by a camera produce isolated spectral peaks (moire, pixel grid)."""
    if face_bgr.size == 0:
        return 0.0
    g = cv2.resize(_gray(face_bgr), (128, 128), interpolation=cv2.INTER_AREA).astype(np.float32)
    g -= g.mean()
    g *= np.outer(np.hanning(128), np.hanning(128)).astype(np.float32)
    mag = np.log1p(np.abs(np.fft.fftshift(np.fft.fft2(g))))
    yy, xx = np.indices(mag.shape)
    r = np.hypot(yy - 64, xx - 64).astype(np.int32)
    radial = np.bincount(r.ravel(), mag.ravel()) / np.maximum(np.bincount(r.ravel()), 1)
    resid = mag - radial[r]
    band = (r >= 14) & (r <= 62)
    vals = resid[band]
    if vals.size == 0:
        return 0.0
    return float(vals.max() / (vals.std() + 1e-6))


# ---------------------------------------------------------------- specular glare (glossy print / screen)
def glare_raw(face_bgr: np.ndarray) -> float:
    if face_bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2HSV)
    m = (hsv[..., 2] >= 240) & (hsv[..., 1] <= 40)
    return float(m.mean())


# ---------------------------------------------------------------- flatness: high-frequency energy ratio
def hf_ratio_raw(face_bgr: np.ndarray) -> float:
    if face_bgr.size == 0:
        return 0.0
    g = cv2.resize(_gray(face_bgr), (96, 96), interpolation=cv2.INTER_AREA).astype(np.float32)
    g -= g.mean()
    p = np.abs(np.fft.fftshift(np.fft.fft2(g * np.outer(np.hanning(96), np.hanning(96)).astype(np.float32)))) ** 2
    yy, xx = np.indices(p.shape)
    r = np.hypot(yy - 48, xx - 48)
    tot = p[(r > 2)].sum() + 1e-9
    return float(p[(r > 20)].sum() / tot)


# ---------------------------------------------------------------- screen / paper border (needs context crop)
def screen_edge_raw(context_bgr: np.ndarray) -> float:
    """Count long straight lines in the context crop. A phone, tablet or sheet of paper held in front of
    the camera shows strong long borders; a live face in a room usually does not."""
    g = cv2.GaussianBlur(_gray(context_bgr), (5, 5), 0)
    edges = cv2.Canny(g, 60, 150)
    side = context_bgr.shape[0]
    lines = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=60, minLineLength=int(0.45 * side), maxLineGap=6)
    if lines is None:
        return 0.0
    n = 0
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):  # OpenCV 4 returns (N,1,4), OpenCV 5 (N,4)
        ang = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1))) % 180
        if min(ang, abs(ang - 90), abs(ang - 180)) < 12:  # near horizontal / vertical
            n += 1
    return float(min(n, 8))


# ---------------------------------------------------------------- temporal: rigid vs non-rigid motion
def nonrigid_motion_raw(face_crops_gray: list[np.ndarray]) -> float | None:
    """Fit a global affine to dense optical flow between consecutive face crops; return mean residual (px @ 64px).
    A held photo / screen moves as one rigid sheet (small residual). A live face deforms (blink, mouth, skin).
    Returns None when fewer than 3 frames."""
    if len(face_crops_gray) < 3:
        return None
    small = [cv2.resize(g, (64, 64), interpolation=cv2.INTER_AREA) for g in face_crops_gray]
    yy, xx = np.mgrid[0:64, 0:64].astype(np.float32)
    A = np.stack([xx.ravel(), yy.ravel(), np.ones(64 * 64, np.float32)], axis=1)
    res = []
    for a, b in zip(small[:-1], small[1:]):
        flow = cv2.calcOpticalFlowFarneback(a, b, None, 0.5, 2, 15, 2, 5, 1.1, 0)
        resid2 = 0.0
        for c in range(2):
            coef, *_ = np.linalg.lstsq(A, flow[..., c].ravel(), rcond=None)
            resid2 += (flow[..., c].ravel() - A @ coef) ** 2
        res.append(float(np.sqrt(resid2).mean()))
    return float(np.mean(res))


# ---------------------------------------------------------------- quality (higher = better)
def quality_scores(frame_bgr: np.ndarray, face_bgr: np.ndarray, box) -> dict:
    g = _gray(face_bgr) if face_bgr.size else np.zeros((8, 8), np.uint8)
    sharp = float(min(1.0, cv2.Laplacian(g, cv2.CV_64F).var() / 120.0))
    mean = float(g.mean())
    bright = float(max(0.0, 1.0 - abs(mean - 125.0) / 125.0))
    size = float(min(1.0, box[2] / (0.22 * frame_bgr.shape[1])))
    overall = float(min(sharp, bright, size) * 0.6 + 0.4 * (sharp * bright * size) ** (1 / 3))
    return {"sharpness": sharp, "brightness": bright, "face_size": size, "overall": overall}


def to_scores(moire, glare, hf, edge, rigid) -> dict:
    out = {
        "moire": _sig(moire, "moire"),
        "glare": _sig(glare, "glare"),
        "flatness": _sig(hf, "flatness"),
        "screen_edge": _sig(edge, "screen_edge"),
        "rigid_motion": _sig(rigid, "rigid_motion") if rigid is not None else 0.0,
    }
    return out
