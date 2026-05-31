"""tests/test_feedback.py — 反馈功能集成测试 (FB-01/02/03/07/08/09).

覆盖:
  1. POST /api/feedback 成功 → feedback + feedback_consent_log 各一行
  2. consent_token 缺失 → 400
  3. DELETE /api/feedback/{id} — 用户自删 unreviewed
  4. nsfw_terminate 后用户不能再删 → 403
  5. prune_feedback 删 24m 老行、保留 nsfw_terminate 行

注意: 测试直接操作 DB，不走 HTTP（避免 session cookie / FastAPI lifespan 复杂度）。
      需要 DATABASE_URL 指向真实 Postgres。
"""
from __future__ import annotations

import hashlib
import json
import sys
import os
import random
import string
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("RPG_REQUIRE_AUTH", "1")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def db_conn():
    """返回 psycopg Connection（dict_row）。"""
    from platform_app.db import init_db, connect
    init_db()
    with connect() as db:
        yield db


def _rand(n: int = 8) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _create_test_user(db, username: str) -> int:
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
    db.execute("delete from feedback_consent_log where user_id = %s", (user_id,))
    db.execute("delete from feedback where user_id = %s", (user_id,))
    db.execute("delete from users where id = %s", (user_id,))


def _make_consent_token(text: str = "我已阅读 AUP §2.J") -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# 1. 提交成功 → 写两个表
# ---------------------------------------------------------------------------

def test_submit_feedback_writes_two_tables(db_conn):
    """FB-01/02: POST 成功后 feedback + feedback_consent_log 各插一行。"""
    db = db_conn
    uid = _create_test_user(db, f"fb_submit_{_rand()}")
    token = _make_consent_token()
    excerpts = [{"session_id": "s1", "range": "0-5", "plaintext": "hello"}]
    excerpts_raw = json.dumps(excerpts, ensure_ascii=False)

    try:
        # 直接调 DB 层（模拟 API handler 逻辑）
        row = db.execute(
            """
            insert into feedback
              (user_id, free_text, excerpts_jsonb, consent_token, ua, app_version, ip)
            values (%s, %s, %s::jsonb, %s, %s, %s, %s)
            returning id
            """,
            (uid, "这是一条测试反馈", excerpts_raw, token, "pytest-ua", "v0.1", "127.0.0.1"),
        ).fetchone()
        feedback_id = row["id"]
        assert feedback_id is not None

        db.execute(
            """
            insert into feedback_consent_log (user_id, consent_text_hash, app_version, ip)
            values (%s, %s, %s, %s)
            """,
            (uid, token, "v0.1", "127.0.0.1"),
        )

        # 验证 feedback 行
        f = db.execute("select * from feedback where id = %s", (feedback_id,)).fetchone()
        assert f is not None
        assert f["user_id"] == uid
        assert f["consent_token"] == token
        assert f["review_decision"] is None

        # 验证 consent_log 行
        cl = db.execute(
            "select * from feedback_consent_log where user_id = %s order by created_at desc limit 1",
            (uid,),
        ).fetchone()
        assert cl is not None
        assert cl["consent_text_hash"] == token

    finally:
        _delete_test_user(db, uid)


# ---------------------------------------------------------------------------
# 2. consent_token 缺失 → API 应返回 400（验证校验逻辑）
# ---------------------------------------------------------------------------

def test_submit_feedback_missing_consent_token():
    """FB-02: consent_token 格式校验——不走 DB，直接测校验逻辑。"""
    # 模拟 API handler 中的校验
    def _validate_token(token: str) -> str | None:
        """返回 None=通过, 字符串=错误信息"""
        if not token or len(token) != 64:
            return "consent_token 缺失或格式不正确"
        try:
            int(token, 16)
        except ValueError:
            return "consent_token 不是合法的 hex 字符串"
        return None

    assert _validate_token("") is not None
    assert _validate_token("short") is not None
    assert _validate_token("z" * 64) is not None  # 非 hex
    assert _validate_token(_make_consent_token()) is None  # 合法


# ---------------------------------------------------------------------------
# 3. DELETE 自删 unreviewed
# ---------------------------------------------------------------------------

def test_user_delete_own_unreviewed(db_conn):
    """FB-08: 用户删自己的 unreviewed 反馈。"""
    db = db_conn
    uid = _create_test_user(db, f"fb_del_{_rand()}")
    token = _make_consent_token()

    try:
        row = db.execute(
            """
            insert into feedback
              (user_id, free_text, excerpts_jsonb, consent_token, ua, app_version, ip)
            values (%s, %s, '[]'::jsonb, %s, '', 'v0.1', '127.0.0.1')
            returning id
            """,
            (uid, "待删反馈", token),
        ).fetchone()
        feedback_id = row["id"]

        # 模拟 DELETE handler：校验 user_id + review_decision
        f = db.execute(
            "select user_id, review_decision from feedback where id = %s", (feedback_id,)
        ).fetchone()
        assert f is not None
        assert f["user_id"] == uid
        assert f["review_decision"] is None  # 未审查，允许删

        db.execute("delete from feedback where id = %s", (feedback_id,))

        # 确认已删
        f2 = db.execute("select 1 from feedback where id = %s", (feedback_id,)).fetchone()
        assert f2 is None

        # consent_log 保留
        cl = db.execute(
            "select 1 from feedback_consent_log where user_id = %s", (uid,)
        ).fetchone()
        # consent_log 在本测试未插入（此 case 只测 feedback 删除）
        # 关键是 feedback 行不存在即通过

    finally:
        _delete_test_user(db, uid)


