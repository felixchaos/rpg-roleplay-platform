from __future__ import annotations

from typing import Any

from platform_app.db.connection import connect


def try_enable_pgvector() -> dict[str, Any]:
    """尝试启用 vector 扩展。pgvector 未安装时返回 {ok: False}，不抛异常。

    生产部署运维步骤：
      brew install pgvector  # macOS
      apt install postgresql-NN-pgvector  # debian
    然后下次 init_db 调用此函数会自动启用。
    """
    try:
        with connect() as db:
            row = db.execute(
                "select * from pg_available_extensions where name = 'vector'"
            ).fetchone()
            if not row:
                return {"ok": False, "reason": "pgvector 未在 server 端安装"}
            db.execute("create extension if not exists vector")
            # 检查是否已启用
            installed = db.execute(
                "select extversion from pg_extension where extname = 'vector'"
            ).fetchone()
            return {"ok": True, "version": installed["extversion"] if installed else None}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def has_pgvector() -> bool:
    try:
        with connect() as db:
            row = db.execute(
                "select 1 from pg_extension where extname = 'vector'"
            ).fetchone()
        return bool(row)
    except Exception:
        return False
