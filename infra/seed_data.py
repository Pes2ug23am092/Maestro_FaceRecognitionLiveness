import os
import json
import boto3
from dotenv import load_dotenv

load_dotenv()
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
dynamodb = boto3.resource("dynamodb", region_name=REGION)

def seed_campus_data():
    seed_file = os.path.join("config", "seed_campus.json")
    if not os.path.exists(seed_file):
        print(f"Seed file not found: {seed_file}")
        return

    with open(seed_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. Zones
    tbl_zones = dynamodb.Table("kiosk_zones")
    for z in data.get("zones", []):
        tbl_zones.put_item(Item=z)
    print(f"[SEEDED] {len(data.get('zones', []))} zones.")

    # 2. Groups
    tbl_groups = dynamodb.Table("kiosk_groups")
    for g in data.get("groups", []):
        tbl_groups.put_item(Item=g)
    print(f"[SEEDED] {len(data.get('groups', []))} groups.")

    # 3. Schedules
    tbl_schedules = dynamodb.Table("kiosk_schedules")
    for s in data.get("schedules", []):
        tbl_schedules.put_item(Item=s)
    print(f"[SEEDED] {len(data.get('schedules', []))} schedules.")

    # 4. Users
    tbl_users = dynamodb.Table("kiosk_users")
    for u in data.get("users", []):
        tbl_users.put_item(Item=u)
    print(f"[SEEDED] {len(data.get('users', []))} users.")

if __name__ == "__main__":
    seed_campus_data()
