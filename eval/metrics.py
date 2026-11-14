"""Metrics and report generation for locked-test decision logs."""

from __future__ import annotations

import csv
import html
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


OUTCOMES = {"ALLOW", "STEP_UP", "DENY"}
TRUTHS = {"genuine", "attack"}
ABLATIONS = ("rekognition_only", "tier1_only", "tier1_tier2", "full_stack")
Z_95 = 1.959963984540054


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"line {line_number}: invalid JSON: {error.msg}") from error
            if not isinstance(record, dict):
                raise ValueError(f"line {line_number}: each record must be a JSON object")
            records.append(record)
    validate_records(records)
    return records


def validate_records(records: list[dict[str, Any]]) -> None:
    if not records:
        raise ValueError("no records found")
    seen_ids: set[str] = set()
    for index, record in enumerate(records, start=1):
        prefix = f"record {index}"
        sample_id = record.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id.strip():
            raise ValueError(f"{prefix}: sample_id must be a non-empty opaque string")
        if sample_id in seen_ids:
            raise ValueError(f"{prefix}: duplicate sample_id {sample_id!r}")
        seen_ids.add(sample_id)
        if record.get("split") != "locked_test":
            raise ValueError(f"{prefix}: only split='locked_test' may be evaluated")
        mode = record.get("mode")
        if mode not in {"ONLINE", "OFFLINE"}:
            raise ValueError(f"{prefix}: mode must be ONLINE or OFFLINE")
        if record.get("truth") not in TRUTHS:
            raise ValueError(f"{prefix}: truth must be 'genuine' or 'attack'")
        if record["truth"] == "attack" and not isinstance(record.get("attack_type"), str):
            raise ValueError(f"{prefix}: attack records require attack_type")
        if record["truth"] == "genuine" and record.get("attack_type") not in (None, ""):
            raise ValueError(f"{prefix}: genuine records must not have attack_type")
        if record.get("outcome") not in OUTCOMES or record.get("online_outcome") not in OUTCOMES:
            raise ValueError(f"{prefix}: outcome and online_outcome must be ALLOW, STEP_UP, or DENY")
        if mode == "ONLINE" and record["outcome"] != record["online_outcome"]:
            raise ValueError(f"{prefix}: online outcome must match online_outcome")
        if not isinstance(record.get("cold_start"), bool):
            raise ValueError(f"{prefix}: cold_start must be a boolean")
        latency = record.get("latency_ms")
        if isinstance(latency, bool) or not isinstance(latency, (int, float)):
            raise ValueError(f"{prefix}: latency_ms must be a non-negative number")
        if not math.isfinite(float(latency)) or latency < 0:
            raise ValueError(f"{prefix}: latency_ms must be a finite non-negative number")
        score = record.get("spoof_prob")
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)) or not 0 <= score <= 1:
            raise ValueError(f"{prefix}: spoof_prob must be a finite number in [0, 1]")
        identity_is_match = record.get("identity_is_match")
        if identity_is_match is not None and not isinstance(identity_is_match, bool):
            raise ValueError(f"{prefix}: identity_is_match must be boolean or null")
        ablation = record.get("ablation")
        if mode == "ONLINE":
            if not isinstance(ablation, dict) or set(ablation) != set(ABLATIONS):
                raise ValueError(f"{prefix}: online ablation must contain all four decision paths")
            if any(path_outcome not in OUTCOMES for path_outcome in ablation.values()):
                raise ValueError(f"{prefix}: ablation outcomes must be ALLOW, STEP_UP, or DENY")
            if ablation["full_stack"] != record["outcome"]:
                raise ValueError(f"{prefix}: full_stack ablation must match outcome")
        elif ablation is not None:
            raise ValueError(f"{prefix}: offline records must not include online ablation outcomes")
        if not isinstance(record.get("must_deny_offline"), bool):
            raise ValueError(f"{prefix}: must_deny_offline must be a boolean")
        sync_success = record.get("sync_success")
        if sync_success is not None and not isinstance(sync_success, bool):
            raise ValueError(f"{prefix}: sync_success must be boolean or null")
        sync_latency = record.get("sync_latency_ms")
        if sync_latency is not None and (
            isinstance(sync_latency, bool) or not isinstance(sync_latency, (int, float))
            or not math.isfinite(float(sync_latency)) or sync_latency < 0
        ):
            raise ValueError(f"{prefix}: sync_latency_ms must be a finite non-negative number or null")
        if mode == "ONLINE" and (sync_success is not None or sync_latency is not None):
            raise ValueError(f"{prefix}: online records must not contain sync results")


