"""B's liveness code behind a small service API (Tier 1 passive, Tier 2 nonce challenge).

Models are loaded once per process/container. Everything fails closed: no face / error -> spoof_prob 1.0 or failed challenge.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "models"))
from liveness import challenge as _chal  # noqa: E402
from liveness.face_crop import FaceFinder  # noqa: E402
from liveness.tier1 import Tier1  # noqa: E402

_tier1 = Tier1()
_finder = FaceFinder()
MODEL_NAME = _tier1.model_name


def decode_b64(b64: str) -> np.ndarray | None:
    if "," in b64[:64]:
        b64 = b64.split(",", 1)[1]
    try:
        return cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        return None


def score_frames(frames_bgr: list[np.ndarray]) -> dict:
    """Tier 1 on a burst (works with a single frame too: the motion cue is simply skipped)."""
    return _tier1.score_burst(frames_bgr)


def make_challenge() -> dict:
    return _chal.make_challenge()


def verify_challenge(ch: dict, frames_bgr: list[np.ndarray], t_ms: list[float]) -> dict:
    return _chal.verify_challenge(ch, frames_bgr, t_ms, _finder)


def encode_jpeg(bgr: np.ndarray, quality: int = 90) -> bytes:
    return cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()


def dumps(obj) -> str:
    return json.dumps(obj, default=float)


now_ms = lambda: time.perf_counter() * 1000  # noqa: E731
