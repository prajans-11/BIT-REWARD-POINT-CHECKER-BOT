# api/models.py
from datetime import datetime
from bson import ObjectId
from typing import Optional
from api.db import get_collection

async def upsert_user(user_id: int, username: Optional[str], last_seen: datetime, last_report: dict = None):
    query = {"user_id": int(user_id)}
    update = {
        "$set": {"username": username, "last_seen": last_seen},
        "$inc": {"total_requests": 1}
    }
    if last_report is not None:
        update["$set"]["last_report"] = last_report
    # create with default total_requests=0 if not exists
    users_col = get_collection("users")
    await users_col.update_one(query, update, upsert=True)

async def create_user_if_missing(user_id: int, username: Optional[str], last_seen: datetime):
    users_col = get_collection("users")
    doc = await users_col.find_one({"user_id": int(user_id)})
    if not doc:
        await users_col.insert_one({
            "user_id": int(user_id),
            "username": username,
            "last_seen": last_seen,
            "total_requests": 0,
            "last_report": None
        })

async def save_report(user_id: int, roll_no: str, report: dict):
    now = datetime.utcnow()
    doc = {
        "user_id": int(user_id),
        "roll_no": roll_no,
        "report": report,
        "created_at": now
    }
    reports_col = get_collection("reports")
    res = await reports_col.insert_one(doc)
    # update user's last_report and last_seen + increment count
    users_col = get_collection("users")
    await users_col.update_one(
        {"user_id": int(user_id)},
        {
            "$set": {"last_report": report, "last_seen": now},
            "$inc": {"total_requests": 1}
        },
        upsert=True
    )
    return str(res.inserted_id)

async def get_last_report(user_id: int):
    users_col = get_collection("users")
    user = await users_col.find_one({"user_id": int(user_id)})
    if not user:
        return None
    return user.get("last_report")
