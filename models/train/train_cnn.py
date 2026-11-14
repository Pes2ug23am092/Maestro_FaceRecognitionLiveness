"""Fine-tune a small pretrained CNN (MobileNetV3-small) for 5-way presentation-attack classification.

    python train/train_cnn.py --crops ../data/crops --epochs 15
    python train/train_cnn.py --synthetic --epochs 1      # smoke test of the pipeline only (NOT a real model)

Reads ONLY the train and val splits. Model selection = lowest validation ACER (binary live vs spoof).
Output: artifacts/liveness_cnn.pt  (+ artifacts/train_report.json)
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import timm
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from liveness.schema import ATTACK_CLASSES  # noqa: E402

ART = Path(__file__).resolve().parent.parent / "artifacts"
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
SIZE = 160


def augment(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Camera-style augmentation: flip, brightness/contrast, blur, JPEG, small scale/shift."""
    if rng.random() < 0.5:
        img = img[:, ::-1]
    s = rng.uniform(0.9, 1.1)
    M = np.float32([[s, 0, rng.uniform(-8, 8)], [0, s, rng.uniform(-8, 8)]])
    img = cv2.warpAffine(np.ascontiguousarray(img), M, (SIZE, SIZE), borderMode=cv2.BORDER_REFLECT)
    img = np.clip(img.astype(np.float32) * rng.uniform(0.7, 1.3) + rng.uniform(-25, 25), 0, 255).astype(np.uint8)
    if rng.random() < 0.3:
        img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.5, 1.6))
    if rng.random() < 0.4:
        _, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(rng.integers(45, 95))])
        img = cv2.imdecode(enc, 1)
    return img


class Crops(Dataset):
    def __init__(self, items, train: bool):
        self.items, self.train, self.rng = items, train, np.random.default_rng(0)

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        img, y = self.items[i]
        if isinstance(img, (str, Path)):
            img = cv2.imread(str(img))
        if self.train:
            img = augment(img, self.rng)
        x = (cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0 - MEAN) / STD
        return torch.from_numpy(x.transpose(2, 0, 1)), y


SPOOF_UNKNOWN = -1  # label for "is a spoof, attack type not known" (e.g. CelebA-Spoof has only live/spoof)


def label_of(cls: str) -> int:
    return SPOOF_UNKNOWN if cls == "spoof" else ATTACK_CLASSES.index(cls)


def load_items(crops: Path, split: str, sources: set[str] | None = None):
    items = []
    with open(crops / "crops_manifest.csv") as f:
        for r in csv.DictReader(f):
            if r["split"] == split and (not sources or (r.get("source") or "own") in sources):
                items.append((crops / r["crop"], label_of(r["cls"])))
    return items


def partial_label_loss(logits, y, smoothing=0.05):
    """CE for known classes; for SPOOF_UNKNOWN use -log(P(not live)) so the model only has to be right that it is an attack."""
    known = y >= 0
    loss = logits.new_zeros(())
    if known.any():
        loss = loss + nn.functional.cross_entropy(logits[known], y[known], label_smoothing=smoothing, reduction="sum")
    if (~known).any():
        p_live = torch.softmax(logits[~known], 1)[:, 0]
        loss = loss - torch.log((1 - p_live).clamp_min(1e-6)).sum()
    return loss / len(y)


def synthetic_items(n, seed):
    rng = np.random.default_rng(seed)
    return [(rng.integers(0, 255, (SIZE, SIZE, 3), dtype=np.uint8), int(rng.integers(0, 5))) for _ in range(n)]


def binary_metrics(probs: np.ndarray, y: np.ndarray, thr=0.5):
    spoof = 1 - probs[:, 0]
    is_spoof = y != 0
    apcer = float(((spoof < thr) & is_spoof).sum() / max(is_spoof.sum(), 1))
    bpcer = float(((spoof >= thr) & ~is_spoof).sum() / max((~is_spoof).sum(), 1))
    return apcer, bpcer, (apcer + bpcer) / 2


@torch.no_grad()
def evaluate(model, dl, dev):
    model.eval()
    P, Y = [], []
    for x, y in dl:
        P.append(torch.softmax(model(x.to(dev)), 1).cpu().numpy())
        Y.append(y.numpy())
    return np.concatenate(P), np.concatenate(Y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--crops", default="../data/crops")
    ap.add_argument("--epochs", type=int, default=15)
    ap.add_argument("--bs", type=int, default=64)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--sources", default="", help="comma list of sources to use (own,casia,celeba); default all")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-pretrained", action="store_true")
    a = ap.parse_args()
    dev = "cuda" if torch.cuda.is_available() else "cpu"

    if a.synthetic:
        tr, va = synthetic_items(128, 1), synthetic_items(64, 2)
    else:
        srcs = {s for s in a.sources.split(",") if s} or None
        tr, va = load_items(Path(a.crops), "train", srcs), load_items(Path(a.crops), "val", srcs)
    assert tr and va, "empty train/val: run collect + prepare_data first"

    ytr = np.array([y for _, y in tr])
    bucket = np.where(ytr < 0, 5, ytr)  # unknown-spoof is its own sampling bucket
    cnt = np.bincount(bucket, minlength=6).astype(np.float32)
    sampler = WeightedRandomSampler((1.0 / np.maximum(cnt, 1))[bucket], len(tr), replacement=True)  # balance classes
    dl_tr = DataLoader(Crops(tr, True), batch_size=a.bs, sampler=sampler, num_workers=0)
    dl_va = DataLoader(Crops(va, False), batch_size=128, num_workers=0)

    model = timm.create_model("mobilenetv3_small_100", pretrained=not (a.no_pretrained or a.synthetic), num_classes=5)
    model.to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=a.epochs * len(dl_tr))
    best, hist = 9.0, []
    ART.mkdir(exist_ok=True)
    for ep in range(a.epochs):
        model.train()
        tl = 0.0
        for x, y in dl_tr:
            x, y = x.to(dev), y.to(dev)
            opt.zero_grad()
            loss = partial_label_loss(model(x), y)
            loss.backward()
            opt.step()
            sched.step()
            tl += loss.item() * len(y)
        P, Y = evaluate(model, dl_va, dev)
        apcer, bpcer, acer = binary_metrics(P, Y)
        hist.append({"epoch": ep, "loss": tl / len(tr), "val_apcer": apcer, "val_bpcer": bpcer, "val_acer": acer})
        print(hist[-1])
        if acer <= best:
            best = acer
            torch.save(model.state_dict(), ART / "liveness_cnn.pt")
    json.dump({"history": hist, "best_val_acer": best, "synthetic": a.synthetic, "n_train": len(tr), "n_val": len(va)},
              open(ART / "train_report.json", "w"), indent=2)
    print("best val ACER", best)


if __name__ == "__main__":
    main()
