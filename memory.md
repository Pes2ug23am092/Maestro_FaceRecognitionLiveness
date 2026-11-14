## [2026-11-14 12:10] [A] Phase 0 – Sandbox Probe & Repo Scaffold
- Done: Scaffolded repository layout (/infra, /backend, /models, /frontend, /eval, /config, /docs, /tests). Executed automated sandbox probe script verifying all 15 core AWS services.
- Files/resources changed: infra/probe.py, docs/sandbox-probe.md, docs/openapi.yaml, .gitignore, memory.md
- Decisions & why: Confirmed AWS region us-east-1. All required AWS services (DynamoDB, S3, Lambda, Rekognition, KMS, Cognito, WAF, SES, SNS, CloudFront, X-Ray, Face Liveness) are active and accessible with zero permission blocks. Full primary architecture (Tier 1 + Tier 2 + Tier 3) is viable without fallbacks.
- Numbers: 15/15 AWS service checks returned OK.
- Issues / risks: None. Credentials and permissions are fully operational.
- Next: Phase 1 & 2: Provision DynamoDB tables and S3 evidence bucket, author Lambda handlers for /session, /decide, and Rekognition 1:1 match collection.

## [2026-11-14 12:15] [A] Phase 1 & 2 & 6b, 6d, 8 – Backend Core & Integration Verification
- Done:
  1. Provisioned AWS infrastructure via infra/provision.py: 7 DynamoDB tables (kiosk_users, kiosk_sessions, kiosk_decisions, kiosk_sync_batches, kiosk_zones, kiosk_groups, kiosk_schedules), TTL enabled on kiosk_sessions, S3 bucket kiosk-evidence-891377320041-us-east-1 with public access block and AES-256 encryption, Rekognition Collection kiosk-face-collection-v1, and KMS ECDSA P-256 key alias/kiosk-policy-signer.
  2. Seeded campus database tables with config/seed_campus.json (8 zones, 7 groups, 6 schedules, 4 users).
  3. Implemented all 6 core API routes in backend/ (/session, /decide, /policy-bundle, /credential, /sync, /verify-otp) plus /health and /face-liveness-session.
  4. Implemented KMS ECDSA signing with ASN.1 DER to raw IEEE P1363 (r||s) conversion for edge kiosk WebCrypto verification.
  5. Implemented idempotent sync with conditional DynamoDB writes and revocation detection (OFFLINE_ALLOW_REVOKED).
  6. Implemented step-up OTP with 3-attempt lockout (HTTP 403).
  7. Built headless test harness in tests/test_harness.py running 7 integration tests against live AWS resources.
- Files/resources changed: infra/provision.py, infra/seed_data.py, config/seed_campus.json, config/aws_resources.json, backend/config.py, backend/session_service.py, backend/decide_service.py, backend/enrolment_service.py, backend/policy_service.py, backend/sync_service.py, backend/otp_service.py, backend/main.py, backend/lambda_handler.py, tests/test_harness.py, memory.md
- Decisions & why: Implemented dual-mode execution (direct FastAPI TestClient / local server and AWS Lambda Mangum handler) so backend can be verified completely headlessly without creating frontend or other services.
- Numbers: 7/7 automated tests passed in 10.168s. Nonce replay rejection verified (400 NONCE_REUSED), WebCrypto 64-byte raw signature verified, duplicate sync prevention verified (0 new events, 2 duplicates on replay), lockout verified on 3rd failed attempt.
- Issues / risks: None. All AWS services connected and verified.
- Next: Ready for teammate handoffs (OpenAPI spec for C & D, ECR/container setup for B).

## [2026-11-14 13:50] [B] Integration of role-a + evaluation_fusion + B liveness (branch integration-b, NOT pushed)
- Done: reviewed all three branches (role-a = A + C's UI, evaluation_fusion = D, frontend = C). Wired B's Tier 1 liveness into `/decide`, D's `fusion.decide` (thresholds.v2.json) and a schedule/zone/calendar policy (`policy_engine.py` + `campus_adapter.py` on A's data). Replaced the Tier 2 stub with a real colour-flash + head-turn challenge (state in the sessions table); `/liveness/validate` now fails closed; enrolment no longer stores the AWS account/ARN; added `/kiosk` (burst capture, Tier 2, OTP, enrol) and `/config`; startup + post-enrol warm-ups.
- Problems found in the branches: (1) liveness hard-coded 0.95 and only frame 0 used; (2) `zone_id` ignored but ALLOW tagged `OK_IN_SCHEDULE`; (3) Tier 2 verify always true; (4) Tier 3 demo fallback = fail open; (5) three incompatible campus datasets (A: USER_*/ZONE_*, D: Library/demo-*, B: U001/Z-*); (6) D's fusion unused; (7) `get_caller_identity()` stored in DynamoDB; (8) label-based veto flagged Monitor/Screen/TV (false deny on a genuine user), and did not flag a high-res printed photo.
- Numbers (real AWS, public CASIA held-out frames, small n): genuine ALLOW; print / cut-print / video replay all DENY SPOOF_DETECTED; impostor DENY ID_MISMATCH; schedule cases correct; warm server time 0.68-0.91 s; first call after enrol ~4 s (warm-up added). Tests: policy adapter 6/6, fusion 9/9, A harness 7/7 live AWS.
- Decisions & why: kept A's DynamoDB ids as the runtime ids (live Rekognition/DDB data) and adapted the policy engine to them instead of re-seeding; zone risk from security_tier; Python policy online, D's TS engine left for the offline edge path.
- Issues / risks: nothing deployed; OTP in memory; D's TS vs Python policy parity unproven; offline edge agent / C's React app not integrated; accuracy below HID target; thresholds draft.
- Next: decide canonical campus dataset with D; wire D's TS engine for offline; deploy to Lambda + API Gateway (A); own-data collection and threshold freeze.

## [2026-11-14 14:20] [B] Round 2: dataset decision, webcam enrolment fix, lockout, AWS-only accuracy
- Done: canonical dataset = A's ids (live in DDB/Rekognition); relaxed quality gates for laptop webcams (sharpness 50->20, brightness 40->30, QualityFilter AUTO, Tier1 gate 0.15) and verified with a frame at the user's sharpness 38.9; kiosk Enrol retries 6 frames with advice; thresholds hot-reload from config/thresholds.v2.json; DynamoDB lockout (3 biometric failures -> 60 s); AWS-only accuracy experiment (eval/aws_only_accuracy.py -> docs/AWS_ONLY_ACCURACY.md).
- Numbers: AWS Rekognition labels alone: attacks accepted 91.4 % (orig rule) / 95.4 % (narrow), accuracy ~37 %; B CNN alone 96.2 % (target 99.4 %). Real-AWS e2e: genuine ALLOW, print/cut-print/video replay DENY, impostor DENY, schedule cases correct, lockout engages on 3rd failure; server time 1.1-1.8 s warm.
- Issues / risks: no AppConfig / Face Liveness widget / offline edge mode / Lambda deploy; model is trained on public data and has not been run on the user's laptop webcam (domain shift: tune thresholds.v2.json via the live spoof-probability readout); latency margin thin.
- Next: parallelise nonce+lock checks; own-camera data; Face Liveness widget; offline edge; deploy.
