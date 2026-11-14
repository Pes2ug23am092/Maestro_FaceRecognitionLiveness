import os
import json
import base64
import hashlib
import time
from datetime import datetime, timezone, timedelta
import boto3
from cryptography.hazmat.primitives.asymmetric import utils, ec
from cryptography.hazmat.primitives import hashes, serialization
from backend.config import REGION, KMS_KEY_ALIAS, KMS_KEY_ID

kms = boto3.client("kms", region_name=REGION)

# Local ephemeral software key fallback if KMS key alias is not found
_LOCAL_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())

def der_to_raw_rs(der_bytes: bytes) -> bytes:
    """Converts ASN.1 DER ECDSA signature to IEEE P1363 raw r||s (64 bytes)."""
    r, s = utils.decode_dss_signature(der_bytes)
    return r.to_bytes(32, byteorder="big") + s.to_bytes(32, byteorder="big")

def raw_rs_to_der(raw_bytes: bytes) -> bytes:
    """Converts IEEE P1363 raw r||s (64 bytes) to ASN.1 DER format."""
    r = int.from_bytes(raw_bytes[:32], byteorder="big")
    s = int.from_bytes(raw_bytes[32:], byteorder="big")
    return utils.encode_dss_signature(r, s)

def sign_digest(digest: bytes) -> tuple[str, str, str]:
    """
    Signs a 32-byte SHA-256 digest using AWS KMS (ECDSA_SHA_256) or local fallback.
    Returns (raw_rs_base64, der_base64, public_key_pem).
    """
    key_id = KMS_KEY_ID or KMS_KEY_ALIAS
    try:
        # KMS Sign
        res = kms.sign(
            KeyId=key_id,
            Message=digest,
            MessageType="DIGEST",
            SigningAlgorithm="ECDSA_SHA_256"
        )
        der_sig = res["Signature"]
        raw_rs = der_to_raw_rs(der_sig)

        pub_res = kms.get_public_key(KeyId=key_id)
        pub_der = pub_res["PublicKey"]
        pub_key = serialization.load_der_public_key(pub_der)
        pub_pem = pub_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("utf-8")

        return (
            base64.b64encode(raw_rs).decode("utf-8"),
            base64.b64encode(der_sig).decode("utf-8"),
            pub_pem
        )
    except Exception as e:
        print(f"[INFO] KMS sign fallback to software EC key: {e}")
        der_sig = _LOCAL_PRIVATE_KEY.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        raw_rs = der_to_raw_rs(der_sig)
        pub_pem = _LOCAL_PRIVATE_KEY.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        ).decode("utf-8")
        return (
            base64.b64encode(raw_rs).decode("utf-8"),
            base64.b64encode(der_sig).decode("utf-8"),
            pub_pem
        )

def get_policy_bundle_payload() -> dict:
    """
    Loads campus policies, revocation lists, signs the bundle digest with ECDSA,
    and returns the complete signed bundle for edge kiosks.
    """
    seed_path = os.path.join("config", "seed_campus.json")
    if os.path.exists(seed_path):
        with open(seed_path, "r", encoding="utf-8") as f:
            campus_data = json.load(f)
    else:
        campus_data = {"zones": [], "groups": [], "schedules": [], "users": []}

    revocations = [
        u["badge_id"] for u in campus_data.get("users", [])
        if u.get("status") in ["revoked", "suspended"]
    ]

    expires_at = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat()
    bundle_content = {
        "bundle_version": f"v1-{int(time.time())}",
        "expires_at": expires_at,
        "zones": campus_data.get("zones", []),
        "groups": campus_data.get("groups", []),
        "schedules": campus_data.get("schedules", []),
        "revocations": revocations
    }

    # Canonical JSON string for deterministic hashing
    canonical_bytes = json.dumps(bundle_content, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(canonical_bytes).digest()

    raw_rs_b64, der_b64, pub_pem = sign_digest(digest)

    bundle_content["signature"] = raw_rs_b64
    bundle_content["signature_der"] = der_b64
    bundle_content["signature_format"] = "raw_rs_base64"
    bundle_content["public_key"] = pub_pem
    bundle_content["digest_sha256"] = digest.hex()

    return bundle_content

def issue_credential(badge_id: str) -> dict:
    """
    Issues a short-lived signed credential pass (e.g. for offline QR or pass verification).
    """
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()
    cred_body = {
        "badge_id": badge_id,
        "zone_grants": ["ZONE_ENTRY_MAIN", "ZONE_OFFICE_A"],
        "expires_at": expires_at,
        "nonce": str(time.time())
    }
    canonical_bytes = json.dumps(cred_body, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(canonical_bytes).digest()
    raw_rs_b64, der_b64, pub_pem = sign_digest(digest)

    cred_body["signature"] = raw_rs_b64
    cred_body["public_key"] = pub_pem
    return cred_body
