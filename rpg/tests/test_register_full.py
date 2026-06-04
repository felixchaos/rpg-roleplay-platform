"""test_register_full.py — REG-01..04 + AGE-01 + ENC-08 全流程测试。

运行前提:
  - 测试 DB: DATABASE_URL 指向测试 Postgres，或 SQLite（若 connect() 支持）
  - 无需真实 Resend key（send_verification_email 会被 mock）

测试用例:
  1. 完整两步 register → verify-email 成功并颁 session
  2. <18 岁拒绝 (AGE-01)
  3. banned 邮箱拒绝 (REG-04)
  4. invite_code 模式无 invite_code 拒绝
  5. code 错误拒绝、过期拒绝
  6. 老 PBKDF2 账号 login 后 password_hash 升级为 Argon2id (ENC-08)
"""
from __future__ import annotations

import importlib
import json

# ── 环境变量 mock（须在 import 前设置）────────────────────────────────────────
import os
from datetime import UTC, date, datetime, timedelta

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/stellatrix_test")
os.environ.setdefault("EMAIL_CODE_SECRET", "test-secret-key-for-pytest")

# ── 延迟 import 以便 mock 生效 ─────────────────────────────────────────────────

def _auth():
    import platform_app.auth as m
    importlib.reload(m)
    return m


def _security():
    import platform_app.security as m
    return m


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clear_pending(monkeypatch):
    """每个测试清理进程内 pending cache。"""
    import platform_app.auth as auth_mod
    auth_mod._PENDING_REGISTER.clear()
    auth_mod._RESEND_LAST.clear()
    yield
    auth_mod._PENDING_REGISTER.clear()
    auth_mod._RESEND_LAST.clear()


@pytest.fixture()
def mock_email(monkeypatch):
    """mock send_verification_email，捕获发送的 code。"""
    sent: list[dict] = []

    def fake_send(to, code, lang="zh-CN"):
        sent.append({"to": to, "code": code})

    monkeypatch.setattr("platform_app.email.send_verification_email", fake_send)
    # 也 patch 在 auth 模块内 import 的引用
    # auth.register 内 lazy import，直接 patch email module
    import platform_app.email as email_mod
    monkeypatch.setattr(email_mod, "send_verification_email", fake_send)
    return sent


@pytest.fixture()
def mock_db_open(monkeypatch, tmp_path):
    """用内存字典 mock DB 连接（不连真实 Postgres）。

    复杂查询用 patch auth.connect / init_db。
    这里使用集成测试方式：若 DATABASE_URL 指向真实 DB 则直连；
    否则用 psycopg mock。
    """
    # 本 fixture 仅用于控制是否需要真 DB。
    # 若 CI 无 DB，可扩展为 SQLite mock。
    pass


# ─────────────────────────────────────────────────────────────────────────────
# 辅助：绕过真实 DB 的轻量单元测试
# ─────────────────────────────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []
        self._row_idx = 0

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    """极简 DB mock，按 SQL 前缀路由返回预设行。"""

    def __init__(self, presets: dict):
        self._presets = presets  # sql_fragment → list[dict] or None
        self._executed: list[str] = []

    def execute(self, sql: str, params=None):
        self._executed.append(sql.strip()[:80])
        for fragment, rows in self._presets.items():
            if fragment in sql:
                return _FakeCursor(rows)
        return _FakeCursor([])

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 测试 1: 完整两步注册
# ─────────────────────────────────────────────────────────────────────────────

def test_register_and_verify_success(monkeypatch, mock_email):
    """两步注册正常流程：register 返回 pending_verify，confirm 颁 session。"""
    import platform_app.auth as auth_mod
    from platform_app.security import hash_email_code

    # Step 1: mock DB — 没有 banned，没有重名，commit 成功
    def fake_connect():
        conn = _FakeConn({
            "banned_users": [],
            "users where lower(email)": [],
            "users where username": [],
            "app_config": [],
            "email_verifications": [],
            "update email_verifications": [],
            "insert into email_verifications": [],
        })
        return conn

    monkeypatch.setattr(auth_mod, "connect", fake_connect)
    monkeypatch.setattr(auth_mod, "init_db", lambda: None)

    result = auth_mod.register(
        username="alice",
        password="SecurePass123!",
        email="alice@example.com",
        birthday=date(2000, 1, 1),
        terms_accepted=True,
        age_confirmed=True,
    )
    assert result["ok"] is True
    assert result["pending_verify"] is True
    assert "email_mask" in result
    assert mock_email, "期望已发送验证码邮件"

    code = mock_email[0]["code"]
    assert len(code) == 6 and code.isdigit()

    # Step 2: confirm_email_verification
    email_norm = "alice@example.com"
    code_hash = hash_email_code(code)
    fake_verif_row = {
        "id": 1,
        "email": email_norm,
        "code_hash": code_hash,
        "purpose": "register",
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
        "used_at": None,
    }
    fake_user_row = {
        "id": 42, "username": "alice", "display_name": "alice",
        "role": "user", "email": email_norm, "email_verified": True,
        "email_verified_at": datetime.now(UTC),
        "birthday": date(2000, 1, 1),
        "password_hash": auth_mod._PENDING_REGISTER.get(email_norm, "{}"),
        "public_id": None, "bio": "", "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC), "row_version": 1,
        "deactivated_at": None, "ban_reason": "",
        "terms_accepted_at": datetime.now(UTC), "age_confirmed": True,
    }

    class _FakeConnVerify:
        def __init__(self):
            self._executed = []

        def execute(self, sql, params=None):
            s = sql.strip()
            self._executed.append(s[:80])
            if "email_verifications" in s and "select" in s.lower():
                return _FakeCursor([fake_verif_row])
            if "insert into users" in s:
                return _FakeCursor([fake_user_row])
            if "invite_codes" in s:
                return _FakeCursor([])
            if "sessions" in s:
                return _FakeCursor([])
            return _FakeCursor([])

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(auth_mod, "connect", _FakeConnVerify)

    user, token = auth_mod.confirm_email_verification("alice@example.com", code)
    assert user["id"] == 42
    assert len(token) > 20


