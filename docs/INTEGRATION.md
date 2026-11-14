# Integration notes (branch `integration-b`, based on `role-a` + D's fusion + B's liveness)

## What /decide does now
`nonce (DynamoDB, single use)` -> **Tier 1 liveness on every frame (B: CNN + cues)** -> Rekognition on the **best frame** (DetectFaces quality, DetectLabels, SearchFacesByImage) in parallel with the user lookup -> **D's `fusion.decide`** (`config/thresholds.v2.json`) -> **schedule/zone/calendar policy** (`backend/policy_engine.py` on A's data via `backend/campus_adapter.py`) -> audit item in `kiosk_decisions`. Any exception -> `DENY INTERNAL_ERROR_FAIL_CLOSED`.

## Changes (and why)
| File | Change | Why |
|---|---|---|
| `backend/decide_service.py` | rewritten around `evaluate()` (shared with Tier 2); startup `warm_up()` | liveness was hard-coded 0.95; only frame 0 was used; `zone_id` was ignored but every ALLOW said `OK_IN_SCHEDULE`; first request was slow |
| `backend/liveness_service.py`, `models/` | B's model, cues, Tier 2, Lambda handler, browser JS | real liveness |
| `backend/policy_engine.py`, `campus_adapter.py` | Python policy (most-restrictive-wins, 16 + 6 tests) mapped onto A's zones/groups/schedules; zone risk from `security_tier` (1 low, 2 med, 3 high) | context-aware access + offline rules on the live data |
| `backend/tier2_service.py`, `main.py` `/challenge`, `/challenge/verify` | real Tier 2: random colour flash + head turn, single use in the sessions table | the old `/challenge/verify` returned `verified: true` for anything |
| `main.py` `/liveness/validate` | fails closed (the old code returned a demo "verified" on any error) | fail-open bug |
| `backend/enrolment_service.py` | `enrolled_at` is a timestamp | it stored `get_caller_identity()` (AWS account id + ARN) in the users table |
| `backend/kiosk_page.py` | `/kiosk` browser kiosk (burst capture, colour-flash challenge, OTP, enrol), `/config` | multi-frame capture + Tier 2 that Streamlit's single `camera_input` cannot do |
| `config/thresholds.v2.json` | liveness thresholds calibrated on public data, same schema as D's v1 | v1 was illustrative |
| SPOOF label set | only phone/tablet/paper/poster/frame/photograph | "Monitor/Screen/TV" vetoed a genuine user with a monitor behind them |
| tests | `tests/e2e_integrated.py` (real AWS), `tests/test_policy_adapter.py` | |

## Run
```
$env:AWS_...   # sandbox credentials in the environment (never in files)
pip install -r backend/requirements.txt         # (the models venv from B already has everything)
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000      # use 127.0.0.1, not localhost (Windows IPv6 delay)
http://127.0.0.1:8000/kiosk        # browser kiosk      |  Streamlit UI: streamlit run frontend/app.py (single frame, no Tier 2)
python tests/e2e_integrated.py --casia <CASIA dir>       # real AWS end to end (creates + removes USER_BTEST)
python -m pytest tests/test_policy_adapter.py tests/test_fusion.py tests/test_harness.py -q
```

## Measured (real AWS us-east-1, public CASIA frames of held-out people, one run, n small)
| Case | Result |
|---|---|
| genuine (high-res clip) | ALLOW `OK_IN_SCHEDULE`, ~0.9-1.1 s client time |
| printed photo, cut-out print, video replay (5 clips) | all DENY `SPOOF_DETECTED` (Rekognition matched the printed face as the enrolled person with similarity 1.0: liveness is what stopped it) |
| other person | DENY `ID_MISMATCH` |
| genuine at a door not in their schedule / 23:30 / audit day | DENY `ZONE_NOT_AUTHORISED` / STEP_UP `OUT_OF_HOURS_STEPUP` / DENY `CALENDAR_BLOCK` |
| warm decision time | server 0.68-0.91 s (nonce DynamoDB write ~0.22 s, liveness ~0.07 s, Rekognition ~0.45 s) |
Tests: policy adapter 6, D fusion 9, A harness 7 (live AWS): all pass.

## Known gaps / risks (honest)
- **Not deployed**: runs locally against real AWS; no API Gateway/Lambda yet. OTP store is in memory (A's `otp_service`): not Lambda-safe; OTP lockout never expires.
- **Model accuracy**: frame-level 96.2 % on public data (target 99.4 %); no screen-photo/mask training data; Tier 2 untested on a real screen-lit face (uses synthetic reflection + real face frames). Thresholds are draft; similarity thresholds are not calibrated on our own enrolments.
- **Two policy engines**: D's TypeScript engine (82 vectors, different schema) is NOT wired in; the online path uses the Python engine. The offline/edge path (D's TS + C's React app, which is currently a static demo) is not integrated. Parity between them is unproven.
- First Rekognition search right after enrolment can take ~4 s (mitigated: `/enroll` does a warm-up search).
- Streamlit's Tier 2 (single frame) now fails closed by design; use `/kiosk` for the real challenge.
- Cedar/Verified Permissions, AppConfig, Step Functions, EventBridge, Athena: not done (Cedar policy generator exists in B's local folder, 16/16 scenarios).

## Update (round 2)
- **Canonical dataset = A's ids** (`USER_*`, `ZONE_*`, `GRP_*`): they are what is live in DynamoDB + the Rekognition collection. The policy engine reads them through `campus_adapter.py`; D's and B's seeds are not used at runtime.
- **Webcam enrolment fixed**: laptop cameras give Rekognition sharpness ~25-45, so `FACE_QUALITY_MIN_SHARPNESS` 50 -> 20 (brightness 40 -> 30), IndexFaces `QualityFilter` HIGH -> AUTO, Tier 1 quality gate `TIER1_QUALITY_MIN` 0.15. Reproduced with a blurred frame at sharpness 38.9 (the reported value): enrols OK. The kiosk's Enrol button now tries up to 6 frames and explains failures. All overridable via env vars (`FACE_QUALITY_MIN_SHARPNESS`, ...).
- **Thresholds hot-reload**: edit `config/thresholds.v2.json` while the server runs (no restart) to tune live_allow/live_deny/similarity per risk level.
- **Lockout** (plan rule): 3 biometric failures -> 60 s lock per badge (DynamoDB `LOCK#<badge>` item). Verified end to end: 3rd impostor try returns `ATTEMPTS_EXCEEDED`, and the genuine user is refused while locked.
- **Can AWS alone reach 99 %?** Measured, `docs/AWS_ONLY_ACCURACY.md` (1,329 held-out public images): Rekognition label-based spoof checks accept 91-95 % of attacks (accuracy ~37 %); our CNN alone 96.2 %. Face Liveness (managed) cannot be measured headless. So AWS-only (as wired) is nowhere near 99 %.
- Latency after the lockout/audit writes: server 1.1-1.8 s warm (DynamoDB nonce write 0.2-0.5 s, Rekognition ~0.55 s, liveness 0.08 s): under 2 s, but the margin is thin. Easy win not yet done: validate the nonce and check the lock in parallel with the Rekognition calls.
