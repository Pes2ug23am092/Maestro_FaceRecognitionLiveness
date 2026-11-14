"""Tier 2: active, nonce-bound challenge = random colour-flash sequence + random head-turn action.

Why it works: the kiosk screen lights the face with a server-chosen random colour sequence. A live face reflects
THAT sequence in step; a pre-recorded video / replay screen cannot know it, and a printed photo cannot turn its head.
We test SYNCHRONY with a permutation test (does the face's colour change follow the issued sequence better than
random sequences do?), so it does not depend on the absolute strength of the reflection.

    ch = make_challenge()                         # server: send colors/action/seg_ms to the kiosk
    ... kiosk shows each colour seg_ms ms, captures frames with t_ms (ms since challenge start) ...
    res = verify_challenge(ch, frames, t_ms)      # server: res["passed"], res["colour"], res["action"]

Thresholds are UNCALIBRATED defaults (no real kiosk captures yet): calibrate on own data (plan Phase 9).
"""
from __future__ import annotations

import secrets
import time

import numpy as np

from .face_crop import FaceFinder, face_region

COLORS = {"red": (255, 40, 40), "green": (40, 255, 40), "blue": (40, 40, 255)}
ACTIONS = {"turn_left": +1, "turn_right": -1}  # sign of the raw-frame yaw proxy change (camera frame, NOT mirrored)
PARAMS = {
    "n_colors": 6, "seg_ms": 300, "ttl_s": 15, "lag_ms": (0, 100, 200, 300, 400),
    "p_max": 0.05, "score_min": 0.20, "n_perm": 400,
    "yaw_min": 0.18, "yaw_ratio": 2.0, "face_frac_min": 0.7, "min_frames": 8,
}


def make_challenge(rng: np.random.Generator | None = None, **over) -> dict:
    p = {**PARAMS, **over}
    r = rng or np.random.default_rng(secrets.randbits(64))
    names = list(COLORS)
    seq = [names[int(r.integers(len(names)))]]
    while len(seq) < p["n_colors"]:  # no immediate repeats, so every segment is a visible change
        c = names[int(r.integers(len(names)))]
        if c != seq[-1]:
            seq.append(c)
    return {"id": secrets.token_urlsafe(10), "colors": seq, "seg_ms": p["seg_ms"], "ttl_s": p["ttl_s"],
            "action": list(ACTIONS)[int(r.integers(len(ACTIONS)))], "issued_at": time.time()}


# ------------------------------------------------------------------ colour reflection (pure numeric, unit-testable)
def _chroma(rgb: np.ndarray) -> np.ndarray:
    return rgb / (rgb.sum(axis=1, keepdims=True) + 1e-6)


