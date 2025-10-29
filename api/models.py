# api/models.py
from datetime import datetime
from typing import Optional
import asyncio
from api.db import get_collection

async def upsert_user(user_id: int, username: Optional[str], last_seen: datetime, last_report: dict = None):
    query = {"user_id": int(user_id)}
    update = {
        "$set": {"username": username, "last_seen": last_seen},
        "$inc": {"total_requests": 1}
    }
    if last_report is not None:
        update["$set"]["last_report"] = last_report
    try:
        users_col = get_collection("users")
        await asyncio.to_thread(users_col.update_one, query, update, upsert=True)
    except Exception:
        # DB not configured or unreachable; ignore to keep bot responsive
        return

async def create_user_if_missing(user_id: int, username: Optional[str], last_seen: datetime):
    try:
        users_col = get_collection("users")
        doc = await asyncio.to_thread(users_col.find_one, {"user_id": int(user_id)})
        if not doc:
            await asyncio.to_thread(users_col.insert_one, {
                "user_id": int(user_id),
                "username": username,
                "last_seen": last_seen,
                "total_requests": 0,
                "last_report": None
            })
    except Exception:
        return

async def save_report(user_id: int, roll_no: str, report: dict):
    now = datetime.utcnow()
    doc = {
        "user_id": int(user_id),
        "roll_no": roll_no,
        "report": report,
        "created_at": now
    }
    try:
        reports_col = get_collection("reports")
        res = await asyncio.to_thread(reports_col.insert_one, doc)
        users_col = get_collection("users")
        await asyncio.to_thread(
            users_col.update_one,
            {"user_id": int(user_id)},
            {
                "$set": {"last_report": report, "last_seen": now},
                "$inc": {"total_requests": 1}
            },
            upsert=True
        )
        return str(res.inserted_id)
    except Exception:
        return None

async def get_last_report(user_id: int):
    try:
        users_col = get_collection("users")
        user = await asyncio.to_thread(users_col.find_one, {"user_id": int(user_id)})
        if not user:
            return None
        return user.get("last_report")
    except Exception:
        return None
