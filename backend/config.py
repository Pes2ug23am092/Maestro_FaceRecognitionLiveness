import os
import json
from dotenv import load_dotenv

load_dotenv()

REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
COLLECTION_ID = os.getenv("COLLECTION_ID", "kiosk-face-collection-v1")

# Tables
TABLE_USERS = os.getenv("TABLE_USERS", "kiosk_users")
TABLE_SESSIONS = os.getenv("TABLE_SESSIONS", "kiosk_sessions")
TABLE_DECISIONS = os.getenv("TABLE_DECISIONS", "kiosk_decisions")
TABLE_SYNC_BATCHES = os.getenv("TABLE_SYNC_BATCHES", "kiosk_sync_batches")
TABLE_ZONES = os.getenv("TABLE_ZONES", "kiosk_zones")
TABLE_GROUPS = os.getenv("TABLE_GROUPS", "kiosk_groups")
TABLE_SCHEDULES = os.getenv("TABLE_SCHEDULES", "kiosk_schedules")

# KMS
KMS_KEY_ALIAS = os.getenv("KMS_KEY_ALIAS", "alias/kiosk-policy-signer")
KMS_KEY_ID = os.getenv("KMS_KEY_ID", None)

# Thresholds
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "90.0"))
FACE_QUALITY_MIN_SHARPNESS = float(os.getenv("FACE_QUALITY_MIN_SHARPNESS", "50.0"))
FACE_QUALITY_MIN_BRIGHTNESS = float(os.getenv("FACE_QUALITY_MIN_BRIGHTNESS", "40.0"))
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "30"))
