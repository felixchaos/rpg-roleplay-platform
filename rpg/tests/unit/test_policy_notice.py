"""unit/test_policy_notice.py — DOC-02 / AUP-03 政策通知核心逻辑单测。

使用 in-memory mock db（字典行为），不依赖真实数据库。

Cases:
  1. schedule_policy_change 创建 pending record
  2. list_pending_notices 过滤已激活
  3. dispatch_notice (RESEND_API_KEY 缺失) 降级写日志,标 dispatched_at
  4. dispatch_notice (mock Resend 成功) 发邮件,计数正确
  5. activate_notice 更新 policy_versions,标 activated_at
  6. 30d cron — run_dispatch_due 只发 effective_at <= now+30d 的
  7. activate cron — run_activate_due 只激活 effective_at <= now 且已发邮件的
  8. 双语邮件内容 (zh-CN / en)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ─────────────────────────── mock db ─────────────────────────────────────────

class MockDb:
    """最小 psycopg-like mock,用 dict 存 app_config。"""

    def __init__(self):
        self._store: dict[str, str] = {}  # key -> json str

    def execute(self, sql: str, params=()) -> "MockCursor":
        sql_stripped = sql.strip().lower()

        if sql_stripped.startswith("select value from app_config"):
            key = params[0]
            value = self._store.get(key)
            if value is not None:
                return MockCursor([{"value": json.loads(value)}])
            return MockCursor([])

        if "insert into app_config" in sql_stripped:
            key, value_obj = params
            # value_obj may be psycopg Jsonb or dict/list
            raw = value_obj
            if hasattr(raw, "obj"):
                raw = raw.obj  # psycopg Jsonb wrapper
            self._store[key] = json.dumps(raw)
            return MockCursor([])

        if sql_stripped.startswith("select u.email"):
            # returns users mock
            return MockCursor(self._users if hasattr(self, "_users") else [])

        return MockCursor([])

    def set_users(self, users: list[dict]) -> None:
        self._users = users


class MockCursor:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def fetchone(self) -> dict | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict]:
        return self._rows


# ─────────────────────────── helpers ─────────────────────────────────────────

def make_db(**kwargs) -> MockDb:
    return MockDb()


def future_dt(days: int = 31) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=days)


def past_dt(days: int = 1) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


# ─────────────────────────── tests ───────────────────────────────────────────

from platform_app.policy_notice import (
    activate_notice,
    dispatch_notice,
    get_current_version,
    list_pending_notices,
    schedule_policy_change,
    _build_email,
)
from cron.policy_notice import run_dispatch_due, run_activate_due


def test_schedule_creates_pending():
    db = make_db()
    record = schedule_policy_change(
        db, "privacy-policy", "v1.3", "小幅修订数据保留期限"
    )
    assert record["slug"] == "privacy-policy"
    assert record["new_version"] == "v1.3"
    assert record["activated_at"] is None
    assert record["dispatched_at"] is None
    assert "id" in record

    pending = list_pending_notices(db)
    assert len(pending) == 1
    assert pending[0]["id"] == record["id"]


def test_list_pending_excludes_activated():
    db = make_db()
    r1 = schedule_policy_change(db, "privacy-policy", "v1.3", "摘要A")
    r2 = schedule_policy_change(db, "terms-of-service", "v2.0", "摘要B")

    # 手动激活 r1
    activate_notice(db, r1["id"])

    pending = list_pending_notices(db)
    ids = [n["id"] for n in pending]
    assert r1["id"] not in ids
    assert r2["id"] in ids


def test_dispatch_degraded_without_resend_key():
    """RESEND_API_KEY 未配置时,dispatch 应降级:标 dispatched_at,recipients_sent=0。"""
    db = make_db()
    db.set_users([
        {"email": "a@example.com", "lang": "zh-CN"},
        {"email": "b@example.com", "lang": "en"},
    ])
    record = schedule_policy_change(db, "privacy-policy", "v1.3", "摘要")

    with patch("platform_app.email.RESEND_API_KEY", ""):
        result = dispatch_notice(db, record["id"])

    assert result["dispatched_at"] is not None
    assert result["recipients_sent"] == 0
    assert result["recipients_total"] == 2


def test_dispatch_sends_emails_via_resend():
    """mock Resend API 返回 200,应计数 sent=2。"""
    db = make_db()
    db.set_users([
        {"email": "a@example.com", "lang": "zh-CN"},
        {"email": "b@example.com", "lang": "en"},
    ])
    record = schedule_policy_change(db, "terms-of-service", "v2.0", "major update")

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("platform_app.email.RESEND_API_KEY", "re_test_key"), \
         patch("platform_app.policy_notice.httpx.post", return_value=mock_response) as mock_post, \
         patch("platform_app.policy_notice.time.sleep"):
        result = dispatch_notice(db, record["id"])

    assert result["recipients_sent"] == 2
    assert result["recipients_total"] == 2
    assert mock_post.call_count == 2


def test_activate_updates_version():
    db = make_db()
    record = schedule_policy_change(db, "cookie-policy", "v2.0", "新版 Cookie 政策")

    # 先标为已发邮件(跳过实际发送)
    with patch("platform_app.email.RESEND_API_KEY", ""):
        dispatch_notice(db, record["id"])

    result = activate_notice(db, record["id"])
    assert result["activated_at"] is not None

    version = get_current_version(db, "cookie-policy")
    assert version == "v2.0"


def test_cron_dispatch_due_triggers_within_30d():
    """effective_at = now+29d 的 notice 应被 run_dispatch_due 触发。"""
    db = make_db()
    db.set_users([{"email": "u@example.com", "lang": "zh-CN"}])

    soon = datetime.now(timezone.utc) + timedelta(days=29)
    schedule_policy_change(db, "privacy-policy", "v1.3", "摘要", effective_at=soon)

    with patch("platform_app.email.RESEND_API_KEY", ""):
        result = run_dispatch_due(db)

    assert result["dispatched"] == 1


def test_cron_dispatch_due_skips_far_future():
    """effective_at = now+60d 的 notice 不应被 run_dispatch_due 触发。"""
    db = make_db()
    db.set_users([{"email": "u@example.com", "lang": "zh-CN"}])

    far = datetime.now(timezone.utc) + timedelta(days=60)
    schedule_policy_change(db, "privacy-policy", "v1.3", "摘要", effective_at=far)

    with patch("platform_app.email.RESEND_API_KEY", ""):
        result = run_dispatch_due(db)

    assert result["dispatched"] == 0


def test_cron_activate_due_activates_past_effective():
    """effective_at 已过且已发邮件的 notice 应被 run_activate_due 激活。"""
    db = make_db()
    db.set_users([])

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    record = schedule_policy_change(db, "terms-of-service", "v3.0", "摘要", effective_at=past)

    with patch("platform_app.email.RESEND_API_KEY", ""):
        run_dispatch_due(db)  # 会立即发(past < now+30d)

    result = run_activate_due(db)
    assert result["activated"] == 1
    assert get_current_version(db, "terms-of-service") == "v3.0"


def test_cron_activate_due_skips_undispatched():
    """effective_at 已过但 dispatched_at IS NULL 的 notice 不应被激活。"""
    db = make_db()
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    schedule_policy_change(db, "terms-of-service", "v3.0", "摘要", effective_at=past)

    # 不触发 dispatch
    result = run_activate_due(db)
    assert result["activated"] == 0


def test_build_email_zh():
    subject, body = _build_email(
        "privacy-policy", "v1.3", "修订数据保留期",
        (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        is_zh=True,
    )
    assert "隐私政策" in subject
    assert "v1.3" in subject
    assert "修订数据保留期" in body
    assert "stellatrix.icu/legal/privacy-policy" in body
    # 双语:body 末尾应含英文段落
    assert "Privacy Policy" in body


def test_build_email_en():
    subject, body = _build_email(
        "terms-of-service", "v2.0", "major update",
        (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        is_zh=False,
    )
    assert "Terms of Service" in subject
    assert "v2.0" in subject
    assert "major update" in body
    assert "stellatrix.icu/legal/terms-of-service" in body
    # 双语:body 末尾应含中文段落
    assert "服务条款" in body
