"""AWS Lambda entry point for B's liveness code (container image).

Invoke (API Gateway proxy event or direct):
  {"op": "score", "frames": ["<base64 jpeg>", ...]}                                   -> score dict (b.score.v1)
  {"op": "challenge_make"}                                                              -> challenge dict
  {"op": "challenge_verify", "challenge": {...}, "frames": [...], "t_ms": [...]}      -> verification dict
Response: {"statusCode": 200, "body": "<json>"} (API Gateway friendly). Fails closed: any error -> 500 + error code,
never an 'allow'. The model + detector are loaded ONCE per container (module level) so warm calls are fast.
"""
import base64
import json
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from liveness import challenge as chal  # noqa: E402
from liveness.face_crop import FaceFinder  # noqa: E402
from liveness.tier1 import Tier1  # noqa: E402

_t0 = time.perf_counter()
_TIER1 = Tier1()
_FINDER = FaceFinder()
INIT_MS = (time.perf_counter() - _t0) * 1000


def _decode(b64: str):
    if "," in b64[:64]:
        b64 = b64.split(",", 1)[1]
    return cv2.imdecode(np.frombuffer(base64.b64decode(b64), np.uint8), cv2.IMREAD_COLOR)


def _resp(code: int, obj: dict):
    return {"statusCode": code, "headers": {"Content-Type": "application/json"}, "body": json.dumps(obj, default=float)}


def handler(event, context=None):
    try:
        body = event if "op" in event else json.loads(event.get("body") or "{}")
        op = body.get("op", "score")
        if op == "challenge_make":
            return _resp(200, chal.make_challenge())
        frames = [f for f in (_decode(x) for x in body.get("frames", [])[:40]) if f is not None]
        if op == "score":
            out = _TIER1.score_burst(frames)
            out["timings_ms"]["container_init_ms"] = INIT_MS
            return _resp(200, out)
        if op == "challenge_verify":
            return _resp(200, chal.verify_challenge(body["challenge"], frames, body["t_ms"], _FINDER))
        return _resp(400, {"error": "UNKNOWN_OP"})
    except Exception as e:  # fail closed: caller must treat any non-200 as DENY / recapture
        return _resp(500, {"error": "INTERNAL_ERROR_FAIL_CLOSED", "detail": type(e).__name__})
