import base64
import csv
import json
import sys
from pathlib import Path

import cv2
import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE / "lambda"))
sys.path.insert(0, str(HERE))
import handler as H  # noqa: E402

ROOT = HERE.parent
CASIA = ROOT / "data" / "downloads" / "casia" / "x"


def b64(img):
    return base64.b64encode(cv2.imencode(".jpg", img)[1].tobytes()).decode()


def test_unknown_op_and_garbage_fail_closed():
    assert H.handler({"op": "nope"})["statusCode"] == 400
    r = H.handler({"op": "score", "frames": ["!!!not base64!!!"]})
    assert r["statusCode"] in (200, 500)
    if r["statusCode"] == 200:  # no decodable frames -> no face -> spoof_prob 1.0 (fail closed)
        assert json.loads(r["body"])["spoof_prob"] == 1.0


def test_api_gateway_proxy_event_and_challenge_make():
    r = H.handler({"body": json.dumps({"op": "challenge_make"})})
    ch = json.loads(r["body"])
    assert r["statusCode"] == 200 and ch["action"] in ("turn_left", "turn_right") and len(ch["colors"]) >= 4


@pytest.mark.skipif(not CASIA.exists(), reason="public data not downloaded")
def test_score_real_frames_returns_contract():
    rows = [r for r in csv.DictReader(open(ROOT / "data" / "crops" / "crops_manifest.csv")) if r["source"] == "casia"]
    live = [r for r in rows if r["cls"] == "live" and r["split"] == "test"][:6]
    frames = []
    for r in live:
        stem = Path(r["file"]).name
        part = "test_img" if (CASIA / "test_img" / "test_img" / "color" / stem).exists() else "train_img"
        frames.append(b64(cv2.imread(str(CASIA / part / part / "color" / stem))))
    out = json.loads(H.handler({"op": "score", "frames": frames})["body"])
    for k in ("spoof_prob", "signals", "quality", "veto", "best_frame", "timings_ms", "schema"):
        assert k in out
    assert out["quality"]["face_found"] and out["spoof_prob"] < 0.5
