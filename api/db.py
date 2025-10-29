# api/db.py
import os

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "Reward-Bot")

_client = None
_db = None

def _ensure_db_initialized():
    global _client, _db
    if _db is not None:
        return
    if not MONGO_URI:
        raise RuntimeError("MONGO_URI is not set in environment variables!")
    # Import motor lazily to avoid import-time failures in serverless cold starts
    from motor.motor_asyncio import AsyncIOMotorClient
    _client = AsyncIOMotorClient(MONGO_URI)
    _db = _client[MONGO_DB]

def get_db():
    _ensure_db_initialized()
    return _db

def get_collection(name: str):
    _ensure_db_initialized()
    return _db[name]

# Simple connectivity check
async def ping_db():
    _ensure_db_initialized()
    return await _db.command("ping")
