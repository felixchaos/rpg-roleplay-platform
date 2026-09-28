from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from .db import connect, init_db


# 设置页读取时兼容的旧中文偏好键(memory-section 的 loadOrFallback)。后端按同一口径生效,
# 否则用户在设置页看到的是旧键的值、GM 用的却是默认值。
_LEGACY_MEMORY_PREF_KEYS = {
    "recall_depth": "settings.召回深度",
    "summary_window": "settings.摘要窗口",
    "pinned_max": "settings.固定记忆上限",
}


def get_memory_settings(user_id: int):
    """读取用户的记忆系统配置，缺失 key 时返回默认值。

    返回 MemorySettings 实例（Pydantic model），调用方可直接属性访问。
    Import 放在函数内部，避免循环依赖。

    数据源(后者覆盖前者):
      1. settings 表的 memory.*(POST /api/settings 写;目前没有客户端调它,只作老数据兜底);
      2. user_preferences.preferences 的 memory.* —— 三端「设置 → 记忆」页经
         POST /api/me/preference 真实写入的地方。以前只读 1,设置页怎么调都不生效。
    逐字段校验:某个值非法只丢掉那一项,不把其它合法设置一起打回默认。
    """
    from schemas.memory import MemorySettings

    prefix = "memory."
    data: dict[str, Any] = {}
    try:
        for k, v in list_settings(user_id).items():
            if k.startswith(prefix):
                data[k[len(prefix):]] = v
    except Exception:
        pass
    try:
        from core.request_cache import get_user_prefs_cached
        prefs = get_user_prefs_cached(int(user_id)) or {}
    except Exception:
        prefs = {}
    for field, legacy_key in _LEGACY_MEMORY_PREF_KEYS.items():
        if f"{prefix}{field}" not in prefs and prefs.get(legacy_key) is not None:
            data[field] = prefs[legacy_key]
    for k, v in prefs.items():
        if isinstance(k, str) and k.startswith(prefix) and v is not None:
            data[k[len(prefix):]] = v

    valid: dict[str, Any] = {}
    for field in MemorySettings.model_fields:
        if field not in data:
            continue
        try:
            valid[field] = getattr(MemorySettings.model_validate({field: data[field]}), field)
        except Exception:
            continue
    return MemorySettings(**valid)


def list_settings(user_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        return {
            row["key"]: row["value"]
            for row in db.execute("select key, value from settings where user_id = %s", (user_id,)).fetchall()
        }


_VALID_KEY_RE = __import__("re").compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def set_setting(user_id: int, key: str, value: Any) -> dict[str, Any]:
    init_db()
    key = (key or "").strip()
    if not key:
        raise ValueError("setting key 不能为空")
    if not _VALID_KEY_RE.match(key):
        raise ValueError("setting key 必须以字母开头，仅含字母数字 _ . - 且 ≤64 字符")
    with connect() as db:
        db.execute(
            """
            insert into settings(user_id, key, value)
            values (%s, %s, %s)
            on conflict(user_id, key)
            do update set value = excluded.value, updated_at = now()
            """,
            (user_id, key, Jsonb(value)),
        )
    return list_settings(user_id)
