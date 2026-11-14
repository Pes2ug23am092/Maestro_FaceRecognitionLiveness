"""Generate locked-test CSV, Markdown, and HTML evaluation artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval.metrics import load_jsonl, write_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSONL decision log from the locked test split")
    parser.add_argument("--thresholds", type=Path, default=Path("config/thresholds.v1.json"))
    parser.add_argument("--output", type=Path, default=Path("report"))
    parser.add_argument("--policy-results", type=Path, help="JSON output from the TypeScript policy-vector runner")
    parser.add_argument("--offline-drills", type=Path, help="JSON output from the offline chaos-drill runner")
    args = parser.parse_args()

    with args.thresholds.open(encoding="utf-8") as source:
        thresholds = json.load(source)
    if thresholds.get("status") != "frozen" or not thresholds.get("locked_test"):
        parser.error("evaluation requires thresholds.v1.json status='frozen' and locked_test=true")
    if not thresholds.get("frozen_at") or not thresholds.get("git_commit"):
        parser.error("frozen thresholds must record frozen_at and git_commit")

    records = load_jsonl(args.input)
    policy_results = json.loads(args.policy_results.read_text(encoding="utf-8")) if args.policy_results else None
    offline_drills = json.loads(args.offline_drills.read_text(encoding="utf-8")) if args.offline_drills else None
    write_report(records, args.output, policy_results, offline_drills)
    print(f"Evaluated {len(records)} locked-test samples; reports written to {args.output}")


if __name__ == "__main__":
    main()