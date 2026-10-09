from typing import Dict, Optional, List
import os
import json
import logging

logger = logging.getLogger(__name__)

USER_MEMORY_FILE: str = "user_memory.json"
MAX_NOTES_IN_CONTEXT: int = 3
MAX_NOTES_CHARS: int = 400


def load_user_memory() -> Dict[str, Dict]:
    """Loads the user memory store from a JSON file, or returns an empty store."""
    if os.path.exists(USER_MEMORY_FILE):
        try:
            with open(USER_MEMORY_FILE, "r") as f:
                data = json.load(f)
            logger.info("User memory loaded successfully.")
            return data if isinstance(data, dict) else {}
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load user memory: {e}. Starting with an empty store.")
    return {}


user_memory: Dict[str, Dict] = load_user_memory()


def save_user_memory() -> None:
    """Saves the user memory store to a JSON file."""
    try:
        with open(USER_MEMORY_FILE, "w") as f:
            json.dump(user_memory, f, indent=4)
    except IOError as e:
        logger.error(f"Failed to save user memory: {e}")


def upsert_user_profile(
    user_id: int,
    username: str,
    roles: Optional[List[str]] = None,
    display_name: Optional[str] = None,
) -> str:
    """Creates or refreshes a user's profile from Discord data. Existing notes are kept."""
    key = str(user_id)
    profile = user_memory.get(key)
    if profile is None:
        profile = {
            "username": username,
            "display_name": display_name or username,
            "roles": list(roles or []),
            "notes": [],
        }
        user_memory[key] = profile
    else:
        profile["username"] = username
        if display_name:
            profile["display_name"] = display_name
        if roles is not None:
            profile["roles"] = list(roles)
    save_user_memory()
    return key


def find_user(user_id: Optional[int] = None, username: Optional[str] = None) -> Optional[str]:
    """Returns the store key for a user matched by ID or case-insensitive username, or None."""
    if user_id is not None:
        key = str(user_id)
        if key in user_memory:
            return key
    if username:
        needle = username.lower()
        for key, profile in user_memory.items():
            if (
                profile.get("username", "").lower() == needle
                or profile.get("display_name", "").lower() == needle
            ):
                return key
    return None


def add_note(key: str, note: str) -> None:
    """Appends a note to a user's profile."""
    profile = user_memory.get(key)
    if profile is None:
        return
    notes = profile.setdefault("notes", [])
    if notes and notes[-1] == note:
        return
    notes.append(note)
    save_user_memory()


def profile_to_context(key: str) -> str:
    """Renders a user's stored profile as a compact string for model injection."""
    profile = user_memory.get(key)
    if profile is None:
        return ""
    lines = [f"User ID: {key}"]
    if profile.get("username"):
        lines.append(f"Username: {profile['username']}")
    display_name = profile.get("display_name")
    if display_name and display_name != profile.get("username"):
        lines.append(f"Display name: {display_name}")
    roles = profile.get("roles") or []
    if roles:
        lines.append(f"Roles: {', '.join(roles)}")
    notes = profile.get("notes") or []
    if notes:
        combined = " ".join(notes[-MAX_NOTES_IN_CONTEXT:])
        if len(combined) > MAX_NOTES_CHARS:
            combined = combined[:MAX_NOTES_CHARS].rstrip() + "..."
        lines.append(f"Notes: {combined}")
    return "\n".join(lines)
