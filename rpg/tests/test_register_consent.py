"""
test_register_consent.py — 注册时 terms_accepted / age_confirmed 合规校验测试

case 1: 不传 terms_accepted → 400 + error_key auth.terms_not_accepted
case 2: 不传 age_confirmed → 400 + error_key auth.age_not_confirmed
case 3: 都传 true → 注册成功（复用 register_user，验证 ok: True）
"""
from __future__ import annotations

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
