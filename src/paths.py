"""Shared filesystem locations for the bot.

All paths are anchored at the project root (the parent of ``src/``), so the
bot runs the same whether invoked as ``python -m src.main`` from the root or
from anywhere else.
"""

import os

SRC_DIR: str = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT: str = os.path.dirname(SRC_DIR)

CONFIG_DIR: str = os.path.join(PROJECT_ROOT, "config")
CONFIG_FILE_PATH: str = os.path.join(CONFIG_DIR, "config.json")

DATA_DIR: str = os.path.join(PROJECT_ROOT, "data")
CHAT_HISTORY_PATH: str = os.path.join(DATA_DIR, "chat_history.json")
USER_MEMORY_PATH: str = os.path.join(DATA_DIR, "user_memory.json")

LOG_DIR: str = os.path.join(PROJECT_ROOT, "logs")
BOT_LOG_PATH: str = os.path.join(LOG_DIR, "bot.log")

PROMPTS_DIR: str = os.path.join(PROJECT_ROOT, "prompts")


def resolve_root_relative(path: str) -> str:
    """Resolve a config-supplied relative path against the project root."""
    if os.path.isabs(path):
        return path
    return os.path.join(PROJECT_ROOT, path)


def ensure_runtime_dirs() -> None:
    """Create writable runtime dirs (data/, logs/) if they don't exist."""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)
