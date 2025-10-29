# api/db.py
import os
from pymongo import MongoClient

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
    # Initialize synchronous PyMongo client for serverless stability
    _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    _db = _client[MONGO_DB]

def get_db():
    _ensure_db_initialized()
    return _db

def get_collection(name: str):
    _ensure_db_initialized()
    return _db[name]

# Simple connectivity check (sync)
def ping_db_sync():
    _ensure_db_initialized()
    return _db.command("ping")
