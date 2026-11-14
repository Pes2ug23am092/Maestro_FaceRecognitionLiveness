import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from liveness import challenge as C  # noqa: E402

SKIN = np.array([190.0, 140.0, 120.0])


def reflect(colors, seg_ms, t_ms, amp=10.0, noise=1.5, seed=0, lag=100):
    """Simulate skin mean-RGB lit by the displayed sequence (reflection amplitude `amp` levels) + sensor noise."""
    rng = np.random.default_rng(seed)
    exp = np.array([C.COLORS[colors[i]] for i in np.clip(((t_ms - lag) // seg_ms).astype(int), 0, len(colors) - 1)], float)
    return SKIN + amp * (exp / 255.0) + rng.normal(0, noise, (len(t_ms), 3))


def test_challenge_has_no_immediate_repeats_and_is_random():
    a, b = C.make_challenge(), C.make_challenge()
    for ch in (a, b):
        assert all(x != y for x, y in zip(ch["colors"], ch["colors"][1:]))
        assert ch["action"] in C.ACTIONS
    assert a["id"] != b["id"]


def test_live_reflection_passes():
    ch = C.make_challenge(np.random.default_rng(1))
    t = np.arange(0, 2100, 100, dtype=float)
    r = C.colour_sync(reflect(ch["colors"], ch["seg_ms"], t), t, ch["colors"], ch["seg_ms"])
    assert r["ok"] and r["p_value"] <= 0.05 and r["lag_ms"] in (0, 100, 200), r


def test_replay_with_other_sequence_fails():
    """A pre-recorded video lit by ITS OWN colours (not the issued sequence) must not pass."""
    fails = 0
    for s in range(20):
        ch = C.make_challenge(np.random.default_rng(100 + s))
        other = C.make_challenge(np.random.default_rng(900 + s))
        t = np.arange(0, 2100, 100, dtype=float)
        r = C.colour_sync(reflect(other["colors"], ch["seg_ms"], t, seed=s), t, ch["colors"], ch["seg_ms"])
        fails += (not r["ok"])
    assert fails >= 18, f"only {fails}/20 replays rejected"


def test_static_photo_with_no_reflection_fails():
    ch = C.make_challenge(np.random.default_rng(3))
    t = np.arange(0, 2100, 100, dtype=float)
    r = C.colour_sync(reflect(ch["colors"], ch["seg_ms"], t, amp=0.0), t, ch["colors"], ch["seg_ms"])
    assert not r["ok"]


def lm(nose_dx):
    # right eye, left eye, nose, right mouth, left mouth ; inter-ocular distance 40
    return np.array([[80, 100], [120, 100], [100 + nose_dx, 125], [85, 150], [115, 150]], float)


def test_yaw_proxy_sign_and_action_check():
    assert C.yaw_proxy(lm(0)) == pytest.approx(0, abs=1e-6)
    assert C.yaw_proxy(lm(10)) > 0 > C.yaw_proxy(lm(-10))
    still = [C.yaw_proxy(lm(0.5 * np.sin(i))) for i in range(12)]
    left = [C.yaw_proxy(lm(x)) for x in (0, 0, 1, 4, 8, 11, 12, 8, 3, 0, 0, 0)]  # nose moves to image-right = turn_left
    assert C.verify_action(left, "turn_left")["ok"]
    assert not C.verify_action(left, "turn_right")["ok"]
    assert not C.verify_action(still, "turn_left")["ok"]


def test_expired_and_malformed_inputs_rejected():
    ch = C.make_challenge()
    frames = [np.zeros((64, 64, 3), np.uint8)] * 10
    assert C.verify_challenge(ch, frames, list(range(0, 1000, 100)), now=ch["issued_at"] + 99)["reasons"] == ["CHALLENGE_EXPIRED"]
    assert C.verify_challenge(ch, frames[:3], [0, 100, 200])["reasons"] == ["TOO_FEW_FRAMES"]
    assert C.verify_challenge(ch, frames, list(range(900, -100, -100)))["reasons"] == ["BAD_TIMESTAMPS"]
    assert C.verify_challenge(ch, frames, list(range(0, 1000, 100)))["reasons"] == ["FACE_NOT_TRACKED"]  # blank frames: fail closed
