"""console_assistant._state — 进程内共享状态 (imports + 常量 + mutable global)."""
from __future__ import annotations

import json
import secrets
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from threading import Lock
from typing import Any

from tools_dsl.command_dispatcher import (
    ToolCallEnvelope,
    ToolDispatcher,
    ToolResult,
    get_registry,
)

# ────────────────────────────────────────────────────────────
# 常量
# ────────────────────────────────────────────────────────────

CONVERSATION_TTL_SECONDS = 60 * 60 * 6   # 6 小时不活跃后丢弃
MAX_CONVERSATIONS_PER_USER = 20
MAX_MESSAGES_PER_CONVERSATION = 60       # 防止 token 爆炸

# ────────────────────────────────────────────────────────────
# 可变共享状态 — 其他模块必须通过模块属性访问
# ────────────────────────────────────────────────────────────

_conversations: dict[int, dict[str, dict[str, Any]]] = {}
_lock = Lock()
