"""
test_register_consent.py — 注册时 terms_accepted / age_confirmed 合规校验测试

case 1: 不传 terms_accepted → 400 + error_key auth.terms_not_accepted
case 2: 不传 age_confirmed → 400 + error_key auth.age_not_confirmed
case 3: 都传 true → 注册成功（复用 register_user，验证 ok: True）
case 4: confirm_email_verification 写入 users 时 terms_accepted_at IS NOT NULL + age_confirmed = true
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.helpers import cleanup_test_users, integtest_username, make_client


@pytest.fixture(scope="module")
def client():
    return make_client()


@pytest.fixture(autouse=True, scope="module")
def _cleanup():
    yield
    cleanup_test_users()


def _base_body(username: str) -> dict:
    return {
        "username": username,
        "password": "Test12345!",
        "display_name": "consent_test",
        "terms_accepted": True,
        "age_confirmed": True,
    }


def test_missing_terms_accepted_returns_400(client):
    body = _base_body(integtest_username())
    body.pop("terms_accepted")
    resp = client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 400, f"期待 400，实际 {resp.status_code}"
    detail = resp.json().get("detail", {})
    assert detail.get("error_key") == "auth.terms_not_accepted", f"error_key 不符: {detail}"


def test_missing_age_confirmed_returns_400(client):
    body = _base_body(integtest_username())
    body.pop("age_confirmed")
    resp = client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 400, f"期待 400，实际 {resp.status_code}"
    detail = resp.json().get("detail", {})
    assert detail.get("error_key") == "auth.age_not_confirmed", f"error_key 不符: {detail}"


def test_both_consents_true_registers_successfully(client):
    body = _base_body(integtest_username())
    resp = client.post("/api/v1/auth/register", json=body)
    assert resp.status_code == 200, f"期待 200，实际 {resp.status_code}; body={resp.text}"
    j = resp.json()
    assert j.get("ok") is True, f"期待 ok: true，实际: {j}"


# ─────────────────────────────────────────────────────────────────────────────
# case 4: confirm_email_verification 写 DB 时持久化 terms_accepted_at + age_confirmed
# ─────────────────────────────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _CapturingConn:
    """记录所有 INSERT INTO users 调用的 SQL 和参数。"""

    def __init__(self, verif_row, user_row):
        self._verif_row = verif_row
        self._user_row = user_row
        self.insert_users_calls: list[dict] = []

    def execute(self, sql: str, params=None):
        s = sql.strip().lower()
        if "select" in s and "email_verifications" in s:
            return _FakeCursor([self._verif_row])
        if "update email_verifications" in s:
            return _FakeCursor([])
        if "insert into users" in s:
            self.insert_users_calls.append({"sql": sql, "params": list(params or [])})
            return _FakeCursor([self._user_row])
        if "invite_codes" in s:
            return _FakeCursor([])
        if "sessions" in s:
            return _FakeCursor([])
        return _FakeCursor([])

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def test_confirm_email_verification_persists_consent_fields(monkeypatch):
    """Phase 2 (confirm_email_verification) 写 users 行时：
    - terms_accepted_at 参数为 True（SQL 中 CASE WHEN %s THEN now()）
    - age_confirmed 参数为 True
    """
    import platform_app.auth as auth_mod
    from platform_app.security import hash_email_code

    # 清理 pending cache
    auth_mod._PENDING_REGISTER.clear()

    email_norm = "consent_persist@example.com"
    real_code = "654321"
    code_hash = hash_email_code(real_code)

    verif_row = {
        "id": 10,
        "email": email_norm,
        "code_hash": code_hash,
        "purpose": "register",
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        "used_at": None,
    }
    user_row = {
        "id": 55, "username": "consentuser", "display_name": "consentuser",
        "role": "user", "email": email_norm, "email_verified": True,
        "email_verified_at": datetime.now(timezone.utc),
        "birthday": date(1995, 6, 15),
        "password_hash": "$argon2id$fake",
        "public_id": None, "bio": "", "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc), "row_version": 1,
        "deactivated_at": None, "ban_reason": "",
        "terms_accepted_at": datetime.now(timezone.utc),
        "age_confirmed": True,
    }

    # 预置 pending 注册参数（terms_accepted=True, age_confirmed=True）
    auth_mod._PENDING_REGISTER[email_norm] = json.dumps({
        "username": "consentuser",
        "password_hash": "$argon2id$fake",
        "display_name": "consentuser",
        "birthday": "1995-06-15",
        "terms_accepted": True,
        "age_confirmed": True,
        "invite_code": None,
        "setup_token": None,
    })

    capturing_conn = _CapturingConn(verif_row, user_row)
    monkeypatch.setattr(auth_mod, "init_db", lambda: None)
    monkeypatch.setattr(auth_mod, "connect", lambda: capturing_conn)

    user, token = auth_mod.confirm_email_verification(email_norm, real_code)

    # 验证至少触发了一次 INSERT INTO users
    assert capturing_conn.insert_users_calls, "期望 INSERT INTO users 被调用"

    call = capturing_conn.insert_users_calls[0]
    params = call["params"]

    # SQL 使用 CASE WHEN %s THEN now() ELSE null END 处理 terms_accepted_at:
    # 参数顺序: username, password_hash, display_name, email, birthday, terms_accepted(bool), age_confirmed(bool)
    # terms_accepted 对应 True，age_confirmed 对应 True
    assert True in params, f"期望 True(terms_accepted) 在 INSERT 参数中，实际: {params}"
    # 统计 True 值：terms_accepted 和 age_confirmed 各一个，共至少 2 个
    true_count = sum(1 for p in params if p is True)
    assert true_count >= 2, (
        f"期望 INSERT 参数含 >=2 个 True（terms_accepted + age_confirmed），"
        f"实际 True 个数: {true_count}, 全参数: {params}"
    )

    # 验证返回的 user dict 中字段正确
    assert user["terms_accepted_at"] is not None, "user.terms_accepted_at 应不为 None"
    assert user["age_confirmed"] is True, "user.age_confirmed 应为 True"
    assert len(token) > 20, "应颁发有效 session token"