def _expected(colors: list[str], seg_ms: int, t_ms: np.ndarray, lag_ms: int) -> np.ndarray:
    idx = np.clip(((t_ms - lag_ms) // seg_ms).astype(int), 0, len(colors) - 1)
    # before the first segment starts (t < lag) there is no colour yet -> treat as first colour (clipped)
    return _chroma(np.array([COLORS[colors[i]] for i in idx], dtype=np.float64))


def _corr(obs: np.ndarray, exp: np.ndarray) -> float:
    o, e = obs - obs.mean(axis=0), exp - exp.mean(axis=0)
    d = np.linalg.norm(o) * np.linalg.norm(e)
    return float((o * e).sum() / d) if d > 1e-9 else 0.0


def colour_sync(region_rgb: np.ndarray, t_ms: np.ndarray, colors: list[str], seg_ms: int,
                rng: np.random.Generator | None = None, **over) -> dict:
    """region_rgb: (T,3) mean RGB of the skin region per frame. Returns score, p_value, lag_ms, ok."""
    p = {**PARAMS, **over}
    r = rng or np.random.default_rng(12345)
    obs = _chroma(np.asarray(region_rgb, dtype=np.float64))
    best = max(((_corr(obs, _expected(colors, seg_ms, t_ms, lag)), lag) for lag in p["lag_ms"]))
    score, lag = best
    # permutation test: how often do RANDOM colour sequences (same lag search) match as well?
    names, n, ge = list(COLORS), len(colors), 0
    for _ in range(p["n_perm"]):
        fake = [names[int(r.integers(3))] for _ in range(n)]
        s = max(_corr(obs, _expected(fake, seg_ms, t_ms, lg)) for lg in p["lag_ms"])
        ge += s >= score
    pval = (ge + 1) / (p["n_perm"] + 1)
    return {"score": score, "p_value": pval, "lag_ms": int(lag), "ok": bool(pval <= p["p_max"] and score >= p["score_min"])}


# ------------------------------------------------------------------ head-turn action
def yaw_proxy(landmarks: np.ndarray) -> float:
    """YuNet landmarks: [right eye, left eye, nose, right mouth, left mouth] (image coords). Nose offset from the
    eye midpoint, in inter-ocular distances. Positive = nose moved toward image-right (raw frame)."""
    re, le, nose = landmarks[0], landmarks[1], landmarks[2]
    io = abs(le[0] - re[0]) + 1e-6
    return float((nose[0] - (re[0] + le[0]) / 2) / io)


def verify_action(yaws: list[float], requested: str, **over) -> dict:
    p = {**PARAMS, **over}
    y = np.asarray([v for v in yaws if v is not None], dtype=np.float64)
    if len(y) < 4:
        return {"requested": requested, "peak": 0.0, "opposite": 0.0, "ok": False}
    base = float(np.median(y[: max(2, len(y) // 5)]))
    d = (y - base) * ACTIONS[requested]
    peak, opp = float(d.max()), float(max(0.0, -d.min()))
    return {"requested": requested, "peak": peak, "opposite": opp,
            "ok": bool(peak >= p["yaw_min"] and peak >= p["yaw_ratio"] * opp)}


# ------------------------------------------------------------------ full verification
def verify_challenge(ch: dict, frames: list[np.ndarray], t_ms: list[float], finder: FaceFinder | None = None,
                     now: float | None = None, **over) -> dict:
    p = {**PARAMS, **over}
    finder = finder or FaceFinder()
    reasons: list[str] = []
    now = time.time() if now is None else now
    if now - ch["issued_at"] > ch["ttl_s"]:
        return {"passed": False, "reasons": ["CHALLENGE_EXPIRED"]}
    if len(frames) != len(t_ms) or len(frames) < p["min_frames"]:
        return {"passed": False, "reasons": ["TOO_FEW_FRAMES"]}
    t = np.asarray(t_ms, dtype=np.float64)
    if np.any(np.diff(t) < 0) or t.min() < -50 or t.max() > ch["ttl_s"] * 1000:
        return {"passed": False, "reasons": ["BAD_TIMESTAMPS"]}

    rgb, yaws, keep = [], [], []
    for i, f in enumerate(frames):
        d = finder.detect(f)
        if d is None:
            continue
        reg = face_region(f, d["box"], shrink=0.5)
        if reg.size == 0:
            continue
        rgb.append(reg.reshape(-1, 3).mean(axis=0)[::-1])  # BGR -> RGB
        yaws.append(yaw_proxy(d["landmarks"]) if d.get("landmarks") is not None else None)
        keep.append(i)
    if len(keep) / len(frames) < p["face_frac_min"]:
        return {"passed": False, "reasons": ["FACE_NOT_TRACKED"], "frames_with_face": len(keep)}

    colour = colour_sync(np.array(rgb), t[keep], ch["colors"], ch["seg_ms"], **over)
    action = verify_action(yaws, ch["action"], **over)
    if not colour["ok"]:
        reasons.append("CHALLENGE_COLOUR_MISMATCH")
    if not action["ok"]:
        reasons.append("CHALLENGE_ACTION_MISSING")
    return {"passed": not reasons, "reasons": reasons, "colour": colour, "action": action,
            "frames_with_face": len(keep), "frames": len(frames)}