def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float] | None:
    if total == 0:
        return None
    if not 0 <= successes <= total:
        raise ValueError("successes must be between zero and total")
    proportion = successes / total
    denominator = 1.0 + z * z / total
    center = (proportion + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt(proportion * (1.0 - proportion) / total + z * z / (4.0 * total * total)) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def percentile(values: list[float], percentile_value: float) -> float | None:
    if not values:
        return None
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile_value must be in [0, 100]")
    ordered = sorted(values)
    rank = max(0, math.ceil(percentile_value / 100.0 * len(ordered)) - 1)
    return ordered[rank]


def build_report(
    records: list[dict[str, Any]],
    policy_results: dict[str, Any] | None = None,
    offline_drills: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str, str]:
    validate_records(records)
    _validate_evidence("policy results", policy_results)
    _validate_evidence("offline drill results", offline_drills)
    metric_rows: list[dict[str, Any]] = []
    online_records = [row for row in records if row["mode"] == "ONLINE"]
    offline_records = [row for row in records if row["mode"] == "OFFLINE"]
    attack_records = [row for row in online_records if row["truth"] == "attack"]
    genuine_records = [row for row in online_records if row["truth"] == "genuine"]

    for path in ABLATIONS:
        outcomes = [row["ablation"][path] for row in online_records]
        group = "overall" if path == "full_stack" else path
        attack_accepts = sum(
            row["ablation"][path] == "ALLOW" for row in attack_records
        )
        genuine_denials = sum(
            row["ablation"][path] == "DENY" for row in genuine_records
        )
        _add_rate(metric_rows, "APCER", group, attack_accepts, len(attack_records))
        _add_rate(metric_rows, "BPCER", group, genuine_denials, len(genuine_records))
        _add_rate(metric_rows, "attack_detection_rate", group, sum(
            row["ablation"][path] == "DENY" for row in attack_records
        ), len(attack_records))
        if attack_records and genuine_records:
            apcer = attack_accepts / len(attack_records)
            bpcer = genuine_denials / len(genuine_records)
            metric_rows.append(_metric_row("ACER", group, (apcer + bpcer) / 2.0,
                                           None, len(attack_records) + len(genuine_records), None))
        _add_rate(metric_rows, "STEP_UP_rate", group, outcomes.count("STEP_UP"), len(outcomes))
        _add_rate(metric_rows, "genuine_allow_rate", group, sum(
            row["ablation"][path] == "ALLOW" for row in genuine_records
        ), len(genuine_records))

    attack_types = sorted({row["attack_type"] for row in attack_records})
    for attack_type in attack_types:
        subset = [row for row in attack_records if row["attack_type"] == attack_type]
        accepted = sum(row["ablation"]["full_stack"] == "ALLOW" for row in subset)
        _add_rate(metric_rows, "APCER", f"attack:{attack_type}", accepted, len(subset))

    for mode_name, subset in (("offline", offline_records), ("online", online_records)):
        attacks = [row for row in subset if row["truth"] == "attack"]
        genuine = [row for row in subset if row["truth"] == "genuine"]
        outcomes = [row["outcome"] for row in subset]
        _add_rate(metric_rows, f"{mode_name}_APCER", mode_name,
                  sum(row["outcome"] == "ALLOW" for row in attacks), len(attacks))
        _add_rate(metric_rows, f"{mode_name}_BPCER", mode_name,
                  sum(row["outcome"] == "DENY" for row in genuine), len(genuine))
        _add_rate(metric_rows, f"{mode_name}_STEP_UP_rate", mode_name,
                  outcomes.count("STEP_UP"), len(outcomes))
        _add_rate(metric_rows, f"{mode_name}_DENY_rate", mode_name,
                  outcomes.count("DENY"), len(outcomes))

    paired_changes = sum(row["outcome"] != row["online_outcome"] for row in offline_records)
    _add_rate(metric_rows, "offline_outcome_changed_vs_online", "offline", paired_changes, len(offline_records))
    fail_open = sum(row["must_deny_offline"] and row["outcome"] == "ALLOW" for row in offline_records)
    _add_rate(metric_rows, "offline_fail_open_rate", "offline", fail_open, len(offline_records))
    sync_records = [row for row in offline_records if row["sync_success"] is not None]
    _add_rate(metric_rows, "sync_success_rate", "offline",
              sum(row["sync_success"] is True for row in sync_records), len(sync_records))
    sync_times = [float(row["sync_latency_ms"]) for row in offline_records if row["sync_latency_ms"] is not None]
    for p in (50, 95):
        metric_rows.append(_metric_row(f"sync_latency_p{p}_ms", "offline", percentile(sync_times, p), None, len(sync_times), None))

    if policy_results is not None:
        _add_evidence_rows(metric_rows, "policy_vector", policy_results)
    if offline_drills is not None:
        _add_evidence_rows(metric_rows, "offline_drill", offline_drills)
        metric_rows.append(_metric_row("offline_drill_fail_open_count", "offline_drill",
                                       offline_drills.get("failOpen", 0), None,
                                       offline_drills["total"], None))

    impostors = [row for row in online_records if row.get("identity_is_match") is False]
    matches = [row for row in online_records if row.get("identity_is_match") is True]
    _add_rate(metric_rows, "identity_FAR", "overall", sum(
        row["ablation"]["full_stack"] == "ALLOW" for row in impostors
    ), len(impostors))
    _add_rate(metric_rows, "identity_FRR", "overall", sum(
        row["ablation"]["full_stack"] != "ALLOW" for row in matches
    ), len(matches))

    for mode_name in ("ONLINE", "OFFLINE"):
        for start_state in (False, True):
            label = f"{mode_name.lower()}_{'cold' if start_state else 'warm'}"
            latencies = [float(row["latency_ms"]) for row in records
                         if row["mode"] == mode_name and row["cold_start"] is start_state]
            for p in (50, 95, 99):
                metric_rows.append(_metric_row(f"latency_p{p}_ms", label, percentile(latencies, p), None, len(latencies), None))

    matrix: dict[str, dict[str, dict[str, int]]] = {
        mode_name: {
            truth: dict(Counter(
                row["outcome"] for row in records
                if row["mode"] == mode_name.upper() and row["truth"] == truth
            ))
            for truth in ("genuine", "attack")
        }
        for mode_name in ("online", "offline")
    }
    report = _markdown_report(records, metric_rows, matrix, policy_results, offline_drills)
    dashboard = _html_dashboard(records, metric_rows, matrix, roc_det_points(online_records),
                                policy_results, offline_drills)
    return metric_rows, report, dashboard


def write_report(
    records: list[dict[str, Any]],
    output_dir: str | Path,
    policy_results: dict[str, Any] | None = None,
    offline_drills: dict[str, Any] | None = None,
) -> None:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rows, report, dashboard = build_report(records, policy_results, offline_drills)
    with (output / "metrics.csv").open("w", newline="", encoding="utf-8") as target:
        fields = ("metric", "group", "numerator", "denominator", "estimate", "ci_low", "ci_high")
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    curve_fields = ("threshold", "false_positive_rate", "true_positive_rate", "false_negative_rate")
    with (output / "roc_det.csv").open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=curve_fields)
        writer.writeheader()
        writer.writerows(roc_det_points(records))
    (output / "report.md").write_text(report, encoding="utf-8")
    (output / "dashboard.html").write_text(dashboard, encoding="utf-8")


