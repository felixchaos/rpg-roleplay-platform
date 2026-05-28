"""platform_app.db — DB 连接/初始化/migrations/utils 子包."""
from .connection import database_url, connect, get_pool, close_pool
from .init import init_db, reset_db_init_flag, _do_init_db
from .migrations import (
    MIGRATIONS,
    list_migrations,
    _migration_advisory_lock,
    _apply_versioned_migrations,
    _assert_schema_up_to_date,
    _assert_migrations_monotonic,
)
from .pgvector import try_enable_pgvector, has_pgvector
from .utils import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    redacted_url,
    expose,
    limit_value,
    cursor_id,
    page_payload,
)

# status() references connect, redacted_url, has_pgvector, database_url — defined here
# after all imports to avoid circular deps.
from typing import Any


def status(reveal_details: bool = False) -> dict:
    """数据库健康状态。

    安全：默认只返回 {driver, ok}。仅 admin 接口才传 reveal_details=True，
    返回 url/database/user/version 等部署信息。
    """
    try:
        with connect() as db:
            row = db.execute("select 1 as ok").fetchone()
        out: dict = {"driver": "postgresql", "ok": bool(row), "pgvector": has_pgvector()}
        if reveal_details:
            with connect() as db:
                meta = db.execute("select current_database() as database, current_user as user, version() as version").fetchone()
            out["url"] = redacted_url(database_url())
            out.update(dict(meta))
        return out
    except Exception as exc:
        out = {"driver": "postgresql", "ok": False}
        if reveal_details:
            out["url"] = redacted_url(database_url())
            out["error"] = str(exc)
        return out


__all__ = [
    "database_url", "connect", "get_pool", "close_pool", "status",
    "init_db", "reset_db_init_flag",
    "list_migrations",
    "try_enable_pgvector", "has_pgvector",
    "redacted_url", "expose", "limit_value", "cursor_id", "page_payload",
    "DEFAULT_LIMIT", "MAX_LIMIT",
    "MIGRATIONS",
    # private but used by migrate.py
    "_migration_advisory_lock", "_do_init_db", "_apply_versioned_migrations",
    "_assert_schema_up_to_date", "_assert_migrations_monotonic",
]
