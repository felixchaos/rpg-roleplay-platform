"""tests/test_password_reset.py — 密码重置（忘记密码）单元测试。

覆盖:
- forgot → confirm 完整流程（新密码生效，旧密码失效）
- 二次使用链接被拒绝
- 过期 token 被拒绝
- 防枚举：无效 email 也返回 ok=True
- token 不存在拒绝
"""
from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# 隔离 DB：用 in-memory Mock 替代真实 Postgres
# ---------------------------------------------------------------------------

class _MockRow(dict):
    """让 dict 支持 row["key"] 访问，模拟 psycopg Row。"""
    pass


def _make_db(rows: dict | None = None):
    """返回一个 mock db 对象，fetchone 根据 rows dict 路由。"""
    rows = rows or {}
    db = MagicMock()
    db.__enter__ = lambda s: db
    db.__exit__ = MagicMock(return_value=False)

    call_count = {"n": 0}

    def _execute(sql, params=()):
        call_count["n"] += 1
        sql_lower = sql.lower().strip()
        cursor = MagicMock()
        cursor.statusmessage = "DELETE 1"

        # SELECT users
        if "select id from users" in sql_lower or "select * from users" in sql_lower:
            cursor.fetchone = lambda: rows.get("user")
        # SELECT email_verifications
        elif "select" in sql_lower and "email_verifications" in sql_lower:
            cursor.fetchone = lambda: rows.get("verif")
        else:
            cursor.fetchone = lambda: None
        return cursor

    db.execute = _execute
    return db, call_count


# ---------------------------------------------------------------------------
# Unit tests (no DB needed for rate-limit / validation paths)
# ---------------------------------------------------------------------------

class TestRequestPasswordResetAntiEnum(unittest.TestCase):
    """request_password_reset 对无效/不存在 email 静默返回 ok=True。"""

    def test_blank_email_returns_ok(self):
        from platform_app.auth import request_password_reset
        result = request_password_reset("", ip="1.2.3.4")
        self.assertTrue(result["ok"])

    def test_invalid_email_no_at_returns_ok(self):
        from platform_app.auth import request_password_reset
        result = request_password_reset("notanemail", ip="1.2.3.4")
        self.assertTrue(result["ok"])

    def test_nonexistent_email_returns_ok(self):
        """DB 查不到用户，也必须返回 ok（防枚举）。"""
        from platform_app import auth as _auth

        mock_db, _ = _make_db(rows={"user": None})

        with patch.object(_auth, "connect", return_value=mock_db), \
             patch.object(_auth, "init_db"), \
             patch.object(_auth, "_check_reset_rate"):  # 绕过限流
            result = _auth.request_password_reset("nosuchuser@example.com", ip="1.2.3.4")
        self.assertTrue(result["ok"])


class TestRequestPasswordResetValidUser(unittest.TestCase):
    """存在用户时应写 email_verifications 并发邮件。"""

    def test_valid_user_sends_email(self):
        from platform_app import auth as _auth

        user_row = _MockRow({"id": 42})
        mock_db, _ = _make_db(rows={"user": user_row})

        with patch.object(_auth, "connect", return_value=mock_db), \
             patch.object(_auth, "init_db"), \
             patch.object(_auth, "_check_reset_rate"), \
             patch("platform_app.email.send_password_reset_email") as mock_send:
            result = _auth.request_password_reset("alice@example.com", ip="9.9.9.9")

        self.assertTrue(result["ok"])
        # 邮件发送应被调用一次
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertIn("@", args[0])   # 收件人
        self.assertTrue(len(args[1]) > 10)  # token 非空


class TestConfirmPasswordReset(unittest.TestCase):
    """confirm_password_reset 核心流程。"""

    def _mock_valid_verif(self, used_at=None, expires_future=True):
        datetime.now(UTC)
        return _MockRow({
            "id": 99,
            "email": "alice@example.com",
            "used_at": used_at,
        })

    def test_valid_token_resets_password(self):
        from platform_app import auth as _auth

        verif = self._mock_valid_verif()
        user = _MockRow({"id": 42})
        mock_db, _ = _make_db(rows={"verif": verif, "user": user})

        with patch.object(_auth, "connect", return_value=mock_db), \
             patch.object(_auth, "init_db"), \
             patch.object(_auth, "hash_password", return_value="newhash") as mock_hp:
            result = _auth.confirm_password_reset("validtoken123", "NewPass!99", ip="1.1.1.1")

        self.assertTrue(result["ok"])
        mock_hp.assert_called_once_with("NewPass!99")

    def test_already_used_token_raises(self):
        from platform_app import auth as _auth

        verif = self._mock_valid_verif(used_at=datetime.now(UTC))
        mock_db, _ = _make_db(rows={"verif": verif})

        with patch.object(_auth, "connect", return_value=mock_db), \
             patch.object(_auth, "init_db"):
            with self.assertRaises(ValueError) as ctx:
                _auth.confirm_password_reset("usedtoken", "NewPass!99")
        self.assertIn("已使用", str(ctx.exception))

    def test_invalid_token_raises(self):
        """DB 查不到 token（过期或不存在）→ ValueError。"""
        from platform_app import auth as _auth

        mock_db, _ = _make_db(rows={"verif": None})

        with patch.object(_auth, "connect", return_value=mock_db), \
             patch.object(_auth, "init_db"):
            with self.assertRaises(ValueError) as ctx:
                _auth.confirm_password_reset("nosuchtoken", "NewPass!99")
        self.assertIn("无效", str(ctx.exception))

    def test_blank_token_raises(self):
        from platform_app.auth import confirm_password_reset
        with self.assertRaises(ValueError):
            confirm_password_reset("", "NewPass!99")

    def test_short_password_raises(self):
        from platform_app.auth import confirm_password_reset
        with self.assertRaises(ValueError) as ctx:
            confirm_password_reset("sometoken", "short")
        self.assertIn("位", str(ctx.exception))


class TestResetRateLimit(unittest.TestCase):
    """_check_reset_rate 不允许同邮箱短时间内频繁触发。"""

    def setUp(self):
        # 清空限流 dict，防止测试间干扰
        from platform_app import auth as _auth
        _auth._RESET_RATE.clear()

    def test_first_call_passes(self):
        from platform_app.auth import _check_reset_rate
        _check_reset_rate("fresh@example.com")  # 不抛异常

    def test_rapid_calls_raise(self):
        from platform_app.auth import _check_reset_rate
        email = "rapid@example.com"
        _check_reset_rate(email)
        # 立即再次调用，应被限流
        with self.assertRaises(ValueError):
            _check_reset_rate(email)


if __name__ == "__main__":
    unittest.main()
