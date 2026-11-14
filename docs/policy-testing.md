# Role D: Context Policy and Offline Evaluation

## Scope

The TypeScript policy is a pure decision function shared by the online adapter and the browser-side offline adapter. It returns `ALLOW`, `STEP_UP`, or `DENY` with reason codes from [../config/reasons.json](../config/reasons.json). Synthetic campus configuration is in [../config/seed_campus.json](../config/seed_campus.json); it is demo data, not a real campus schedule or roster.

The offline policy deliberately does not perform face identity matching. It requires a verified short-lived credential/TOTP and stricter local liveness, denies medium/high-risk access (medium returns `STEP_UP`; high always denies), and never permits uncertain liveness. The adapter must verify the bundle signature before setting `signatureValid: true`, derive `trustedTime` from its protected clock source, and translate exceptions into a deny. The policy does not itself implement cryptographic signature verification, OTP delivery, storage encryption, or kiosk device trust.

## Policy vectors

`config/policy_vectors.json` combines 56 group/zone matrix cases with 26 named schedule/calendar/biometric/offline cases. `policy/test/policy.test.ts` asserts all 82 outcomes and reason codes. The seed contains 8 zones (3 low, 3 medium, 2 high), 7 groups, 25 schedule rows, 4 calendar events, and 12 synthetic users.

From the `policy/` directory:

```powershell
npm --script-shell=cmd.exe test
npm --script-shell=cmd.exe run vectors
npm --script-shell=cmd.exe run offline:drill
```

The first command builds with strict TypeScript and runs the shared vectors. The other commands write `report/policy-vectors.json` and `report/offline-chaos.json`. The offline drills cover short, long, and flapping outages; mid-decision network loss; high-risk denial; invalid signature; expired bundle; clock drift; uncertain liveness; and audit-chain tampering, loss, and duplicate-event detection.

## Offline privacy scan

Export the kiosk's offline IndexedDB contents and the exact sync payload to JSON without adding them to Git. Scan both exports before accepting an offline demo run:

```powershell
python -m eval.scan_no_biometrics path\to\indexeddb-export.json path\to\sync-payload.json
```

The scanner rejects biometric/media keys, embedded image/video data URIs, large base64-like payloads, and media file references. It allows minimal audit fields such as opaque event IDs, salted user references, reason codes, mode, and a numeric liveness score. This is a structural privacy guard, not a substitute for reviewing the actual export format.

## Evaluation report

The report accepts merged, subject-disjoint locked-test ONLINE/OFFLINE records matching [../eval/schema.json](../eval/schema.json), plus the policy and offline-drill JSON artifacts above. It reports policy-vector pass rate, offline drill pass rate, offline APCER/BPCER, STEP_UP/DENY rates, paired changes against online outcomes, fail-open count/rate, and sync outcomes/latencies. No live campus data, participant records, or performance claims are committed here.