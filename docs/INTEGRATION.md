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

## Update (round 3): latency root cause, campus schedule, demo scenarios
- **Why scans took 4-6 s**: on the demo laptop every NEW TLS connection to AWS costs ~1.5-2 s (measured: DynamoDB and Rekognition alike, with and without IPv6; one 58 s outlier), while a warm call takes ~0.3 s. `/decide` used 3 DynamoDB + 3 Rekognition calls concurrently, and idle connections go cold, so scans often hit cold connections. The kiosk timer also included the `/session` call.
- **Fixes**: one Rekognition call per scan (DetectFaces/DetectLabels removed: labels let 91-95 % of attacks through and caused a false deny; quality comes from Tier 1); nonce / lockout / user lookups run in parallel with the model; audit + success-bookkeeping written in the background (synchronous on Lambda); a keep-warm loop refreshes several parallel connections to each service every 12 s (including the REAL collection-search path); `tcp_keepalive`; a hedged search (second identical search after 0.9 s, first success wins); the kiosk fetches the nonce while capturing, and the clock starts at capture-complete.
- **Measured** (`tests/latency_check.py`, 25 warm requests, real AWS from the laptop): p50 952 ms, p95 1.2 s, max 2.3 s, 24/25 <= 2 s; 30-60 s idle gap: 0.93-0.98 s. Full e2e cases 0.5-1.6 s. Remaining tail = this network's new-connection cost; a Lambda inside AWS does not pay it.
- **Campus schedule data** (`config/seed_campus.json`, loaded into DynamoDB with `infra/seed_extra.py`, which never clobbers users/enrolments): 31 schedule rows for all 7 groups x 8 doors (employee entrance 24/7, weekday-only offices, lab/server room by role, visitor 09-17, contractor north gate) and 4 new users (security, executive, visitor, contractor). The earlier denial was because employees only had Mon-Fri entrance access and it was Saturday.
- **Website demo scenarios** (`backend/kiosk.html`, served at /kiosk): 12 one-click campus-rule scenarios with "Run all" and expected-vs-actual checks (a test parses them and checks each against the policy engine), 6 physical attack/edge-case scenarios with a 3 s countdown (printed photo, phone photo, video replay, mask, someone else, poor light), "Enrol under ALL demo badges" (`/demo/enrol-all`: one face under every badge; statuses preserved, so the revoked badge stays revoked), best-match-per-badge identity.

## Deployment (round 4): what runs in AWS
Backend = **Lambda container image** `hidb-kiosk-backend` (FastAPI via Mangum + B's liveness model, 2 GB, 30 s, X-Ray active) behind an **API Gateway HTTP API** (`$default` route, public HTTPS URL, kiosk page at `/kiosk`). Image pushed to **ECR** repo `hidb-kiosk-backend`. IAM role `hidb-kiosk-lambda-role` (least privilege: `kiosk_*` tables, the one Rekognition collection, the signing key, the evidence bucket, logs, X-Ray). **EventBridge** rule `hidb-kiosk-keepwarm` pings every 5 min (keeps the instance and its AWS connections warm). **CloudWatch** log group (7-day retention) + dashboard `hidb-kiosk`. OTP state moved from memory to DynamoDB (Lambda-safe). Scripts: `Dockerfile.lambda`, `infra/deploy_lambda.py`, `infra/dashboard.py`; output in `config/deployed.json`.
Console map (us-east-1): Lambda -> hidb-kiosk-backend | API Gateway -> hidb-kiosk-backend | ECR -> hidb-kiosk-backend | EventBridge -> Rules -> hidb-kiosk-keepwarm | CloudWatch -> Dashboards -> hidb-kiosk | IAM -> Roles -> hidb-kiosk-lambda-role | X-Ray traces | DynamoDB kiosk_* | Rekognition collection | KMS alias/kiosk-policy-signer.

## DEPLOYED (round 4 result)  https://i9as63zk3e.execute-api.us-east-1.amazonaws.com/kiosk
Docker push to ECR failed/was too slow, so the deployment is **zip + S3**: Lambda `hidb-kiosk-backend` (python3.12, 2 GB, 60 s, X-Ray on, code.zip 6 MB; heavy libs `hidb/deps.zip` 77 MB + `hidb/deps_extra.zip` in the evidence bucket, unpacked to /tmp on cold start) behind API Gateway HTTP API `hidb-kiosk-backend`; EventBridge `hidb-kiosk-keepwarm` (5 min); CloudWatch dashboard `hidb-kiosk`, 7-day logs; role `hidb-kiosk-lambda-role`; OTP now in DynamoDB. Scripts: `infra/deploy_zip.py [--extra-only]`, `infra/dashboard.py`, `lambda_boot.py`.
Measured in the cloud (tests/e2e_integrated.py --api <url>): all 11 cases correct, lockout works; **server time 175-390 ms**; cold start ~6 s, warm /health 0.4-0.5 s. The client round trip seen from the laptop (2-4 s in the script) is dominated by the laptop's new-TLS-connection cost + uploading ~700 KB of frames, not by the backend.
Caveats: the API is PUBLIC with no authentication (add Cognito/WAF or delete after the demo); OTP demo code is returned in the response; `ecr` repo `hidb-kiosk-backend` is an unused leftover; temp credentials expire.
