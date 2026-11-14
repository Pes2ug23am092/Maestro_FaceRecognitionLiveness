import unittest

from backend.fusion import PolicyThresholds, RiskThresholds, decide


THRESHOLDS = PolicyThresholds(
    spoof_veto=0.98,
    phone_paper_veto=0.98,
    risk_levels={
        "lobby": RiskThresholds(0.92, 0.20, 0.82, 0.50),
        "server_room": RiskThresholds(0.97, 0.10, 0.90, 0.65),
    },
)


class FusionTests(unittest.TestCase):
    def decide(self, **overrides):
        values = {
            "spoof_prob": 0.05,
            "signals": {},
            "quality": True,
            "similarity": 0.95,
            "thresholds": THRESHOLDS,
        }
        values.update(overrides)
        return decide(**values)

    def test_clear_tier1_case_allows(self):
        result = self.decide()
        self.assertEqual((result.outcome, result.tier), ("ALLOW", "tier1"))

    def test_spoof_veto_denies_before_any_positive_signal(self):
        result = self.decide(signals={"phone_paper_confidence": 0.99})
        self.assertEqual(result.outcome, "DENY")
        self.assertIn("phone_or_paper_veto", result.reasons)

    def test_low_quality_requests_recapture(self):
        result = self.decide(quality=False)
        self.assertEqual((result.outcome, result.reasons), ("STEP_UP", ("recapture_quality",)))

    def test_identity_below_deny_threshold_denies(self):
        result = self.decide(similarity=0.40)
        self.assertEqual(result.outcome, "DENY")

    def test_positive_step_up_resolves_uncertain_liveness(self):
        result = self.decide(spoof_prob=0.35, tier2_passed=True)
        self.assertEqual((result.outcome, result.tier), ("ALLOW", "tier2"))

    def test_failed_step_up_denies(self):
        result = self.decide(tier2_passed=False)
        self.assertEqual((result.outcome, result.reasons), ("DENY", ("tier2_failed",)))

    def test_invalid_probability_is_rejected(self):
        with self.assertRaises(ValueError):
            self.decide(spoof_prob=float("nan"))

    def test_non_boolean_step_up_result_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "tier results"):
            self.decide(tier2_passed=1)

    def test_risk_level_changes_allow_boundary(self):
        lobby = self.decide(spoof_prob=0.06)
        server = self.decide(spoof_prob=0.06, risk_level="server_room")
        self.assertEqual(lobby.outcome, "ALLOW")
        self.assertEqual(server.outcome, "STEP_UP")


if __name__ == "__main__":
    unittest.main()