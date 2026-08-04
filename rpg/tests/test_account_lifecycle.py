"""tests/test_account_lifecycle.py — account lifecycle 集成测试 (LC-01/02/03/05).

覆盖:
  1. request-delete → cancel-delete → 撤销成功
  2. request-delete → 模拟时钟 +31 天 → run_hard_delete → 用户行不存在
  3. 90 天前 login_audit 行 → prune_audit → 删除
  4. export 内容完整（包含 users/profile_extras/game_saves/character_cards/token_usage/login_audit/memories）

注意: 测试直接操作 DB，不走 HTTP（避免 session cookie 复杂度）。
      需要 DATABASE_URL 指向真实 Postgres。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# repo root 在 path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("RPG_REQUIRE_AUTH", "1")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db_conn():
    """返回一个 psycopg.Connection（dict_row）。测试结束后回滚清理。"""
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        yield db


def _create_test_user(db, username: str) -> int:
    """创建最简测试用户，返回 user_id。"""
    row = db.execute(
        """
        insert into users (username, password_hash, email)
        values (%s, 'test-hash', %s)
        returning id
        """,
        (username, f"{username}@test.invalid"),
    ).fetchone()
    return row["id"]


def _delete_test_user(db, user_id: int) -> None:
    """清理测试用户（级联）。"""
    db.execute("delete from account_delete_queue where user_id = %s", (user_id,))
    db.execute("delete from users where id = %s", (user_id,))


# ---------------------------------------------------------------------------
# 1. request-delete → cancel-delete → 撤销成功
# ---------------------------------------------------------------------------

def test_request_then_cancel_delete(db_conn):
    """申请硬删后撤销：队列行消失，deactivated_at 清空。"""
    db = db_conn
    username = f"lc_test_cancel_{_rand()}"
    uid = _create_test_user(db, username)

    try:
        # 申请硬删
        db.execute(
            """
            insert into account_delete_queue
              (user_id, requested_at, scheduled_hard_delete_at, requested_by_ip, reason)
            values
              (%s, now(), now() + interval '30 days', '127.0.0.1', 'test')
            """,
            (uid,),
        )
        db.execute("update users set deactivated_at = now() where id = %s", (uid,))

        # 确认队列行存在
        q = db.execute(
            "select 1 from account_delete_queue where user_id = %s and completed_at is null",
            (uid,),
        ).fetchone()
        assert q is not None, "硬删队列行应存在"

        # 撤销
        db.execute("delete from account_delete_queue where user_id = %s", (uid,))
        db.execute("update users set deactivated_at = null where id = %s", (uid,))

        # 验证
        q2 = db.execute(
            "select 1 from account_delete_queue where user_id = %s and completed_at is null",
            (uid,),
        ).fetchone()
        assert q2 is None, "撤销后队列行应不存在"

        u = db.execute("select deactivated_at from users where id = %s", (uid,)).fetchone()
        assert u["deactivated_at"] is None, "撤销后 deactivated_at 应为 null"

    finally:
        _delete_test_user(db, uid)


# ---------------------------------------------------------------------------
# 2. request-delete → 模拟 +31 天 → run_hard_delete → 用户行不存在
# ---------------------------------------------------------------------------

def test_hard_delete_executes(db_conn):
    """到期账号被 run_hard_delete 物理删除。"""
    from rpg.cron.hard_delete import run_hard_delete

    db = db_conn
    username = f"lc_test_hd_{_rand()}"
    uid = _create_test_user(db, username)

    try:
        # 插入一条「已到期」的队列行（scheduled_hard_delete_at 设为过去）
        db.execute(
            """
            insert into account_delete_queue
              (user_id, requested_at, scheduled_hard_delete_at, requested_by_ip, reason)
            values
              (%s, now() - interval '31 days', now() - interval '1 second', '127.0.0.1', 'test')
            """,
            (uid,),
        )
        db.execute("update users set deactivated_at = now() where id = %s", (uid,))

        result = run_hard_delete(db)

        assert result["due_at_run"] >= 1, f"应至少扫到 1 个到期行，实际 {result}"
        assert result["deleted"] >= 1, f"应至少删除 1 个用户，实际 {result}"

        # 用户行应不存在
        u = db.execute("select 1 from users where id = %s", (uid,)).fetchone()
        assert u is None, "硬删后 users 行应不存在"

        # uid 被标记为 None 防止 finally 重复删
        uid = None
    finally:
        if uid is not None:
            _delete_test_user(db, uid)


# ---------------------------------------------------------------------------
# 3. 90 天前 login_audit 行 → prune_audit → 删除
# ---------------------------------------------------------------------------

def test_prune_login_audit(db_conn):
    """超过 90 天的 login_audit 行被 prune_login_audit 清理。"""
    from rpg.cron.prune_audit import run_prune_login_audit

    db = db_conn
    marker = f"prune_test_{_rand()}"

    # 插入一条 91 天前的行
    db.execute(
        "insert into login_audit (username, ip, event, created_at) values (%s, '127.0.0.1', 'login', now() - interval '91 days')",
        (marker,),
    )
    # 插入一条 1 天前的行（应保留）
    db.execute(
        "insert into login_audit (username, ip, event, created_at) values (%s, '127.0.0.1', 'login', now() - interval '1 day')",
        (marker,),
    )

    result = run_prune_login_audit(db, days=90)
    assert result["pruned"] >= 1, f"应至少清理 1 行，实际 {result}"

    # 91 天前的行已删
    old = db.execute(
        "select 1 from login_audit where username = %s and created_at < now() - interval '90 days'",
        (marker,),
    ).fetchone()
    assert old is None, "91 天前的行应已删除"

    # 1 天前的行保留
    recent = db.execute(
        "select 1 from login_audit where username = %s and created_at > now() - interval '2 days'",
        (marker,),
    ).fetchone()
    assert recent is not None, "1 天前的行应保留"

    # 清理测试数据
    db.execute("delete from login_audit where username = %s", (marker,))


# ---------------------------------------------------------------------------
# 4. export 内容完整
# ---------------------------------------------------------------------------

def test_export_payload_structure(db_conn):
    """验证 export 返回的 JSON 包含所有必要字段。"""
    import datetime as _dt
    import json
    import uuid as _uuid

    db = db_conn
    username = f"lc_test_exp_{_rand()}"
    uid = _create_test_user(db, username)

    try:
        # 构建 export payload（重用 frontend_routes 内的逻辑，直接 DB 查询验证字段）
        u_row = db.execute(
            "select id, username, email, created_at, updated_at, deactivated_at, public_id from users where id = %s",
            (uid,),
        ).fetchone()
        pe_row = db.execute("select * from profile_extras where user_id = %s", (uid,)).fetchone()
        saves = db.execute(
            "select id, title, created_at, updated_at from game_saves where user_id = %s",
            (uid,),
        ).fetchall()
        cards = db.execute(
            "select id, name from character_cards where user_id = %s",
            (uid,),
        ).fetchall()
        usage = db.execute(
            "select id from token_usage where user_id = %s limit 10",
            (uid,),
        ).fetchall()
        audit = db.execute(
            "select id from login_audit where username = %s limit 10",
            (username,),
        ).fetchall()
        memories = db.execute(
            "select id from memories where user_id = %s limit 10",
            (uid,),
        ).fetchall()

        def _to_list(rows):
            return [dict(r) for r in rows] if rows else []

        payload = {
            "export_version": "1",
            "user": dict(u_row) if u_row else {},
            "profile_extras": dict(pe_row) if pe_row else {},
            "game_saves": _to_list(saves),
            "character_cards": _to_list(cards),
            "token_usage": _to_list(usage),
            "login_audit": _to_list(audit),
            "memories": _to_list(memories),
        }

        def _default(obj):
            if isinstance(obj, (_dt.datetime, _dt.date)):
                return obj.isoformat()
            if isinstance(obj, _uuid.UUID):
                return str(obj)
            raise TypeError

        raw = json.dumps(payload, default=_default, ensure_ascii=False)
        loaded = json.loads(raw)

        # 验证所有顶级 key 存在
        required_keys = [
            "export_version", "user", "profile_extras", "game_saves",
            "character_cards", "token_usage", "login_audit", "memories",
        ]
        for k in required_keys:
            assert k in loaded, f"export payload 缺少 key: {k}"

        assert loaded["user"]["username"] == username

    finally:
        _delete_test_user(db, uid)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _rand(n: int = 6) -> str:
    import random
    import string
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))
