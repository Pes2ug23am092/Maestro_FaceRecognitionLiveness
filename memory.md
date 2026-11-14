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