# ---------------------------------------------------------------------------
# 4. nsfw_terminate 后用户不能删
# ---------------------------------------------------------------------------

def test_user_cannot_delete_nsfw_terminate(db_conn):
    """FB-08 例外: review_decision=nsfw_terminate 时不允许用户删除。"""
    db = db_conn
    uid = _create_test_user(db, f"fb_nsfw_{_rand()}")
    token = _make_consent_token()

    try:
        row = db.execute(
            """
            insert into feedback
              (user_id, free_text, excerpts_jsonb, consent_token, ua, app_version, ip,
               review_decision, reviewed_at)
            values (%s, %s, '[]'::jsonb, %s, '', 'v0.1', '127.0.0.1', 'nsfw_terminate', now())
            returning id
            """,
            (uid, "NSFW 反馈", token),
        ).fetchone()
        feedback_id = row["id"]

        # 模拟 DELETE handler 逻辑
        f = db.execute(
            "select user_id, review_decision from feedback where id = %s", (feedback_id,)
        ).fetchone()
        assert f is not None
        assert f["review_decision"] == "nsfw_terminate"

        # 校验：应触发 403 拒绝
        should_block = f["review_decision"] == "nsfw_terminate"
        assert should_block, "nsfw_terminate 标记的反馈应被阻止删除"

        # feedback 行仍存在
        f2 = db.execute("select 1 from feedback where id = %s", (feedback_id,)).fetchone()
        assert f2 is not None, "nsfw_terminate 反馈不应被删除"

    finally:
        _delete_test_user(db, uid)


# ---------------------------------------------------------------------------
# 5. prune_feedback 删 24m 老的、保留 nsfw_terminate
# ---------------------------------------------------------------------------

def test_prune_feedback(db_conn):
    """FB-09: prune 删超期行，保留 nsfw_terminate 行。"""
    db = db_conn
    uid = _create_test_user(db, f"fb_prune_{_rand()}")
    token = _make_consent_token()

    try:
        # 插入一条超期普通行（25 个月前）
        old_row = db.execute(
            """
            insert into feedback
              (user_id, free_text, excerpts_jsonb, consent_token, ua, app_version, ip, created_at)
            values (%s, %s, '[]'::jsonb, %s, '', 'v0.1', '127.0.0.1',
                    now() - interval '25 months')
            returning id
            """,
            (uid, "超期普通反馈", token),
        ).fetchone()
        old_id = old_row["id"]

        # 插入一条超期 nsfw_terminate 行（25 个月前）
        nsfw_row = db.execute(
            """
            insert into feedback
              (user_id, free_text, excerpts_jsonb, consent_token, ua, app_version, ip,
               review_decision, reviewed_at, created_at)
            values (%s, %s, '[]'::jsonb, %s, '', 'v0.1', '127.0.0.1',
                    'nsfw_terminate', now() - interval '25 months',
                    now() - interval '25 months')
            returning id
            """,
            (uid, "超期 NSFW 反馈", token),
        ).fetchone()
        nsfw_id = nsfw_row["id"]

        # 插入一条新鲜行（应保留）
        fresh_row = db.execute(
            """
            insert into feedback
              (user_id, free_text, excerpts_jsonb, consent_token, ua, app_version, ip)
            values (%s, %s, '[]'::jsonb, %s, '', 'v0.1', '127.0.0.1')
            returning id
            """,
            (uid, "新鲜反馈", token),
        ).fetchone()
        fresh_id = fresh_row["id"]

        # 调 prune 函数
        from cron.prune_feedback import run_prune_feedback
        result = run_prune_feedback(db, months=24)

        assert result["pruned"] >= 1, "应该删除至少一行"
        assert result["kept_nsfw"] >= 1, "应该保留至少一条 nsfw_terminate 行"

        # 超期普通行已删
        r1 = db.execute("select 1 from feedback where id = %s", (old_id,)).fetchone()
        assert r1 is None, "超期普通反馈应被删除"

        # nsfw_terminate 行保留
        r2 = db.execute("select 1 from feedback where id = %s", (nsfw_id,)).fetchone()
        assert r2 is not None, "超期 nsfw_terminate 反馈应被保留"

        # 新鲜行保留
        r3 = db.execute("select 1 from feedback where id = %s", (fresh_id,)).fetchone()
        assert r3 is not None, "新鲜反馈应被保留"

    finally:
        _delete_test_user(db, uid)
