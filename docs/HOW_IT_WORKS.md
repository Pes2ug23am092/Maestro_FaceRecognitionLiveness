# How the project works

HID hackathon: **Face Liveness Detection and Authentication kiosk**. A person stands at a door, the kiosk decides in about 2 seconds whether to **ALLOW**, ask for **STEP-UP VERIFICATION**, or **DENY**. It checks four things: is this a real live face (not a photo, screen, video or mask), is it the right person for the badge, is that person allowed through this door right now, and has the badge been locked out.

Live demo URL: https://i9as63zk3e.execute-api.us-east-1.amazonaws.com/kiosk (AWS region us-east-1).

---

## 1. The big picture

```
Browser kiosk page (/kiosk)
   captures 6 camera frames (~0.5 s) + fetches a one-time nonce in parallel
        |  HTTPS
        v
API Gateway (HTTP API)  ->  Lambda "hidb-kiosk-backend"  (FastAPI + my liveness model, 2 GB)
        |
        |  in parallel for every scan:
        |    1. DynamoDB: check + burn the one-time nonce          (replay protection)
        |    2. DynamoDB: is this badge locked out?                (3 failures -> 60 s lock)
        |    3. DynamoDB: load the user (group, status, validity)
        |    4. My liveness model on all frames (Tier 1)           ~0.1 s
        |         -> then Rekognition SearchFacesByImage on the best frame   ~0.3 s
        v
   Fusion (D's module)   : liveness + identity + quality  ->  CONFIDENT / LOW / SPOOF / MISMATCH
   Policy engine         : user status, validity dates, calendar, door permission, schedule, door risk
        v
   ALLOW / STEP-UP / DENY  +  reason codes  ->  audit row in DynamoDB (no images, hashed badge)
```

Server time in the cloud: about 0.2 to 0.4 s per decision. Measured from the laptop it is longer because each new connection from this laptop to AWS costs 1.5 to 2 s; the page reuses connections and the server keeps its AWS connections warm.

---

## 2. What each decision step does

### 2.1 One-time nonce (anti-replay)
Before scanning, the page asks `/session` for a random nonce (30 s life). `/decide` burns it with a DynamoDB conditional update, so a recorded request cannot be replayed. A used or expired nonce returns 400.

### 2.2 Tier 1: passive liveness (my part, model + classical cues)
Runs on all 6 frames, no user action needed.
1. **Face detection** (OpenCV YuNet, 5 landmarks) finds the face in each frame and picks the sharpest, brightest frame as the "best frame".
2. **Context crop**: a 2x box around the face, 160x160 px. The surroundings are kept on purpose: a phone bezel, paper edge or hand is a strong spoof clue.
3. **CNN**: MobileNetV3-small (ONNX, 6 MB, ~10 ms) gives five probabilities: live, print, screen photo, video replay, mask. `spoof_prob = 1 - P(live)`. Probabilities are averaged over the best 3 frames.
4. **Classical cues** (no training): moire pattern in the frequency spectrum (screens), glare, flatness (high-frequency energy), long straight borders (phone/paper edges), and non-rigid motion across frames (a held photo moves as one rigid sheet, a live face deforms). They add a small nudge and can veto.
5. **Quality** (sharpness, brightness, face size). If no face is found, the result is "unclear scan, recapture", never "spoof".

### 2.3 Identity: Amazon Rekognition
The best frame goes to `SearchFacesByImage` against the collection `kiosk-face-collection-v1`. The similarity counts only if the matched ExternalImageId equals the **claimed badge** (one face may be enrolled under several badges, so the best match for that badge is used). The collection stores face vectors, never photos. If the call is slow (more than 0.9 s) a second identical call is sent and the first good answer wins.

### 2.4 Fusion (D's `backend/fusion.py`, thresholds in `config/thresholds.v2.json`)
Combines `spoof_prob`, quality, and similarity using thresholds chosen by the **door's risk level** (low -> "lobby", medium -> "office", high -> "server_room"):

| Level | live allow | live deny | similarity allow | similarity deny |
|---|---|---|---|---|
| lobby | 0.75 | 0.50 | 0.90 | 0.70 |
| office | 0.80 | 0.50 | 0.92 | 0.75 |
| server_room | 0.85 | 0.50 | 0.95 | 0.80 |

(liveness = 1 - spoof_prob). Result: **SPOOF** (hard veto or liveness too low), **MISMATCH** (identity too low), **LOW** (in between, or poor quality), **CONFIDENT** (both clear). Any strong spoof signal beats everything else.

### 2.5 Policy engine (`backend/policy_engine.py`, data from `config/seed_campus.json` and DynamoDB)
"Most restrictive wins". Checked in order: spoof -> unknown badge -> identity mismatch -> revoked or expired badge -> calendar block -> door not authorised for the group -> schedule window (days and hours) -> door risk rules.

- Outside the allowed hours: **STEP-UP** (approval code) if the scan was confident, **DENY** if it was unclear.
- Unclear scan inside hours: **STEP-UP** (recapture, challenge or code).
- High-risk doors (security tier 3: Server Room, Robotics Lab) always need a second factor: **STEP-UP** even for an authorised person.
- Door not in the person's group schedule: **DENY**. Revoked badge: **DENY**. Audit day (20 Nov 2026): only sysadmin and security.