def roc_det_points(records: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Sweep spoof_prob (higher means more attack-like) for ROC/DET plotting."""
    attacks = [row for row in records if row["truth"] == "attack"]
    genuine = [row for row in records if row["truth"] == "genuine"]
    if not attacks or not genuine:
        return []

    grouped: dict[float, list[str]] = defaultdict(list)
    for row in records:
        grouped[float(row["spoof_prob"])].append(row["truth"])

    true_positives = 0
    false_positives = 0
    points = [{
        "threshold": math.nextafter(max(grouped), math.inf),
        "false_positive_rate": 0.0,
        "true_positive_rate": 0.0,
        "false_negative_rate": 1.0,
    }]
    for score in sorted(grouped, reverse=True):
        labels = grouped[score]
        true_positives += labels.count("attack")
        false_positives += labels.count("genuine")
        points.append({
            "threshold": score,
            "false_positive_rate": false_positives / len(genuine),
            "true_positive_rate": true_positives / len(attacks),
            "false_negative_rate": 1.0 - true_positives / len(attacks),
        })
    if min(grouped) > 0:
        points.append({
            "threshold": 0.0,
            "false_positive_rate": 1.0,
            "true_positive_rate": 1.0,
            "false_negative_rate": 0.0,
        })
    return points


def _add_rate(rows: list[dict[str, Any]], metric: str, group: str, numerator: int, denominator: int) -> None:
    interval = wilson_interval(numerator, denominator)
    rows.append(_metric_row(metric, group, numerator / denominator if denominator else None,
                            numerator, denominator, interval))


def _metric_row(metric: str, group: str, estimate: float | None, numerator: int | None,
                denominator: int, interval: tuple[float, float] | None) -> dict[str, Any]:
    return {
        "metric": metric,
        "group": group,
        "numerator": numerator,
        "denominator": denominator,
        "estimate": estimate,
        "ci_low": interval[0] if interval else None,
        "ci_high": interval[1] if interval else None,
    }


def _fmt(value: Any) -> str:
    return "n/a" if value is None else f"{value:.4f}" if isinstance(value, float) else str(value)


def _validate_evidence(name: str, evidence: dict[str, Any] | None) -> None:
    if evidence is None:
        return
    total = evidence.get("total")
    passed = evidence.get("passed")
    if (not isinstance(total, int) or isinstance(total, bool) or total < 0
            or not isinstance(passed, int) or isinstance(passed, bool) or not 0 <= passed <= total):
        raise ValueError(f"{name} must contain valid non-negative total and passed counts")
    fail_open = evidence.get("failOpen", 0)
    if not isinstance(fail_open, int) or isinstance(fail_open, bool) or not 0 <= fail_open <= total:
        raise ValueError(f"{name} failOpen must be a non-negative integer")


def _add_evidence_rows(rows: list[dict[str, Any]], prefix: str, evidence: dict[str, Any]) -> None:
    total = evidence["total"]
    passed = evidence["passed"]
    rows.append(_metric_row(f"{prefix}_pass_rate", prefix, passed / total if total else None,
                            passed, total, None))
    rows.append(_metric_row(f"{prefix}_failure_count", prefix, total - passed, None, total, None))


def _markdown_report(records: list[dict[str, Any]], rows: list[dict[str, Any]],
                     matrix: dict[str, dict[str, dict[str, int]]],
                     policy_results: dict[str, Any] | None,
                     offline_drills: dict[str, Any] | None) -> str:
    lines = [
        "# Locked-Test Evaluation",
        "",
        f"Samples: {len(records)} ({sum(row['truth'] == 'genuine' for row in records)} genuine, "
        f"{sum(row['truth'] == 'attack' for row in records)} attacks)",
        "",
        "All APCER/BPCER and identity-rate intervals are two-sided 95% Wilson intervals. "
        "A missing denominator is reported as n/a, not zero.",
        "",
        "## Metrics",
        "",
        "| Metric | Group | Estimate | Numerator / n | 95% CI |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for row in rows:
        ci = "n/a" if row["ci_low"] is None else f"[{row['ci_low']:.4f}, {row['ci_high']:.4f}]"
        numerator = "n/a" if row["numerator"] is None else f"{row['numerator']} / {row['denominator']}"
        lines.append(f"| {row['metric']} | {row['group']} | {_fmt(row['estimate'])} | {numerator} | {ci} |")
    lines.extend(["", "## Online and Offline Confusion Matrices", ""])
    for mode_name in ("online", "offline"):
        lines.extend([f"### {mode_name.title()}", "", "| Truth | ALLOW | STEP_UP | DENY |", "| --- | ---: | ---: | ---: |"])
        for truth in ("genuine", "attack"):
            counts = matrix[mode_name][truth]
            lines.append(f"| {truth} | {counts.get('ALLOW', 0)} | {counts.get('STEP_UP', 0)} | {counts.get('DENY', 0)} |")
        lines.append("")
        lines.extend(["## Policy and Offline Results", "",
                                    f"Policy vectors: {_evidence_text(policy_results)}.",
                                    f"Offline chaos drills: {_evidence_text(offline_drills)}.",
                                    "Offline APCER/BPCER, STEP_UP/DENY rates, paired outcome changes, fail-open count/rate, and sync success/latency are included in the metrics table. "
                                    "An offline ALLOW on a record marked must_deny_offline is counted as fail-open. "
                                    "STEP_UP is neither an accepted attack nor a denied genuine sample. "
                                    "APCER counts attack samples ending in ALLOW; BPCER counts genuine samples ending in DENY. "
                                    "ROC/DET is calculated from ONLINE spoof scores and written to roc_det.csv.", ""])
    return "\n".join(lines)


def _html_dashboard(records: list[dict[str, Any]], rows: list[dict[str, Any]],
                                        matrix: dict[str, dict[str, dict[str, int]]], curve: list[dict[str, float]],
                                        policy_results: dict[str, Any] | None,
                                        offline_drills: dict[str, Any] | None) -> str:
    metric_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{} / {}</td><td>{}</td></tr>".format(
            html.escape(str(row["metric"])), html.escape(str(row["group"])), _fmt(row["estimate"]),
            _fmt(row["numerator"]), _fmt(row["denominator"]),
            "n/a" if row["ci_low"] is None else f"[{row['ci_low']:.4f}, {row['ci_high']:.4f}]",
        ) for row in rows
    )
    matrix_tables = "".join(
        f"<h3>{mode_name.title()}</h3><table><thead><tr><th>Truth</th><th>ALLOW</th><th>STEP_UP</th><th>DENY</th></tr></thead><tbody>" + "".join(
            f"<tr><th>{truth.title()}</th><td>{matrix[mode_name][truth].get('ALLOW', 0)}</td>"
            f"<td>{matrix[mode_name][truth].get('STEP_UP', 0)}</td><td>{matrix[mode_name][truth].get('DENY', 0)}</td></tr>"
            for truth in ("genuine", "attack")
        ) + "</tbody></table>"
        for mode_name in ("online", "offline")
    )
    roc_svg = _curve_svg(curve, "true_positive_rate", "ROC", diagonal=True)
    det_svg = _curve_svg(curve, "false_negative_rate", "DET", diagonal=False)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Locked-Test Evaluation</title><style>
body{{font:16px/1.5 system-ui,sans-serif;color:#172b3a;background:#f4f7f9;margin:0;padding:2rem}}
main{{max-width:1100px;margin:auto}}h1{{border-bottom:5px solid #129b95;padding-bottom:.5rem}}
table{{border-collapse:collapse;width:100%;margin:1rem 0 2rem;background:white}}th,td{{padding:.55rem .7rem;text-align:left;border-bottom:1px solid #d5dde4}}
th{{background:#163451;color:white}}tr:nth-child(even){{background:#edf2f6}}.note{{color:#465766}}
</style></head><body><main><h1>Locked-Test Evaluation</h1>
<p>{len(records)} samples | {sum(row['truth'] == 'genuine' for row in records)} genuine | {sum(row['truth'] == 'attack' for row in records)} attacks</p>
<h2>Metrics</h2><table><thead><tr><th>Metric</th><th>Group</th><th>Estimate</th><th>Numerator / n</th><th>95% CI</th></tr></thead><tbody>{metric_rows}</tbody></table>
<h2>Online and Offline Confusion Matrices</h2>{matrix_tables}
<h2>Policy and Offline Results</h2><p class="note">Includes offline APCER/BPCER, STEP_UP/DENY rates, fail-open rate, and sync statistics in the metrics table.</p>
<p>Policy vectors: {html.escape(_evidence_text(policy_results))}. Offline chaos drills: {html.escape(_evidence_text(offline_drills))}.</p>
<h2>Online Tier 1 Spoof-Score Curves</h2><p class="note">Threshold sweep uses spoof_prob, with higher scores indicating attack-like samples.</p>
<section class="curves">{roc_svg}{det_svg}</section>
<p class="note">STEP_UP is reported separately. APCER counts attacks ending in ALLOW; BPCER counts genuine samples ending in DENY. Rate intervals use the Wilson method.</p>
</main></body></html>"""


def _evidence_text(evidence: dict[str, Any] | None) -> str:
    if evidence is None:
        return "not supplied"
    fail_open = f", fail-open={evidence.get('failOpen', 0)}" if "failOpen" in evidence else ""
    return f"{evidence['passed']}/{evidence['total']} passed{fail_open}"


def _curve_svg(points: list[dict[str, float]], y_key: str, title: str, *, diagonal: bool) -> str:
    if not points:
        return f"<p>{title} unavailable: both genuine and attack samples are required.</p>"
    left, top, width, height = 35, 18, 340, 220
    plotted = " ".join(
        f"{left + point['false_positive_rate'] * width:.1f},{top + (1 - point[y_key]) * height:.1f}"
        for point in points
    )
    baseline = f'<line x1="{left}" y1="{top + height}" x2="{left + width}" y2="{top}" stroke="#9aa8b4" stroke-dasharray="5 5" />' if diagonal else ""
    return (
        f'<svg role="img" aria-label="{title} curve" viewBox="0 0 400 275" class="curve">'
        f'<text x="{left}" y="14">{title}</text><line x1="{left}" y1="{top}" x2="{left}" y2="{top + height}" stroke="#526577" />'
        f'<line x1="{left}" y1="{top + height}" x2="{left + width}" y2="{top + height}" stroke="#526577" />{baseline}'
        f'<polyline points="{plotted}" fill="none" stroke="#129b95" stroke-width="3" />'
        f'<text x="{left}" y="{top + height + 20}">0</text><text x="{left + width - 8}" y="{top + height + 20}">1</text>'
        f'<text x="{left + width - 60}" y="{top + height + 42}">False positive rate</text></svg>'
    )