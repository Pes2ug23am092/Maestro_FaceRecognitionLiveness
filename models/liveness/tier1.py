"""Tier 1 passive liveness: burst of frames -> score dict (schema b.score.v1).

    from liveness.tier1 import Tier1
    t1 = Tier1()                       # loads YuNet, and the CNN if artifacts/liveness_cnn.onnx exists
    score = t1.score_burst(frames)     # frames: list of BGR uint8 images (5-8 frames)

Runs in the Lambda container (A) and, with the same ONNX file, in the browser edge agent (C).
"""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from . import cues
from .face_crop import CNN_INPUT, FaceFinder, context_crop, face_region
from .schema import ATTACK_CLASSES, SCHEMA_VERSION, empty_score

ARTIFACTS = Path(__file__).resolve().parent.parent / "artifacts"
# fp32 first: INT8 quantisation of MobileNetV3 flipped 17/32 decisions in our parity check (and was not faster).
CNN_CANDIDATES = ["liveness_cnn.onnx"]
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)

# Hard veto thresholds on the 0..1 signal scores (to be frozen in config/thresholds.v1.json in Phase 9).
VETO = {"moire": 0.95, "screen_edge": 0.95, "cnn_spoof": 0.97}
# Weights for the heuristic spoof_prob used only when no CNN is available.
HEUR_W = {"moire": 0.35, "glare": 0.15, "flatness": 0.15, "rigid_motion": 0.20, "screen_edge": 0.15}


class Tier1:
    def __init__(self, model_path: str | None = None):
        self.finder = FaceFinder()
        self.sess = None
        self.model_name = None
        paths = [Path(model_path)] if model_path else [ARTIFACTS / n for n in CNN_CANDIDATES]
        for p in paths:
            if p.exists():
                import onnxruntime as ort

                so = ort.SessionOptions()
                so.intra_op_num_threads = 2
                self.sess = ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"])
                self.input_name = self.sess.get_inputs()[0].name
                self.model_name = p.name
                break

    # ------------------------------------------------------------------ CNN
    @staticmethod
    def _prep(crop_bgr: np.ndarray) -> np.ndarray:
        x = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        x = (x - MEAN) / STD
        return x.transpose(2, 0, 1)

    def _cnn(self, crops: list[np.ndarray]) -> np.ndarray:
        x = np.stack([self._prep(c) for c in crops]).astype(np.float32)
        logits = self.sess.run(None, {self.input_name: x})[0]
        e = np.exp(logits - logits.max(axis=1, keepdims=True))
        return (e / e.sum(axis=1, keepdims=True)).mean(axis=0)  # mean prob over frames

    # ------------------------------------------------------------------ main entry
    def score_burst(self, frames: list[np.ndarray]) -> dict:
        t0 = time.perf_counter()
        out = empty_score()
        out["quality"]["frames_used"] = len(frames)
        dets = [self.finder.detect(f) for f in frames]
        t_det = time.perf_counter()

        good = [i for i, d in enumerate(dets) if d is not None]
        if not good:
            out["timings_ms"] = {"total": (time.perf_counter() - t0) * 1000}
            return out  # face_found False, spoof_prob 1.0 (fail closed); policy turns this into recapture

        out["quality"]["face_found"] = True
        crops, regions, ctxs, qs = {}, {}, {}, {}
        for i in good:
            box = dets[i]["box"]
            ctxs[i] = context_crop(frames[i], box)
            regions[i] = face_region(frames[i], box)
            qs[i] = cues.quality_scores(frames[i], regions[i], box)

        best = max(good, key=lambda i: qs[i]["overall"])
        out["best_frame"] = int(best)
        out["quality"].update({k: qs[best][k] for k in ("overall", "sharpness", "brightness", "face_size")})

        # cues: spatial cues on best 3 frames (median), temporal cue over the burst
        top = sorted(good, key=lambda i: -qs[i]["overall"])[:3]
        moire = float(np.median([cues.moire_raw(regions[i]) for i in top]))
        glare = float(np.median([cues.glare_raw(regions[i]) for i in top]))
        hf = float(np.median([cues.hf_ratio_raw(regions[i]) for i in top]))
        edge = float(np.median([cues.screen_edge_raw(ctxs[i]) for i in top]))
        rigid = cues.nonrigid_motion_raw([cv2.cvtColor(ctxs[i], cv2.COLOR_BGR2GRAY) for i in good])
        sc = cues.to_scores(moire, glare, hf, edge, rigid)
        out["signals"].update(sc)
        out["signals"]["raw"] = {"moire": moire, "glare": glare, "hf_ratio": hf, "screen_edge": edge,
                                 "nonrigid": rigid}
        t_cues = time.perf_counter()

        # CNN
        if self.sess is not None:
            probs = self._cnn([ctxs[i] for i in top])
            out["attack_probs"] = {c: float(p) for c, p in zip(ATTACK_CLASSES, probs)}
            cnn_spoof = float(1.0 - probs[ATTACK_CLASSES.index("live")])
            out["signals"]["cnn_spoof"] = cnn_spoof
            out["model"]["cnn"] = self.model_name
            # CNN is primary; cues nudge it (max of CNN and a damped cue score) so strong physical cues are not lost.
            heur = sum(HEUR_W[k] * sc[k] for k in HEUR_W)
            out["spoof_prob"] = float(min(1.0, 0.8 * cnn_spoof + 0.2 * heur))
        else:
            out["spoof_prob"] = float(min(1.0, sum(HEUR_W[k] * sc[k] for k in HEUR_W)))
            out["model"]["cnn"] = None
        t_cnn = time.perf_counter()

        reasons = [k for k, th in VETO.items() if (out["signals"].get(k) or 0.0) >= th]
        out["veto"] = {"any": bool(reasons), "reasons": reasons}
        out["timings_ms"] = {
            "detect": (t_det - t0) * 1000,
            "cues": (t_cues - t_det) * 1000,
            "cnn": (t_cnn - t_cues) * 1000,
            "total": (t_cnn - t0) * 1000,
        }
        return out