The full table of who can enter where and when is in `config/seed_campus.json` (31 schedule rows, 7 groups, 8 doors, 8 demo users).

### 2.6 Lockout
Biometric failures (spoof, mismatch, unclear scan, failed challenge) are counted per badge in DynamoDB. The 3rd failure locks the badge for **60 seconds** (`ATTEMPTS_EXCEEDED`); a successful ALLOW clears the counter. Policy denials (wrong door, holiday) do not count.

### 2.7 Audit
Every decision is written to `kiosk_decisions`: outcome, reason codes, liveness, similarity, timings, door, a hash of the badge, a hash of the frames. No images, no names. On Lambda this write is synchronous; on a long-running server it is in the background.

---

## 3. Step-up verification (the middle outcome)

When the result is STEP-UP the page offers three ways forward:
1. **Recapture**: scan again (counts toward the lockout if it fails).
2. **Tier 2 liveness challenge**: the server issues a random sequence of 6 screen colours (red/green/blue, no immediate repeats) and a random head-turn (left or right). The kiosk flashes the colours full-screen while capturing timestamped frames. The server checks (a) the face's colour change follows the issued sequence better than random sequences (a permutation test, so it does not depend on how strong the reflection is), and (b) the head actually turned the right way. A pre-recorded video cannot know the random sequence; a photo cannot turn. Pass -> fusion is re-run with "Tier 2 passed". The challenge is single use and expires.
3. **OTP**: a 6-digit code stored hashed in DynamoDB, 5-minute life, 3 wrong tries lock it. In the demo the code is shown on screen; in production it would go out through SES or SNS.

AWS Face Liveness (Tier 3) has a backend route but is **not wired into the page**.

---

## 4. The model and data

- **Training data** (public, research use): CASIA-FASD (30 people: live, printed photo, cut-out photo, tablet video replay; split by person 18 train / 6 validation / 6 test) and 3,000 CelebA-Spoof images (live or spoof only).
- **Training**: fine-tuned MobileNetV3-small, 12 epochs, about 4 minutes on the laptop GPU. A "spoof of unknown type" label is handled by a loss that only requires the model to say "not live".
- **Why a custom model**: AWS Rekognition label checks alone let through 91 to 95 % of attacks (measured), so liveness needs its own signal.
- **What it has not seen**: screen-photo attacks, masks, and your laptop camera. That is the main accuracy risk.

---

## 5. What was measured

| Check | Result |
|---|---|
| Model, frame level, public held-out test (1,329 images) | **96.2 %** accuracy (target 99.4 %). Attacks accepted 3.0 %, live rejected 5.2 %. |
| AWS Rekognition labels alone, same images | attacks accepted 91 to 95 %, accuracy about 37 % |
| End to end on real AWS (11 cases) | genuine allowed; print, cut-out, video replay denied; impostor denied; wrong door, out of hours, audit day and lockout all correct |
| Decision time on the deployed Lambda | server 0.18 to 0.39 s; cold start about 6 s; laptop-hosted version median 0.95 s |
| Tests | 23 automated tests pass (policy engine, fusion, live AWS harness) |

Limits to state honestly: below the 99.4 % target; small samples (wide confidence intervals); CASIA test split reused for diagnostics so it is not a clean final test; no data from your own camera, screens or masks yet; thresholds are draft; Tier 2 is tested on simulated reflections and real face frames, not on a real screen-lit face; AWS Face Liveness is not measured.

---

## 6. What runs in AWS

Lambda `hidb-kiosk-backend` (code 6 MB; libraries in S3 `hidb/deps.zip`, loaded into /tmp at cold start), API Gateway HTTP API, EventBridge keep-warm rule (every 5 minutes), CloudWatch logs and dashboard `hidb-kiosk`, X-Ray tracing, IAM role `hidb-kiosk-lambda-role`, plus resources A created: 7 DynamoDB tables `kiosk_*`, Rekognition collection, KMS signing key, S3 evidence bucket. The API is public with no login; restrict or delete it after the demo.

## 7. Not built yet

Offline fail-safe mode in the browser (the policy engine has the offline rules and A built the signed policy bundle, credential and sync endpoints, but no browser edge agent calls them); AWS Face Liveness in the page; AppConfig, Cognito/WAF, Step Functions, Verified Permissions; D's TypeScript policy engine is not wired in (the online path uses the Python engine); OTP delivery by email/SMS.

---

## 8. Where things are

| Path (repo `c:\PESU\remote\repo`) | What |
|---|---|
| `backend/decide_service.py` | the /decide pipeline described above |
| `backend/liveness_service.py`, `models/liveness/` | Tier 1 model, cues, Tier 2 challenge |
| `backend/fusion.py`, `config/thresholds.v2.json` | fusion and thresholds |
| `backend/policy_engine.py`, `backend/campus_adapter.py` | schedule/door policy on A's data |
| `backend/lockout_service.py`, `otp_service.py`, `tier2_service.py` | lockout, OTP, challenge |
| `backend/kiosk.html` | the website, with the demo scenarios |
| `config/seed_campus.json`, `infra/seed_extra.py` | campus data and how to load it |
| `infra/deploy_zip.py`, `infra/dashboard.py`, `lambda_boot.py` | deployment |
| `tests/`, `docs/INTEGRATION.md`, `docs/AWS_ONLY_ACCURACY.md`, `memory.md` | tests, notes, results |
