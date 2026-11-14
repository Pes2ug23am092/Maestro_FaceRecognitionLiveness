import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from liveness import cues  # noqa: E402
from liveness.schema import SCHEMA_VERSION, empty_score  # noqa: E402
from liveness.tier1 import Tier1  # noqa: E402


def _skin(n=160, seed=0):
    rng = np.random.default_rng(seed)
    base = np.full((n, n, 3), (120, 140, 190), np.float32)
    yy, xx = np.mgrid[0:n, 0:n]
    base += 25 * np.sin(xx / 23.0)[..., None] + 18 * np.cos(yy / 31.0)[..., None]
    base += rng.normal(0, 6, base.shape)
    return np.clip(base, 0, 255).astype(np.uint8)


def test_moire_higher_for_grid_pattern():
    clean = _skin()
    grid = clean.astype(np.float32).copy()
    yy, xx = np.mgrid[0:160, 0:160]
    grid += 22 * np.sin(2 * np.pi * xx / 3.0)[..., None] * np.sin(2 * np.pi * yy / 3.1)[..., None]
    grid = np.clip(grid, 0, 255).astype(np.uint8)
    assert cues.moire_raw(grid) > cues.moire_raw(clean) + 1.0


def test_glare_detects_blown_highlights():
    img = _skin()
    img[20:70, 20:90] = 255
    assert cues.glare_raw(img) > 0.1
    assert cues.glare_raw(_skin()) < 0.01


def test_nonrigid_motion_small_for_rigid_shift():
    base = cv2_gray(_skin(160, 1))
    frames = [np.roll(base, s, axis=1) for s in (0, 2, 4, 6)]
    rigid = cues.nonrigid_motion_raw(frames)
    rng = np.random.default_rng(3)
    noisy = [np.clip(base.astype(np.int16) + rng.normal(0, 25, base.shape), 0, 255).astype(np.uint8) for _ in range(4)]
    nonrigid = cues.nonrigid_motion_raw(noisy)
    assert rigid is not None and nonrigid is not None and rigid < nonrigid


def test_need_three_frames_for_motion():
    g = cv2_gray(_skin())
    assert cues.nonrigid_motion_raw([g, g]) is None


def test_no_face_fails_closed():
    blank = np.full((480, 640, 3), 127, np.uint8)
    out = Tier1().score_burst([blank] * 5)
    assert out["schema"] == SCHEMA_VERSION
    assert out["quality"]["face_found"] is False
    assert out["spoof_prob"] == 1.0


def test_schema_has_contract_keys():
    s = empty_score()
    for k in ("spoof_prob", "signals", "quality", "veto", "best_frame", "timings_ms"):
        assert k in s


def cv2_gray(bgr):
    import cv2

    return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
