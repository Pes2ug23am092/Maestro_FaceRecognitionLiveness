import os
import io
import boto3
from botocore.exceptions import ClientError
from backend.config import (
    REGION, COLLECTION_ID, TABLE_USERS,
    FACE_QUALITY_MIN_SHARPNESS, FACE_QUALITY_MIN_BRIGHTNESS
)

rekognition = boto3.client("rekognition", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
users_table = dynamodb.Table(TABLE_USERS)

def check_face_quality(image_bytes: bytes) -> tuple[bool, str, dict]:
    """
    Quality gate before enrolment:
    Checks single face, sharpness, brightness, eyes open, and pose angles.
    """
    res = rekognition.detect_faces(
        Image={"Bytes": image_bytes},
        Attributes=["ALL"]
    )
    details = res.get("FaceDetails", [])
    if not details:
        return False, "NO_FACE_DETECTED", {}
    if len(details) > 1:
        return False, "MULTIPLE_FACES_DETECTED", {}

    face = details[0]
    quality = face.get("Quality", {})
    sharpness = quality.get("Sharpness", 0.0)
    brightness = quality.get("Brightness", 0.0)
    pose = face.get("Pose", {})
    eyes_open = face.get("EyesOpen", {}).get("Value", False)

    metrics = {
        "sharpness": sharpness,
        "brightness": brightness,
        "pitch": pose.get("Pitch", 0.0),
        "roll": pose.get("Roll", 0.0),
        "yaw": pose.get("Yaw", 0.0),
        "eyes_open": eyes_open
    }

    if sharpness < FACE_QUALITY_MIN_SHARPNESS:
        return False, f"LOW_SHARPNESS_{sharpness:.1f}", metrics
    if brightness < FACE_QUALITY_MIN_BRIGHTNESS:
        return False, f"LOW_BRIGHTNESS_{brightness:.1f}", metrics
    if abs(pose.get("Pitch", 0.0)) > 30 or abs(pose.get("Yaw", 0.0)) > 30:
        return False, "OFF_ANGLE_POSE", metrics
    if not eyes_open:
        return False, "EYES_CLOSED", metrics

    return True, "QUALITY_PASSED", metrics

def enroll_user(badge_id: str, image_bytes: bytes, group_id: str = "GRP_EMPLOYEE") -> dict:
    """
    Enrolls a user into the Rekognition Collection and DynamoDB users table.
    Enforces quality filtering and stores vectors only.
    """
    # 1. Quality Gate
    passed, reason, metrics = check_face_quality(image_bytes)
    if not passed:
        return {
            "success": False,
            "badge_id": badge_id,
            "error": reason,
            "metrics": metrics
        }

    # 2. Index face in collection
    index_res = rekognition.index_faces(
        CollectionId=COLLECTION_ID,
        Image={"Bytes": image_bytes},
        ExternalImageId=badge_id,
        DetectionAttributes=["ALL"],
        QualityFilter="AUTO",
        MaxFaces=1
    )

    face_records = index_res.get("FaceRecords", [])
    if not face_records:
        unindexed = index_res.get("UnindexedFaces", [])
        return {
            "success": False,
            "badge_id": badge_id,
            "error": "FACE_NOT_INDEXED_BY_FILTER",
            "details": unindexed
        }

    face_id = face_records[0]["Face"]["FaceId"]

    # 3. Save or update user in DynamoDB
    users_table.put_item(
        Item={
            "badge_id": badge_id,
            "group_id": group_id,
            "status": "active",
            "primary_face_id": face_id,
            "enrolled_at": int(__import__("time").time())  # timestamp only: never store account/ARN details in the users table
        }
    )

    return {
        "success": True,
        "badge_id": badge_id,
        "face_id": face_id,
        "quality_metrics": metrics
    }

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Enroll a user face into Rekognition")
    parser.add_argument("--badge", required=True, help="Badge ID (e.g. USER_001)")
    parser.add_argument("--image", required=True, help="Path to face image file")
    parser.add_argument("--group", default="GRP_EMPLOYEE", help="Group ID")
    args = parser.parse_args()

    with open(args.image, "rb") as f:
        img_data = f.read()
    res = enroll_user(args.badge, img_data, args.group)
    print(res)
