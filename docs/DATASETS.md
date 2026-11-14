# Datasets used (constraint C7: sources, licence notes, use)

All downloaded files live in `data/` (git-ignored). Public data is used for TRAINING/CALIBRATION of the prototype only. The final claims must come from our own consented captures (plan section 6).

| Dataset | Source | Content used | Licence / terms | Use here |
|---|---|---|---|---|
| CASIA-FASD (frames) | Hugging Face `akahana/anti-spoofing-casiafasd` (66 MB) | 30 subjects, live / warped-photo print / cut-photo print / tablet video replay; clip id gives attack type | Research dataset (CASIA); HF copy has no licence stated. Research/prototype use only, not redistributed | Train/val/test by SUBJECT (18/6/6); held-out test subjects for the full-pipeline evaluation |
| CelebA-Spoof (1 shard) | Hugging Face `Ar4ikov/celebA_spoof`, `valid-00000` (503 MB) | 3000 images (1500 live, 1500 spoof); only live/spoof label, no attack type | Research dataset (CelebA-Spoof, non-commercial); HF copy no licence stated | Binary-labelled extra data (loss treats "spoof, type unknown" as not-live). Split by image hash, so the same person can appear in several splits: **its numbers are optimistic** |
| YuNet face detector | opencv_zoo `face_detection_yunet_2023mar.onnx` | model weights | MIT | Face detection + landmarks |
| SFace face recogniser | opencv_zoo `face_recognition_sface_2021dec.onnx` | model weights | Apache-2.0 | Local identity match (stand-in for Rekognition) |
| MobileNetV3-small | timm `mobilenetv3_small_100` ImageNet weights | pre-trained backbone | Apache-2.0 | Fine-tuned for 5-way attack classification |

## Not covered by public data (need our own capture)
- **Screen replay of a PHOTO** and **mask (3D / printed)** attack types: CASIA-FASD has neither; CelebA-Spoof has them but without type labels. These need `models/collect/capture.py` sessions.
- Real kiosk webcam, lighting and distances (CASIA is low-resolution video).

## Candidates found (CC-BY-4.0, not yet downloaded)
AxonData on Hugging Face: `Display_replay_attacks` (156 MB, includes Real jpgs + screen videos), `3D_paper_mask_attack_dataset_for_Liveness` (412 MB), `Replay_attack_mobile` (2.5 GB), `Anti_Spoofing_Cut_print_attack` (1.1 GB). Useful for the screen-replay and mask classes; videos need frame extraction.
