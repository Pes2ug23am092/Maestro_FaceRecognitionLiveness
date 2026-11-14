import os
import sys
import json
import time
import io
import unittest
from dotenv import load_dotenv

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
load_dotenv()

import boto3
from fastapi.testclient import TestClient
from backend.main import app
from backend.config import REGION, TABLE_DECISIONS, TABLE_SESSIONS

client = TestClient(app)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
decisions_table = dynamodb.Table(TABLE_DECISIONS)
sessions_table = dynamodb.Table(TABLE_SESSIONS)

def generate_sample_face_jpeg() -> bytes:
    from PIL import Image
    # Create simple 200x200 JPEG image
    img = Image.new("RGB", (200, 200), color=(180, 160, 140))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()

class TestUIBackendBridge(unittest.TestCase):

    def test_e2e_frontend_to_backend_live_aws_flow(self):
        print("\n=======================================================")
        print("  VERIFYING FRONTEND (MEMBER C) -> BACKEND (MEMBER A)  ")
        print("         WITH LIVE AWS CLOUD SERVICES                  ")
        print("=======================================================")

        # Step 1: Frontend calls POST /session to get a nonce
        print("\n[STEP 1] Frontend requests session nonce from /session...")
        res_session = client.post("/session")
        self.assertEqual(res_session.status_code, 200)
        sess_data = res_session.json()
        nonce = sess_data["nonce"]
        print(f"  --> Nonce received: {nonce}")

        # Verify that this nonce exists in AWS DynamoDB kiosk_sessions
        print("  [CLOUD CHECK] Querying AWS DynamoDB 'kiosk_sessions' table...")
        item = sessions_table.get_item(Key={"nonce": nonce}).get("Item")
        self.assertIsNotNone(item, "Nonce MUST exist in DynamoDB!")
        self.assertFalse(item.get("used"), "Nonce must initially be unused in DynamoDB!")
        print(f"  --> CONFIRMED IN AWS CLOUD: Nonce stored with TTL={item.get('ttl')}, used={item.get('used')}")

        # Step 2: Frontend captures camera photo and submits POST /decide as multipart/form-data
        print("\n[STEP 2] Frontend captures camera image and posts multipart/form-data to /decide...")
        jpeg_bytes = generate_sample_face_jpeg()
        
        # This replicates EXACTLY what frontend/utils.py send_decide_request() does:
        files = {"frame": ("capture.jpg", jpeg_bytes, "image/jpeg")}
        data = {"badge_id": "USER_001", "nonce": nonce, "zone_id": "ZONE_ENTRY_MAIN"}
        
        t0 = time.time()
        res_decide = client.post("/decide", data=data, files=files)
        elapsed = time.time() - t0
        self.assertEqual(res_decide.status_code, 200)
        decide_payload = res_decide.json()

        print(f"  --> Response received in {elapsed:.2f}s:")
        print(f"      decision:   {decide_payload.get('decision')} (Outcome: {decide_payload.get('outcome')})")
        print(f"      confidence: {decide_payload.get('confidence')}")
        print(f"      reason:     {decide_payload.get('reason')}")
        print(f"      event_id:   {decide_payload.get('event_id')}")

        # Step 3: Verify the frontend normalization fields are present
        self.assertIn(decide_payload.get("decision"), ["ALLOW", "REQUEST VERIFICATION", "DENY"])
        self.assertIn("timings", decide_payload)
        timings = decide_payload["timings"]
        print(f"  --> Cloud AWS Latencies: Rekognition={timings.get('rekognition_ms')}ms, Total={timings.get('total_ms')}ms")

        # Step 4: Verify Nonce was atomically marked USED in AWS DynamoDB
        print("\n[STEP 3] Verifying Nonce Anti-Replay in AWS DynamoDB...")
        item_after = sessions_table.get_item(Key={"nonce": nonce}).get("Item")
        self.assertTrue(item_after.get("used"), "Nonce MUST be marked used=True in DynamoDB!")
        print(f"  --> CONFIRMED IN AWS CLOUD: Nonce state updated to used={item_after.get('used')}")

        # Step 5: Verify Replay Attack fails immediately
        print("\n[STEP 4] Replaying the exact same request to test anti-replay...")
        replay_res = client.post("/decide", data=data, files=files)
        self.assertEqual(replay_res.status_code, 400)
        self.assertEqual(replay_res.json()["detail"]["reason"], "NONCE_REUSED")
        print("  --> CONFIRMED: Replay attack blocked with HTTP 400 (NONCE_REUSED)!")

        # Step 6: Verify Audit Trail in AWS DynamoDB kiosk_decisions
        event_id = decide_payload.get("event_id")
        print(f"\n[STEP 5] Querying AWS DynamoDB 'kiosk_decisions' for event_id: {event_id}...")
        decision_item = decisions_table.get_item(Key={"event_id": event_id}).get("Item")
        self.assertIsNotNone(decision_item, "Decision event MUST be recorded in DynamoDB audit trail!")
        print(f"  --> CONFIRMED IN AWS CLOUD: Audit event logged in DynamoDB:")
        print(f"      Outcome:   {decision_item.get('outcome')}")
        print(f"      User Hash: {decision_item.get('user_hash')} (Anonymized SHA-256)")
        print(f"      Timestamp: {decision_item.get('ts')}")
        print(f"      Mode:      {decision_item.get('mode')}")

        print("\n=======================================================")
        print("  ALL VERIFICATIONS PASSED: REAL AWS CLOUD CONFIRMED!  ")
        print("=======================================================\n")

if __name__ == "__main__":
    unittest.main(verbosity=2)
