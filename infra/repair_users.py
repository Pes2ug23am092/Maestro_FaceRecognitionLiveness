"""Restore name/group/status/validity of the demo users from config/seed_campus.json, KEEPING each user's enrolled face id.
(Repairs records overwritten by the old enroll_user bug.)   python infra/repair_users.py"""
import json
from pathlib import Path

import boto3

seed = json.loads((Path(__file__).resolve().parents[1] / "config" / "seed_campus.json").read_bytes().decode("utf-8-sig"))
t = boto3.resource("dynamodb", region_name="us-east-1").Table("kiosk_users")
for u in seed["users"]:
    t.update_item(Key={"badge_id": u["badge_id"]},
                  UpdateExpression="SET #n = :n, group_id = :g, #s = :s, valid_from = :vf, valid_to = :vt",
                  ExpressionAttributeNames={"#n": "name", "#s": "status"},
                  ExpressionAttributeValues={":n": u["name"], ":g": u["group_id"], ":s": u["status"], ":vf": u["valid_from"], ":vt": u["valid_to"]})
for it in sorted(t.scan()["Items"], key=lambda x: x["badge_id"]):
    print(f"{it['badge_id']:13s} {it['group_id']:16s} {it['status']:9s} face_enrolled={bool(it.get('primary_face_id'))}")
