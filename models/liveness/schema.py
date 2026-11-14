"""Score schema = the contract between B (liveness) -> A (Lambda /decide) and D (fusion).

Keep this stable. Any change must be announced and logged in memory.md.
All numbers are plain floats so the dict is JSON-serialisable.
"""
from __future__ import annotations

SCHEMA_VERSION = "b.score.v1"

ATTACK_CLASSES = ["live", "print", "screen_photo", "video_replay", "mask"]


def empty_score() -> dict:
    return {
        "schema": SCHEMA_VERSION,
        # Probability the subject is a presentation attack (0 = live, 1 = spoof).
        "spoof_prob": 1.0,
        # Per attack type probability from the CNN (sums to 1 if model present, else None).
        "attack_probs": None,
        # Individual detector outputs, each 0..1 where HIGHER = MORE SPOOF-LIKE.
        "signals": {
            "cnn_spoof": None,
            "moire": 0.0,
            "glare": 0.0,
            "flatness": 0.0,
            "rigid_motion": 0.0,
            "screen_edge": 0.0,
        },
        # Veto flags: True means a signal is over its hard threshold (D's fusion may force DENY).
        "veto": {"any": False, "reasons": []},
        # Capture quality, each 0..1 where HIGHER = BETTER.
        "quality": {
            "overall": 0.0,
            "sharpness": 0.0,
            "brightness": 0.0,
            "face_size": 0.0,
            "face_found": False,
            "frames_used": 0,
        },
        # Best frame index for the identity match (A uses this frame for Rekognition).
        "best_frame": 0,
        "timings_ms": {},
        "model": {"cnn": None, "cues": "cues.v1"},
    }
