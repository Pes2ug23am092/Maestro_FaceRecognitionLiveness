import time
import uuid
import boto3
from botocore.exceptions import ClientError
from backend.config import REGION, TABLE_SESSIONS, SESSION_TTL_SECONDS

dynamodb = boto3.resource("dynamodb", region_name=REGION)
sessions_table = dynamodb.Table(TABLE_SESSIONS)

def create_session() -> dict:
    nonce = str(uuid.uuid4())
    now = int(time.time())
    expires_at = now + SESSION_TTL_SECONDS
    
    sessions_table.put_item(
        Item={
            "nonce": nonce,
            "created_at": now,
            "expires_at": expires_at,
            "ttl": expires_at,
            "used": False
        }
    )
    return {
        "nonce": nonce,
        "expires_at": expires_at
    }

def validate_and_consume_nonce(nonce: str) -> tuple[bool, str]:
    """
    Validates that the nonce exists, has not expired, and has not been used.
    Atomically marks the nonce as used.
    Returns (is_valid, reason).
    """
    now = int(time.time())
    try:
        response = sessions_table.update_item(
            Key={"nonce": nonce},
            UpdateExpression="SET used = :true_val, used_at = :now_val",
            ConditionExpression="attribute_exists(nonce) AND used = :false_val AND expires_at >= :now_val",
            ExpressionAttributeValues={
                ":true_val": True,
                ":false_val": False,
                ":now_val": now
            },
            ReturnValues="ALL_NEW"
        )
        return True, "NONCE_VALID"
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "ConditionalCheckFailedException":
            # Check why it failed
            item = sessions_table.get_item(Key={"nonce": nonce}).get("Item")
            if not item:
                return False, "NONCE_NOT_FOUND"
            if item.get("used"):
                return False, "NONCE_REUSED"
            if item.get("expires_at", 0) < now:
                return False, "NONCE_EXPIRED"
            return False, "NONCE_INVALID"
        raise
