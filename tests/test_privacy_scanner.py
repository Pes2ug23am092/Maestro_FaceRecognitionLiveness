import unittest

from eval.scan_no_biometrics import scan_document


class PrivacyScannerTests(unittest.TestCase):
    def test_minimal_audit_metadata_and_score_are_allowed(self):
        findings = scan_document({
            "event_id": "opaque-1",
            "user_ref": "salted-hash",
            "outcome": "ALLOW",
            "liveness_score": 0.99,
            "mode": "OFFLINE",
        })
        self.assertEqual(findings, [])

    def test_face_embedding_field_is_rejected(self):
        findings = scan_document({"face_embedding": [0.1, 0.2]})
        self.assertTrue(any("forbidden biometric/media field" in item for item in findings))

    def test_bare_face_field_is_rejected(self):
        findings = scan_document({"face": "not-permitted-in-an-offline-export"})
        self.assertTrue(any("forbidden biometric/media field" in item for item in findings))

    def test_embedded_image_data_is_rejected(self):
        findings = scan_document({"payload": "data:image/png;base64,abc"})
        self.assertTrue(any("data URI" in item for item in findings))

    def test_media_path_is_rejected(self):
        findings = scan_document({"attachment": "capture.webp"})
        self.assertTrue(any("media file reference" in item for item in findings))


if __name__ == "__main__":
    unittest.main()