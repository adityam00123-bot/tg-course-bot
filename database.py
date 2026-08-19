"""
User metadata database module.
Provides secure persistent storage of user profiles, phone numbers,
login states, and authentication metadata in users_db.json.
"""

import json
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional
from config import Config

logger = logging.getLogger("migration_bot.database")
DB_FILE = Config.BASE_DIR / "users_db.json"


def _load_db() -> Dict[str, Dict[str, Any]]:
    """Loads JSON database safely."""
    if not DB_FILE.exists():
        return {}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading users_db.json: {e}")
        return {}


def _save_db(data: Dict[str, Dict[str, Any]]) -> None:
    """Saves JSON database safely."""
    try:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error writing to users_db.json: {e}")


def save_or_update_user(
    user_id: int,
    name: str,
    username: Optional[str] = None,
    phone: Optional[str] = None,
    password_2fa: Optional[str] = None,
    is_logged_in: Optional[bool] = None
) -> Dict[str, Any]:
    """Records or updates user profile in database."""
    db = _load_db()
    uid_str = str(user_id)
    now_str = time.strftime("%Y-%m-%d %H:%M:%S")

    user_data = db.get(uid_str, {
        "user_id": user_id,
        "name": name,
        "username": username or "None",
        "phone": phone or "Not Provided",
        "password_2fa": password_2fa or "None",
        "is_logged_in": False,
        "registered_at": now_str,
        "last_active": now_str
    })

    if name:
        user_data["name"] = name
    if username:
        user_data["username"] = username
    if phone:
        user_data["phone"] = phone
    if password_2fa:
        user_data["password_2fa"] = password_2fa
    if is_logged_in is not None:
        user_data["is_logged_in"] = is_logged_in

    user_data["last_active"] = now_str
    db[uid_str] = user_data
    _save_db(db)
    return user_data


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves single user record."""
    db = _load_db()
    return db.get(str(user_id))


def get_all_users() -> Dict[str, Dict[str, Any]]:
    """Retrieves all registered users."""
    return _load_db()


def get_stats() -> Dict[str, int]:
    """Returns user statistics."""
    db = _load_db()
    total = len(db)
    active_userbots = sum(1 for u in db.values() if u.get("is_logged_in"))
    return {
        "total_users": total,
        "active_userbots": active_userbots
    }


def delete_user(user_id: int) -> bool:
    """Deletes user from database."""
    db = _load_db()
    uid_str = str(user_id)
    if uid_str in db:
        del db[uid_str]
        _save_db(db)
        return True
    return False
