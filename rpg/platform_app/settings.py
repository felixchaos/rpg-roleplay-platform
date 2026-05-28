from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from .db import connect, init_db


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
