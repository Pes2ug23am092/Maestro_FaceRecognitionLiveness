import tempfile
import unittest
from pathlib import Path

from eval.metrics import build_report, percentile, roc_det_points, validate_records, wilson_interval, write_report


def record(sample_id, truth, outcome, *, attack_type=None, latency=100, cold=False, identity=None,
           mode="ONLINE", online_outcome=None, must_deny_offline=False,
           sync_success=None, sync_latency=None):
    return {
        "sample_id": sample_id,
        "split": "locked_test",
        "mode": mode,
        "truth": truth,
        "attack_type": attack_type,
        "identity_is_match": identity,
        "cold_start": cold,
        "latency_ms": latency,
        "spoof_prob": 0.9 if truth == "attack" else 0.1,
        "outcome": outcome,
        "online_outcome": online_outcome or outcome,
        "must_deny_offline": must_deny_offline,
        "sync_success": sync_success,
        "sync_latency_ms": sync_latency,
        "ablation": None if mode == "OFFLINE" else {
            "rekognition_only": "STEP_UP",
            "tier1_only": outcome,
            "tier1_tier2": outcome,
            "full_stack": outcome,
        },
    }


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            record("s1", "genuine", "ALLOW", latency=100, identity=True),
            record("s2", "genuine", "DENY", latency=200, identity=True),
            record("s3", "attack", "ALLOW", attack_type="screen_replay", latency=300, cold=True, identity=False),
            record("s4", "attack", "STEP_UP", attack_type="screen_replay", latency=400, identity=False),
        ]

    def test_report_contains_expected_rates_and_ablation(self):
        rows, markdown, dashboard = build_report(self.records)
        by_key = {(row["metric"], row["group"]): row for row in rows}
        self.assertEqual(by_key[("APCER", "overall")]["estimate"], 0.5)
        self.assertEqual(by_key[("BPCER", "overall")]["estimate"], 0.5)
        self.assertEqual(by_key[("ACER", "overall")]["estimate"], 0.5)
        self.assertEqual(by_key[("identity_FAR", "overall")]["numerator"], 1)
        self.assertIn("Wilson", markdown)
        self.assertIn("Online and Offline Confusion Matrices", dashboard)
        self.assertIn("ROC curve", dashboard)

    def test_offline_metrics_report_fail_open_and_sync(self):
        offline = [
            record("o1", "attack", "ALLOW", attack_type="printed_photo", mode="OFFLINE",
                   online_outcome="DENY", must_deny_offline=True, sync_success=True, sync_latency=250),
            record("o2", "genuine", "STEP_UP", mode="OFFLINE", online_outcome="ALLOW",
                   sync_success=False, sync_latency=500),
        ]
        rows, markdown, _ = build_report(offline)
        by_key = {(row["metric"], row["group"]): row for row in rows}
        self.assertEqual(by_key[("offline_fail_open_rate", "offline")]["numerator"], 1)
        self.assertEqual(by_key[("sync_success_rate", "offline")]["estimate"], 0.5)
        self.assertIn("fail-open", markdown)

    def test_policy_and_chaos_evidence_is_in_report(self):
        policy = {"total": 82, "passed": 82, "failures": []}
        drills = {"total": 11, "passed": 11, "failOpen": 0, "results": []}
        rows, markdown, dashboard = build_report(self.records, policy, drills)
        by_key = {(row["metric"], row["group"]): row for row in rows}
        self.assertEqual(by_key[("policy_vector_pass_rate", "policy_vector")]["estimate"], 1.0)
        self.assertEqual(by_key[("offline_drill_fail_open_count", "offline_drill")]["estimate"], 0)
        self.assertIn("82/82 passed", markdown)
        self.assertIn("11/11 passed", dashboard)

    def test_roc_det_sweep_contains_perfect_separation(self):
        points = roc_det_points(self.records)
        self.assertEqual(points[0]["false_positive_rate"], 0.0)
        self.assertEqual(points[-1]["true_positive_rate"], 1.0)
        self.assertEqual(points[-1]["false_negative_rate"], 0.0)

    def test_wilson_interval_handles_zero_errors(self):
        lower, upper = wilson_interval(0, 10)
        self.assertEqual(lower, 0.0)
        self.assertGreater(upper, 0.0)

    def test_percentile_uses_nearest_rank(self):
        self.assertEqual(percentile([100, 200, 300, 400], 95), 400)

    def test_non_locked_split_is_rejected(self):
        self.records[0]["split"] = "validation"
        with self.assertRaisesRegex(ValueError, "locked_test"):
            validate_records(self.records)

    def test_duplicate_sample_id_is_rejected(self):
        self.records[1]["sample_id"] = "s1"
        with self.assertRaisesRegex(ValueError, "duplicate sample_id"):
            validate_records(self.records)

    def test_ci_is_missing_when_denominator_is_zero(self):
        self.assertIsNone(wilson_interval(0, 0))

    def test_write_report_creates_all_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            write_report(self.records, directory)
            self.assertEqual(
                {path.name for path in Path(directory).iterdir()},
                {"metrics.csv", "roc_det.csv", "report.md", "dashboard.html"},
            )


if __name__ == "__main__":
    unittest.main()