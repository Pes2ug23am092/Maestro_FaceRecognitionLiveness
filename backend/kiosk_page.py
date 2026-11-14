"""Browser kiosk served by the backend: GET /kiosk (burst capture, Tier 2 colour flash + head turn, OTP step-up, enrol)
and GET /config (zones/users for the dropdowns). Same origin as the API, so no CORS/URL setup."""
import base64
from pathlib import Path

import boto3
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.campus_adapter import load_policy_seed
from backend.config import COLLECTION_ID, REGION, TABLE_USERS
from backend.enrolment_service import check_face_quality

router = APIRouter()


@router.get("/config")
def config():
    seed = load_policy_seed()
    return {"zones": [{"id": z["id"], "name": z["name"], "risk": z["risk"]} for z in seed["zones"]],
            "users": [{"id": u["id"], "name": u.get("name", u["id"]), "group": u["groups"][0], "status": u["status"]}
                      for u in seed["users"]]}


class EnrolAll(BaseModel):
    image_base64: str


@router.post("/demo/enrol-all")
def enrol_all(req: EnrolAll):
    """DEMO helper: index ONE face under every demo badge so each campus role can be demonstrated with a single person.
    Updates only primary_face_id (a revoked badge stays revoked; groups/validity untouched)."""
    img = base64.b64decode(req.image_base64)
    ok, reason, metrics = check_face_quality(img)
    if not ok:
        raise HTTPException(400, {"success": False, "error": reason, "metrics": metrics})
    rek = boto3.client("rekognition", region_name=REGION)
    users = boto3.resource("dynamodb", region_name=REGION).Table(TABLE_USERS)
    done = []
    for u in load_policy_seed()["users"]:
        r = rek.index_faces(CollectionId=COLLECTION_ID, Image={"Bytes": img}, ExternalImageId=u["id"],
                            DetectionAttributes=["DEFAULT"], QualityFilter="AUTO", MaxFaces=1)
        if r.get("FaceRecords"):
            users.update_item(Key={"badge_id": u["id"]}, UpdateExpression="SET primary_face_id = :f",
                              ExpressionAttributeValues={":f": r["FaceRecords"][0]["Face"]["FaceId"]})
            done.append(u["id"])
    return {"success": bool(done), "enrolled": done}


@router.get("/kiosk", response_class=HTMLResponse)
def kiosk():
    return (Path(__file__).with_name("kiosk.html")).read_text(encoding="utf-8")
