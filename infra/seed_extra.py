"""Safe re-seed of schedules + NEW users from config/seed_campus.json into DynamoDB.
Overwrites schedule rows (same ids), only ADDS users that do not exist yet (never clobbers enrolments / statuses)."""
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

data = json.loads((Path(__file__).resolve().parents[1] / "config" / "seed_campus.json").read_bytes().decode("utf-8-sig"))
ddb = boto3.resource("dynamodb", region_name="us-east-1")
t = ddb.Table("kiosk_schedules")
for r in data["schedules"]:
    t.put_item(Item=r)
added = 0
for u in data["users"]:
    try:
        ddb.Table("kiosk_users").put_item(Item=u, ConditionExpression="attribute_not_exists(badge_id)")
        added += 1
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
print(f"upserted {len(data['schedules'])} schedules; added {added} new users (existing users untouched)")
