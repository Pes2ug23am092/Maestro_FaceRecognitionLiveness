"""liveness_cnn.pt -> ONNX fp32 -> INT8 (static QDQ), parity check, CPU latency benchmark.

    python train/export_onnx.py                       # calibrates the INT8 model on val crops (or random if none)
    python train/export_onnx.py --calib ../data/crops

Targets (plan Phase 4 / challenge 2): file < 10 MB, 8-frame call well under 400 ms on laptop CPU, and the
SAME .onnx used by the Lambda container (A) and onnxruntime-web in the kiosk browser (C).
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

import numpy as np
import onnxruntime as ort
import timm
import torch
from onnxruntime.quantization import CalibrationDataReader, QuantFormat, QuantType, quantize_static

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from train.train_cnn import MEAN, SIZE, STD  # noqa: E402

ART = Path(__file__).resolve().parent.parent / "artifacts"


class Reader(CalibrationDataReader):
    def __init__(self, arrays):
        self.it = iter([{"input": a[None]} for a in arrays])

    def get_next(self):
        return next(self.it, None)


def calib_arrays(crops: Path | None, n=64):
    import cv2

    arrs = []
    if crops and (crops / "crops_manifest.csv").exists():
        with open(crops / "crops_manifest.csv") as f:
            rows = [r for r in csv.DictReader(f) if r["split"] == "val"][:n]
        for r in rows:  # calibration uses VAL only, never test
            img = cv2.imread(str(crops / r["crop"]))
            x = (cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0 - MEAN) / STD
            arrs.append(x.transpose(2, 0, 1))
    if not arrs:
        print("WARNING: no calibration crops, using random data (INT8 accuracy will be unvalidated)")
        arrs = [np.random.default_rng(i).normal(0, 1, (3, SIZE, SIZE)).astype(np.float32) for i in range(n)]
    return arrs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--calib", default="../data/crops")
    a = ap.parse_args()

    m = timm.create_model("mobilenetv3_small_100", pretrained=False, num_classes=5)
    m.load_state_dict(torch.load(ART / "liveness_cnn.pt", map_location="cpu"))
    m.eval()
    fp32 = ART / "liveness_cnn.onnx"
    torch.onnx.export(m, torch.zeros(1, 3, SIZE, SIZE), str(fp32), input_names=["input"], output_names=["logits"],
                      dynamic_axes={"input": {0: "n"}, "logits": {0: "n"}}, opset_version=17, dynamo=False)
    arrs = calib_arrays(Path(a.calib))
    int8 = ART / "liveness_cnn.int8.onnx"
    quantize_static(str(fp32), str(int8), Reader(arrs), quant_format=QuantFormat.QDQ,
                    activation_type=QuantType.QUInt8, weight_type=QuantType.QInt8)
    print("NOTE: INT8 is only for experiments. For MobileNetV3 it failed parity (see flips below); ship the fp32 file.")

    x = np.stack(arrs[:32]).astype(np.float32)
    outs = {}
    for name, p in (("fp32", fp32), ("int8", int8)):
        so = ort.SessionOptions()
        so.intra_op_num_threads = 2
        s = ort.InferenceSession(str(p), so, providers=["CPUExecutionProvider"])
        outs[name] = torch.softmax(torch.from_numpy(s.run(None, {"input": x})[0]), 1).numpy()
        batch = x[:8]
        s.run(None, {"input": batch})
        t = []
        for _ in range(30):
            t0 = time.perf_counter()
            s.run(None, {"input": batch})
            t.append((time.perf_counter() - t0) * 1000)
        print(f"{name}: {p.stat().st_size / 1e6:.2f} MB | 8-frame call p50 {np.percentile(t, 50):.1f} ms, "
              f"p95 {np.percentile(t, 95):.1f} ms")
    live_fp, live_i8 = 1 - outs["fp32"][:, 0], 1 - outs["int8"][:, 0]
    print(f"parity fp32 vs int8 on {len(x)} crops: max |dspoof_prob| = {np.abs(live_fp - live_i8).max():.4f}, "
          f"decision flips @0.5 = {int(((live_fp > .5) != (live_i8 > .5)).sum())}")


if __name__ == "__main__":
    main()
