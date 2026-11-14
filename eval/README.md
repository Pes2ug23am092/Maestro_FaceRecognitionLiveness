# Data and Evaluation (Role D)

This slice owns the fusion policy, locked-test metrics, ablation reporting, and data-collection documentation. It uses only Python's standard library.

## Run the focused tests

```powershell
python -m unittest discover -s tests -v
```

## Decision policy contract

`backend.fusion.decide` consumes normalized probabilities and returns `ALLOW`, `STEP_UP`, or `DENY`. The model output is represented by `spoof_prob`, `signals`, and boolean `quality`; identity similarity and optional Tier 2/3 results are supplied by backend integration. Invalid or missing required numeric data raises `ValueError`; the API adapter must turn policy errors into `STEP_UP`, never `ALLOW`.

`policy/src/policy.ts` is the pure TypeScript context-aware policy shared by online and offline adapters. It combines biometric confidence, user validity, group/zone grants, schedule windows, calendar events, risk, second-factor status, signed-bundle validity, and clock drift. The offline branch allows only a valid credential plus strict local liveness in a scheduled low-risk zone; medium risk steps up and high risk denies. The policy receives `signatureValid` only after the caller has verified the bundle signature cryptographically.

The synthetic campus data is in `config/seed_campus.json`; it contains no real campus schedule or people. `config/policy_vectors.json` expands to 82 policy vectors (all 7 groups × 8 zones plus boundary and offline cases). Run from `policy/` with `npm --script-shell=cmd.exe test` on this Windows/Git Bash setup. `npm --script-shell=cmd.exe run vectors` writes `report/policy-vectors.json`; `npm --script-shell=cmd.exe run offline:drill` writes `report/offline-chaos.json`.

Thresholds in `config/thresholds.v1.json` are illustrative, uncalibrated values. They are not claims of measured accuracy and must not control a real access gate until calibrated on validation data and reviewed. Keep thresholds fixed before opening the locked test set.

## Decision-log format

Write one JSON object per line, following [schema.json](schema.json). `sample_id` must be random and opaque, never a name, badge number, or stable participant identifier. Do not include images, embeddings, face crops, free-text notes, or direct identifiers. The four `ablation` outcomes are produced for the same locked-test examples by running each configuration offline. `full_stack` is the final policy outcome. `identity_is_match` can be `null` when the sample is not part of an identity evaluation.

Example shape (placeholder values only):

```json
{"sample_id":"opaque-0001","split":"locked_test","mode":"ONLINE","truth":"genuine","attack_type":null,"identity_is_match":true,"cold_start":false,"latency_ms":1200,"spoof_prob":0.03,"outcome":"ALLOW","online_outcome":"ALLOW","must_deny_offline":false,"sync_success":null,"sync_latency_ms":null,"ablation":{"rekognition_only":"STEP_UP","tier1_only":"ALLOW","tier1_tier2":"ALLOW","full_stack":"ALLOW"}}
```

## Generate reports

After thresholds are calibrated and frozen, update the config with `status: "frozen"`, `locked_test: true`, an ISO timestamp in `frozen_at`, and the reviewed Git commit in `git_commit`. Then run:

```powershell
python -m eval.run_eval path\to\locked-test.jsonl --output report
```

The evaluator input JSONL may combine ONLINE and OFFLINE locked-test records. Online rows include all four ablation outcomes; offline rows include the paired `online_outcome`, `must_deny_offline`, and nullable sync result fields. It creates `metrics.csv`, score-swept `roc_det.csv`, `report.md`, and a self-contained `dashboard.html` with separate online/offline confusion matrices. Offline metrics include APCER/BPCER, STEP_UP/DENY rates, paired changes, fail-open count/rate, and sync success/latency. APCER counts attacks ending in `ALLOW`; BPCER counts genuine samples ending in `DENY`; ACER is their unweighted mean. Rates include two-sided 95% Wilson intervals; a metric with no eligible samples is `n/a`. Latency percentiles use nearest-rank and are split by online/offline and warm/cold.

Add the D-side evidence artifacts to the run with `--policy-results report/policy-vectors.json --offline-drills report/offline-chaos.json`. The report records policy-vector and outage-drill pass counts separately from biometric metrics.

The harness reports point estimates, not model quality guarantees. Preserve the raw de-identified decision log and the exact code/config commit used for a run; never tune against the locked-test output.