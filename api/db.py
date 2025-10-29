# api/db.py
import os
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB", "Reward-Bot")

if not MONGO_URI:
    raise RuntimeError("MONGO_URI is not set in environment variables!")

_client = AsyncIOMotorClient(MONGO_URI)
_db = _client[MONGO_DB]

def get_db():
    return _db

def get_collection(name: str):
    return _db[name]