# ─────────────────────────────────────────────────────────────────────────────
# 测试 2: <18 岁拒绝 (AGE-01)
# ─────────────────────────────────────────────────────────────────────────────

def test_register_underage_rejected(monkeypatch, mock_email):
    import platform_app.auth as auth_mod

    monkeypatch.setattr(auth_mod, "init_db", lambda: None)
    monkeypatch.setattr(auth_mod, "connect", lambda: _FakeConn({}))

    today = date.today()
    birthday_17 = date(today.year - 17, today.month, today.day)

    with pytest.raises(ValueError, match="18"):
        auth_mod.register(
            username="youngster",
            password="SecurePass123!",
            email="young@example.com",
            birthday=birthday_17,
            terms_accepted=True,
            age_confirmed=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 测试 3: banned 邮箱拒绝 (REG-04)
# ─────────────────────────────────────────────────────────────────────────────

def test_register_banned_email_rejected(monkeypatch, mock_email):
    import platform_app.auth as auth_mod

    monkeypatch.setattr(auth_mod, "init_db", lambda: None)
    # banned_users 返回一行 → 命中
    monkeypatch.setattr(auth_mod, "connect", lambda: _FakeConn({
        "banned_users": [{"id": 1}],
    }))

    with pytest.raises(ValueError, match="限制"):
        auth_mod.register(
            username="badactor",
            password="SecurePass123!",
            email="banned@example.com",
            birthday=date(1990, 1, 1),
            terms_accepted=True,
            age_confirmed=True,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 测试 4: invite_code 模式无 invite_code 拒绝
# ─────────────────────────────────────────────────────────────────────────────

def test_register_invite_mode_no_code_rejected(monkeypatch, mock_email):
    import platform_app.auth as auth_mod

    monkeypatch.setattr(auth_mod, "init_db", lambda: None)
    # app_config 返回 invite 模式
    cfg_row = {"value": json.dumps({"mode": "invite"})}
    monkeypatch.setattr(auth_mod, "connect", lambda: _FakeConn({
        "banned_users": [],
        "users where lower(email)": [],
        "users where username": [],
        "app_config": [cfg_row],
    }))

    with pytest.raises(ValueError, match="邀请"):
        auth_mod.register(
            username="newuser",
            password="SecurePass123!",
            email="newuser@example.com",
            birthday=date(1995, 5, 15),
            terms_accepted=True,
            age_confirmed=True,
            invite_code=None,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 测试 5: code 错误拒绝、过期拒绝
# ─────────────────────────────────────────────────────────────────────────────

def test_verify_wrong_code_rejected(monkeypatch):
    import platform_app.auth as auth_mod
    from platform_app.security import hash_email_code

    monkeypatch.setattr(auth_mod, "init_db", lambda: None)

    email_norm = "user@example.com"
    real_code = "123456"
    code_hash = hash_email_code(real_code)
    fake_verif_row = {
        "id": 99,
        "email": email_norm,
        "code_hash": code_hash,
        "purpose": "register",
        "expires_at": datetime.now(UTC) + timedelta(minutes=5),
        "used_at": None,
    }
    # 放入 pending
    auth_mod._PENDING_REGISTER[email_norm] = json.dumps({
        "username": "user", "password_hash": "pbkdf2_sha256$salt$hash",
        "display_name": "user", "birthday": "1995-01-01",
        "terms_accepted": True, "age_confirmed": True,
        "invite_code": None, "setup_token": None,
    })

    monkeypatch.setattr(auth_mod, "connect", lambda: _FakeConn({
        "email_verifications": [fake_verif_row],
    }))

    with pytest.raises(ValueError, match="错误"):
        auth_mod.confirm_email_verification(email_norm, "999999")


def test_verify_expired_code_rejected(monkeypatch):
    import platform_app.auth as auth_mod

    monkeypatch.setattr(auth_mod, "init_db", lambda: None)
    # 没有有效 verif 行（过期）→ fetchone 返回 None
    monkeypatch.setattr(auth_mod, "connect", lambda: _FakeConn({
        "email_verifications": [],  # 空 → fetchone None
    }))

    with pytest.raises(ValueError, match="过期"):
        auth_mod.confirm_email_verification("user@example.com", "123456")


# ─────────────────────────────────────────────────────────────────────────────
# 测试 6: 老 PBKDF2 登录后 rehash 为 Argon2id (ENC-08)
# ─────────────────────────────────────────────────────────────────────────────

def test_login_pbkdf2_rehashed_to_argon2id(monkeypatch):
    # 生成一个真实 PBKDF2 哈希
    import hashlib
    import secrets as _sec

    import platform_app.auth as auth_mod
    salt = _sec.token_hex(16)
    plaintext = "OldPassword!"
    digest = hashlib.pbkdf2_hmac("sha256", plaintext.encode(), salt.encode(), 180_000).hex()
    pbkdf2_stored = f"pbkdf2_sha256${salt}${digest}"

    assert pbkdf2_stored.startswith("pbkdf2")

    updated_hash: list[str] = []

    class _FakeConnLogin:
        def execute(self, sql, params=None):
            s = sql.strip().lower()
            if "select * from users where username" in s:
                return _FakeCursor([{
                    "id": 7, "username": "olduser", "password_hash": pbkdf2_stored,
                    "role": "user", "email": "", "email_verified": False,
                    "public_id": None, "display_name": "old", "bio": "",
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                    "row_version": 1, "deactivated_at": None,
                }])
            if "update users set password_hash" in s:
                updated_hash.append(params[0] if params else "?")
                return _FakeCursor([])
            if "select count(*)" in s:
                return _FakeCursor([{"n": 0}])
            if "insert into sessions" in s:
                return _FakeCursor([])
            return _FakeCursor([])

        def __enter__(self): return self
        def __exit__(self, *_): pass

    monkeypatch.setattr(auth_mod, "init_db", lambda: None)
    monkeypatch.setattr(auth_mod, "connect", _FakeConnLogin)
    # 跳过速率限制
    monkeypatch.setattr(auth_mod, "_check_rate_limit", lambda ip, u: None)
    monkeypatch.setattr(auth_mod, "_record_login_success", lambda ip, u: None)

    user, token = auth_mod.login("olduser", plaintext, ip="127.0.0.1")
    assert user["id"] == 7
    # 验证 rehash 被触发且新 hash 为 Argon2id 格式
    assert updated_hash, "期望 UPDATE password_hash 被调用"
    assert updated_hash[0].startswith("$argon2"), f"期望 argon2id 格式，实际: {updated_hash[0][:30]}"


# ─────────────────────────────────────────────────────────────────────────────
# 测试: security 模块单元
# ─────────────────────────────────────────────────────────────────────────────

def test_normalize_email_strips_tag():
    from platform_app.security import normalize_email
    assert normalize_email("User+tag@Example.COM") == "user@example.com"


def test_calc_age_exactly_18():
    from platform_app.security import calc_age
    today = date.today()
    birthday_18 = date(today.year - 18, today.month, today.day)
    assert calc_age(birthday_18) == 18


def test_calc_age_just_under_18():
    from platform_app.security import calc_age
    today = date.today()
    # 生日在明天（未满18）
    birthday = date(today.year - 18, today.month, today.day) + timedelta(days=1)
    assert calc_age(birthday) == 17


def test_verify_password_with_rehash_argon2():
    from platform_app.security import hash_password, verify_password_with_rehash
    h = hash_password("mypassword")
    if h.startswith("$argon2"):
        ok, needs_rehash = verify_password_with_rehash(h, "mypassword")
        assert ok
        assert not needs_rehash  # 新 argon2id 不需 rehash
    elif h.startswith("pbkdf2"):
        ok, needs_rehash = verify_password_with_rehash(h, "mypassword")
        assert ok
        assert needs_rehash  # PBKDF2 需要 rehash（argon2-cffi 未安装）


def test_verify_password_with_rehash_allows_passwordless_user():
    from platform_app.security import verify_password_with_rehash
    assert verify_password_with_rehash(None, "password") == (False, False)


def test_generate_email_code():
    from platform_app.security import generate_email_code
    code = generate_email_code(6)
    assert len(code) == 6
    assert code.isdigit()
