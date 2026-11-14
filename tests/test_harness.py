import os
import sys
import json
import time
import base64
import hashlib
import unittest
from dotenv import load_dotenv

# Ensure repo root is on python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv()

from fastapi.testclient import TestClient
from backend.main import app
from backend.policy_service import raw_rs_to_der
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives import hashes, serialization

client = TestClient(app)

# Helper: Create a small valid test JPEG in memory (100x100 RGB)
def generate_synthetic_jpeg() -> bytes:
    try:
        from PIL import Image
        import io
        img = Image.new("RGB", (200, 200), color=(180, 160, 140))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        return buf.getvalue()
    except ImportError:
        # Fallback raw 1x1 minimal valid JPEG
        return bytes.fromhex(
            "ffd8ffe000104a46494600010101004800480000ffdb00430008060607060508070707090908"
            "0a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c303134"
            "34341f27393d38323c2e333430ffc0000b080001000101011100ffc4001f0000010501010101"
            "010100000000000000000102030405060708090a0bffda0008010100003f007f00ffd9"
        )

class TestKioskBackend(unittest.TestCase):

    def test_01_health_check(self):
        print("\n[TEST 1] Checking GET /health...")
        res = client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        print(f"  Result: {data}")

    def test_02_session_creation_and_ttl(self):
        print("\n[TEST 2] Checking POST /session...")
        res = client.post("/session")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("nonce", data)
        self.assertIn("expires_at", data)
        self.assertGreater(data["expires_at"], int(time.time()))
        print(f"  Nonce created: {data['nonce'][:12]}... (TTL: {data['expires_at']})")

    def test_03_nonce_replay_attack_prevention(self):
        print("\n[TEST 3] Testing Nonce Anti-Replay Protection...")
        # 1. Create a fresh session
        sess_res = client.post("/session").json()
        nonce = sess_res["nonce"]

        sample_img = base64.b64encode(generate_synthetic_jpeg()).decode("utf-8")
        payload = {
            "nonce": nonce,
            "frames": [sample_img],
            "badge_id": "USER_001",
            "zone_id": "ZONE_ENTRY_MAIN"
        }

        # First consumption
        res1 = client.post("/decide", json=payload)
        self.assertEqual(res1.status_code, 200)
        print(f"  First call status: {res1.status_code} ({res1.json().get('outcome')})")

        # Second consumption (Replay Attack)
        res2 = client.post("/decide", json=payload)
        self.assertEqual(res2.status_code, 400)
        detail = res2.json().get("detail", {})
        self.assertEqual(detail.get("reason"), "NONCE_REUSED")
        print(f"  Replay attack successfully blocked! Response: 400 {detail.get('reason')}")

    def test_04_policy_bundle_and_kms_crypto_verification(self):
        print("\n[TEST 4] Testing GET /policy-bundle and ECDSA WebCrypto verification...")
        res = client.get("/policy-bundle")
        self.assertEqual(res.status_code, 200)
        bundle = res.json()

        self.assertIn("bundle_version", bundle)
        self.assertIn("signature", bundle)
        self.assertIn("public_key", bundle)
        self.assertIn("revocations", bundle)
        self.assertIn("USER_REVOKED", bundle["revocations"])

        raw_rs_b64 = bundle["signature"]
        pub_pem = bundle["public_key"]

        # Verify cryptographic signature using public key (mirroring kiosk WebCrypto)
        raw_rs = base64.b64decode(raw_rs_b64)
        self.assertEqual(len(raw_rs), 64, "Raw r||s signature must be exactly 64 bytes")

        der_sig = raw_rs_to_der(raw_rs)
        pub_key = serialization.load_pem_public_key(pub_pem.encode("utf-8"))

        # Re-compute digest
        bundle_content = {
            "bundle_version": bundle["bundle_version"],
            "expires_at": bundle["expires_at"],
            "zones": bundle["zones"],
            "groups": bundle["groups"],
            "schedules": bundle["schedules"],
            "revocations": bundle["revocations"]
        }
        canonical_bytes = json.dumps(bundle_content, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(canonical_bytes).digest()

        # Cryptographic verification assert
        pub_key.verify(der_sig, digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        print("  Cryptographic Signature (IEEE P1363 raw r||s) verified successfully!")
        print(f"  Bundle Version: {bundle['bundle_version']}, Revocations: {bundle['revocations']}")

    def test_05_sync_idempotency_and_reconciliation(self):
        print("\n[TEST 5] Testing POST /sync idempotency & offline revocation alerts...")
        batch_id = f"BATCH_{int(time.time())}"
        test_events = [
            {
                "event_id": f"EVT_{batch_id}_1",
                "seq": 1,
                "ts": int(time.time()),
                "badge_id": "USER_001",
                "user_hash": "hash_alice",
                "outcome": "ALLOW"
            },
            {
                "event_id": f"EVT_{batch_id}_2",
                "seq": 2,
                "ts": int(time.time()),
                "badge_id": "USER_REVOKED",  # Should trigger reconciliation alert
                "user_hash": "hash_eve",
                "outcome": "ALLOW"
            }
        ]

        # First sync submission
        res1 = client.post("/sync", json={
            "kiosk_id": "KIOSK_NORTH_01",
            "batch_id": batch_id,
            "events": test_events
        })
        self.assertEqual(res1.status_code, 200)
        data1 = res1.json()
        self.assertEqual(data1["new_event_count"], 2)
        self.assertEqual(data1["duplicate_count"], 0)
        self.assertEqual(len(data1["reconciled_alerts"]), 1)
        self.assertEqual(data1["reconciled_alerts"][0]["alert_type"], "OFFLINE_ALLOW_REVOKED")
        print(f"  First Sync: 2 new events, Alert detected: {data1['reconciled_alerts'][0]['alert_type']}")

        # Second sync submission (Network Flapping / Retransmission)
        res2 = client.post("/sync", json={
            "kiosk_id": "KIOSK_NORTH_01",
            "batch_id": batch_id,
            "events": test_events
        })
        self.assertEqual(res2.status_code, 200)
        data2 = res2.json()
        self.assertEqual(data2["new_event_count"], 0)
        self.assertEqual(data2["duplicate_count"], 2)
        print("  Second Sync: 0 new events, 2 duplicates detected (Idempotency GUARANTEED).")

    def test_06_otp_step_up_and_lockout(self):
        print("\n[TEST 6] Testing OTP Step-up verification and 3-attempt lockout...")
        badge_id = "USER_TEST_OTP"
        kiosk_id = "KIOSK_DEMO"

        # Generate OTP
        gen_res = client.post("/generate-otp", json={"badge_id": badge_id, "kiosk_id": kiosk_id}).json()
        valid_code = gen_res["code_for_demo"]
        print(f"  Generated OTP: {valid_code}")

        # Attempt 1: Wrong Code
        res_fail1 = client.post("/verify-otp", json={"badge_id": badge_id, "kiosk_id": kiosk_id, "otp_code": "000000"})
        self.assertEqual(res_fail1.json()["status"], "INVALID")
        self.assertEqual(res_fail1.json()["attempts_left"], 2)

        # Attempt 2: Wrong Code
        res_fail2 = client.post("/verify-otp", json={"badge_id": badge_id, "kiosk_id": kiosk_id, "otp_code": "111111"})
        self.assertEqual(res_fail2.json()["status"], "INVALID")
        self.assertEqual(res_fail2.json()["attempts_left"], 1)

        # Attempt 3: Wrong Code -> Lockout!
        res_fail3 = client.post("/verify-otp", json={"badge_id": badge_id, "kiosk_id": kiosk_id, "otp_code": "222222"})
        self.assertEqual(res_fail3.status_code, 403)
        self.assertEqual(res_fail3.json()["detail"]["status"], "LOCKED_OUT")
        print("  Lockout enforced after 3 failed attempts (HTTP 403 LOCKED_OUT)!")

    def test_07_credential_issuance(self):
        print("\n[TEST 7] Testing POST /credential...")
        res = client.post("/credential", json={"badge_id": "USER_001"})
        self.assertEqual(res.status_code, 200)
        cred = res.json()
        self.assertIn("signature", cred)
        self.assertIn("zone_grants", cred)
        print(f"  Credential pass issued with grants: {cred['zone_grants']}")

if __name__ == "__main__":
    unittest.main(verbosity=2)
