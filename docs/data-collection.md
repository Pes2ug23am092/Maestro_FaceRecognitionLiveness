# Data Collection and Split Protocol

Do not collect face data until the consent form is completed with a real contact, retention/deletion date, withdrawal procedure, and any required institutional approval. Participation must be voluntary and unrelated to access, work, grades, or services. Do not collect from minors.

## Collection controls

- Assign a random study code. Keep any code-to-identity key separately encrypted, access-restricted, and delete it on the documented schedule.
- Capture only the minimum face data needed for the approved protocol. Do not record audio, names, badge IDs, or unrelated background details.
- Store raw captures encrypted with restricted access. Keep raw media out of Git, logs, notebooks, issue trackers, and the evaluation report.
- Record condition labels and consent status in a separate access-controlled manifest. Export only opaque IDs, labels, scores, outcomes, and timing to the evaluator.
- Delete raw media and derived embeddings when the approved retention period expires; record deletion without retaining the media.

## Subject-disjoint evaluation

Split participants, not individual frames: every capture from one person belongs to exactly one of train, validation, or locked test. Use train for model fitting, validation for threshold selection, and locked test once after thresholds are frozen. Store split assignments in the restricted manifest; never place participant codes in public artifacts. Ensure every attack type and relevant lighting condition has an adequate sample count before drawing conclusions.

## Test protocol

Record genuine attempts across the approved range of lighting and ordinary appearance conditions. Record presentation-attack outcomes by a predeclared type and condition label; vary props and capture sessions without reusing test samples for threshold tuning. Have the evaluator count unique attempts, not frames, as statistical samples. Document exclusions before looking at outcomes and report their counts/reasons.

The plan gives two attack-count targets: at least 300 attacks overall (constraints table) and 60-100 per attack type (evaluation page). Use the stricter combined target of at least 300 genuine attempts and at least 300 attacks overall; with four planned attack types, aim for at least 75 per type while meeting the 300 total. These are planning minima, not a guarantee of statistical power. Report each denominator and interval. Small samples produce wide intervals; do not describe an unobserved error as a zero population error.