# Offline fail-safe mode: design and current status

HID surprise challenge, principle 02: *if the kiosk loses its connection to the central server it must never fail open; it switches to a safe local decision flow with offline fallback verification, caches only the minimum temporary data needed for auditing (no raw biometrics), and automatically syncs all offline decisions once connectivity returns.*

**Status in one line:** the server side (signed policy bundle, signed credential, idempotent sync, revocation alert), the offline decision rules and the fail-closed behaviour are built and tested. The browser edge agent that ties them together on the kiosk is **not built**, so offline mode cannot yet be demonstrated end to end.

---

## 1. Principles

1. **Offline can only be stricter than online, never looser.** Every offline outcome is the same as or more restrictive than the online outcome for the same person, door and time. A test enforces this across all users, doors, times and scan results.
2. **Never fail open.** Any error, unreadable bundle or unknown state is a DENY.
3. **No raw biometrics are cached.** No face images and no face vectors are stored on the kiosk. Offline identity therefore cannot be a face match against a local database (see section 3).
4. **Audit data is minimal and tamper-evident**: event ID, time, door, hashed badge reference, outcome and reason codes only.

## 2. When the kiosk goes offline

The kiosk page health-checks the server every few seconds and also treats a failed or timed-out decision request as a lost connection. After repeated failures it shows an **OFFLINE** banner and uses the local flow. It returns to online only after several consecutive good checks, so a flapping network does not bounce between modes.

## 3. The local decision flow

1. **Local liveness check.** The same liveness model that runs in the Lambda runs in the browser (ONNX model, `models/web/liveness_web.js`), with a stricter threshold than online. A spoof or an uncertain scan is denied.
2. **Offline identity anchor (no cached faces).** The person presents a short-lived **signed credential** issued while online (`/credential`, signed with the KMS key and carrying the person's door grants), or an offline one-time code. Live face plus a valid credential shows that the person holding the credential is physically present. This is weaker than an online face match, which is why it is limited to low-risk doors (section 4).
3. **Policy from the cached signed bundle.** The kiosk keeps the latest **policy bundle** (`/policy-bundle`): schedules, door risk levels, calendar, and a **revocation list**, signed with an ECDSA P-256 key held in KMS and verified in the browser with WebCrypto. The same policy rules used online are applied.
4. **Bundle safety checks.** The bundle is rejected if its signature is invalid, it has expired, or the kiosk clock disagrees with trusted time by more than the allowed drift. A rejected bundle means DENY on every door.

## 4. Offline outcomes by door risk

| Door risk | Examples | Offline result (confident live scan, valid credential, inside schedule) |
|---|---|---|
| Low | Main Entrance, North Gate, Cafeteria | **ALLOW** (logged as offline) |
| Medium | Office A, Office B, Executive Suite | **STEP-UP** (approval or one-time code required) |
| High | Server Room, Robotics Lab | **DENY always** (never admitted offline) |

Other offline rules: an unclear scan is DENY; outside the schedule a confident scan gets STEP-UP and an unclear one gets DENY; a revoked or expired user is DENY; no schedule row for the door is DENY; an expired or invalid bundle is DENY everywhere.

## 5. What is stored while offline

Only an append-only audit queue: `{event_id, sequence, time, kiosk, door, hashed badge, outcome, reason codes, liveness score, mode=OFFLINE, previous-entry hash}`. Each entry carries the hash of the previous one, so a deleted or edited entry breaks the chain. No frames, no embeddings, no names. The queue is encrypted at rest and cleared only after the server acknowledges it.

## 6. Automatic sync on reconnect

When the connection returns, the queue is sent in order to `POST /sync`. The server:
- ignores duplicates (conditional write keyed on `event_id`), so retries never double-count;
- checks the batch and writes each event to the decisions table with `mode = offline`;
- **raises an alert (`OFFLINE_ALLOW_REVOKED`)** if someone who was revoked during the outage was admitted offline, so security can review it;
- returns an acknowledgement, after which the kiosk clears those events and fetches a fresh bundle and revocation list.

## 7. Known trade-offs

- **Revocation lag:** a person revoked during the outage can still pass a low-risk door until the bundle expires. Mitigations: short bundle and credential lifetimes, high-risk doors never offline, and the post-sync alert.
- **Weaker identity offline:** a credential plus a live face is not a face match. This is deliberate, because the brief forbids caching raw biometrics.
- **Trusted time:** a kiosk without server contact depends on its own clock, hence the drift check.

## 8. What exists today

| Piece | State |
|---|---|
| Fail-closed behaviour (any error means DENY) | **Built and tested** |
| Offline rules in the policy engine (door-risk table above, expired bundle, offline never looser than online) | **Built and tested** (`backend/policy_engine.py`) |
| Signed policy bundle with revocation list (`/policy-bundle`, KMS ECDSA, WebCrypto-compatible signature) | **Built and tested** (A, live AWS) |
| Signed short-lived credential (`/credential`) | **Built and tested** (A, live AWS) |
| Idempotent sync with duplicate prevention and revoked-user alert (`/sync`) | **Built and tested** (A, live AWS) |
| Hash-chained audit log, no-biometrics scanner, 11 outage drills | **Built in D's branch** (TypeScript), not integrated |
| Browser liveness model (`liveness_web.js`) with test vectors | **Written, never run in a browser** |
| Connectivity detection, offline banner, "Disconnect" demo switch | **Not built** |
| Browser caching and signature check of the bundle | **Not built** |
| Local decision flow using the cached bundle, credential or offline code | **Not built** |
| Local encrypted audit queue and automatic sync on reconnect | **Not built** |
| `/decide` running the offline rules (it always runs online today) | **Not built** |

## 9. Planned demo (once the edge agent is built)

1. Scan a genuine user at a low-risk door online: ALLOW.
2. Press **Disconnect**: the OFFLINE banner appears.
3. Genuine user at a low-risk door: ALLOW, marked offline. Printed photo: DENY. Medium-risk door: STEP-UP. Server Room: DENY.
4. Show the audit queue count and that it contains no biometric data.
5. Press **Reconnect**: the queue drains to `/sync`, and the decisions table shows the offline entries with their reason codes.
6. Tampered or expired bundle: DENY.
