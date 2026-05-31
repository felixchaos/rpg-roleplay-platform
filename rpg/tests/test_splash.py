"""
test_splash.py — AGE-02 splash ack 端点集成测试

cases:
  1. 未 ack → /status 返 acked: false
  2. ack 后 /status 返 acked: true
  3. 错版本 ack → 400
  4. 同用户 ack 两次幂等 (on conflict do nothing → ok: true, 不报错)
"""
from __future__ import annotations

import pytest

from tests.helpers import cleanup_test_users, make_client, register_user

SPLASH_VERSION = "v1.0-2026-05-31"


@pytest.fixture(scope="module")
def client():
    return make_client()


@pytest.fixture(autouse=True, scope="module")
def _cleanup():
    yield
    cleanup_test_users()


def _login(client, username, password="Test12345!") -> dict:
    """Login and return cookies dict."""
    resp = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert resp.status_code == 200, f"登录失败 {resp.status_code}: {resp.text}"
    return dict(resp.cookies)


# --------------------------------------------------------------------------- #

def test_status_before_ack_returns_false(client):
    """未 ack 时 /status 应返回 acked: false。"""
    reg = register_user(client)
    assert reg["status"] == 200, f"注册失败: {reg}"
    cookies = _login(client, reg["username"])

    resp = client.get("/api/me/splash/status", cookies=cookies)
    assert resp.status_code == 200, f"status failed: {resp.text}"
    j = resp.json()
    assert j["acked"] is False, f"期待 acked=false，实际: {j}"
    assert j["current_version"] == SPLASH_VERSION


def test_ack_then_status_returns_true(client):
    """ack 后 /status 应返回 acked: true。"""
    reg = register_user(client)
    cookies = _login(client, reg["username"])

    ack = client.post(
        "/api/me/splash/ack",
        json={"splash_version": SPLASH_VERSION},
        cookies=cookies,
    )
    assert ack.status_code == 200, f"ack failed: {ack.text}"
    assert ack.json()["ok"] is True

    status = client.get("/api/me/splash/status", cookies=cookies)
    assert status.status_code == 200
    j = status.json()
    assert j["acked"] is True, f"期待 acked=true，实际: {j}"
    assert j["acked_at"] is not None


def test_stale_version_ack_returns_400(client):
    """错版本 splash_version 应返回 400。"""
    reg = register_user(client)
    cookies = _login(client, reg["username"])

    resp = client.post(
        "/api/me/splash/ack",
        json={"splash_version": "v0.0-old"},
        cookies=cookies,
    )
    assert resp.status_code == 400, f"期待 400，实际 {resp.status_code}: {resp.text}"


def test_double_ack_is_idempotent(client):
    """同用户连续 ack 两次应幂等，不报错。"""
    reg = register_user(client)
    cookies = _login(client, reg["username"])

    for _ in range(2):
        resp = client.post(
            "/api/me/splash/ack",
            json={"splash_version": SPLASH_VERSION},
            cookies=cookies,
        )
        assert resp.status_code == 200, f"ack failed: {resp.text}"
        assert resp.json()["ok"] is True

    status = client.get("/api/me/splash/status", cookies=cookies)
    assert status.json()["acked"] is True
