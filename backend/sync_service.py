import time
import boto3
from botocore.exceptions import ClientError
from backend.config import REGION, TABLE_DECISIONS, TABLE_SYNC_BATCHES, TABLE_USERS

dynamodb = boto3.resource("dynamodb", region_name=REGION)
decisions_table = dynamodb.Table(TABLE_DECISIONS)
sync_batches_table = dynamodb.Table(TABLE_SYNC_BATCHES)
users_table = dynamodb.Table(TABLE_USERS)

def sync_audit_batch(kiosk_id: str, batch_id: str, events: list[dict]) -> dict:
    """
    Idempotent offline audit sync:
    - Writes each event with a conditional put (fails silently if event_id already exists).
    - Detects whether any offline event allowed an access for a user who was subsequently revoked.
    - Records the batch in sync_batches table.
    """
    acked_ids = []
    duplicate_ids = []
    reconciled_alerts = []

    # Sort events by seq if available
    sorted_events = sorted(events, key=lambda x: x.get("seq", 0))

    for ev in sorted_events:
        event_id = ev.get("event_id")
        if not event_id:
            continue

        item = {
            "event_id": event_id,
            "seq": ev.get("seq", 0),
            "kiosk_id": kiosk_id,
            "ts": ev.get("ts", int(time.time())),
            "user_hash": ev.get("user_hash", "UNKNOWN"),
            "badge_id": ev.get("badge_id", "UNKNOWN"),
            "outcome": ev.get("outcome", "UNKNOWN"),
            "mode": "offline",
            "synced_at": int(time.time()),
            "prev_hash": ev.get("prev_hash", ""),
            "hash": ev.get("hash", "")
        }

        # Idempotent Conditional Put
        try:
            decisions_table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(event_id)"
            )
            acked_ids.append(event_id)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Duplicate event safely skipped
                duplicate_ids.append(event_id)
                acked_ids.append(event_id)  # Still acknowledge receipt so edge kiosk knows it's synced
            else:
                raise

        # Reconciliation check: Check if user was revoked/suspended
        badge_id = ev.get("badge_id")
        if badge_id and ev.get("outcome") == "ALLOW":
            try:
                user = users_table.get_item(Key={"badge_id": badge_id}).get("Item")
                if user and user.get("status") in ["revoked", "suspended"]:
                    alert = {
                        "alert_type": "OFFLINE_ALLOW_REVOKED",
                        "event_id": event_id,
                        "badge_id": badge_id,
                        "status": user.get("status"),
                        "message": f"User {badge_id} was allowed offline during outage but is currently {user.get('status')}!"
                    }
                    reconciled_alerts.append(alert)
                    print(f"[SECURITY ALERT] {alert['message']}")
            except Exception as e:
                print(f"[WARN] Reconciliation lookup error: {e}")

    # Record sync batch summary
    try:
        sync_batches_table.put_item(
            Item={
                "batch_id": batch_id,
                "kiosk_id": kiosk_id,
                "received_at": int(time.time()),
                "total_events": len(events),
                "new_events": len(acked_ids) - len(duplicate_ids),
                "duplicates": len(duplicate_ids),
                "alerts_raised": len(reconciled_alerts)
            }
        )
    except Exception as e:
        print(f"[WARN] Failed to write sync_batch record: {e}")

    return {
        "status": "ACK",
        "batch_id": batch_id,
        "kiosk_id": kiosk_id,
        "acknowledged_event_ids": acked_ids,
        "new_event_count": len(acked_ids) - len(duplicate_ids),
        "duplicate_count": len(duplicate_ids),
        "reconciled_alerts": reconciled_alerts
    }
