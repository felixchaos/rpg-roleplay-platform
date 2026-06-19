"""platform_app.turnstile 单元测试 — Cloudflare Turnstile 人机验证门控。

覆盖 fail-safe 契约:
  - 未配置 secret → 整体关闭（enabled=False，verify 放行）
  - 配置 secret 后 → 空 token 拒绝；按 Cloudflare 返回 success 判定
"""
from __future__ import annotations

import json
import io
from unittest import mock

import pytest

from platform_app import turnstile as ts


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("RPG_TURNSTILE_SECRET", raising=False)
    monkeypatch.delenv("RPG_TURNSTILE_SITEKEY", raising=False)
    yield


def test_disabled_when_no_secret():
    assert ts.enabled() is False
    # 关闭态:任何 token（含空）都放行,不触网
    assert ts.verify("") is True
    assert ts.verify("whatever") is True


def test_sitekey_passthrough(monkeypatch):
    assert ts.sitekey() == ""
    monkeypatch.setenv("RPG_TURNSTILE_SITEKEY", "0xABC")
    assert ts.sitekey() == "0xABC"


def test_enabled_empty_token_rejected_without_network(monkeypatch):
    monkeypatch.setenv("RPG_TURNSTILE_SECRET", "sek")
    assert ts.enabled() is True
    # 空 token 在触网前就拒,确保不浪费一次 Cloudflare 调用
    with mock.patch("urllib.request.urlopen", side_effect=AssertionError("should not call")):
        assert ts.verify("") is False
        assert ts.verify("   ") is False


def _fake_resp(payload: dict):
    body = json.dumps(payload).encode()
    cm = mock.MagicMock()
    cm.__enter__.return_value = io.BytesIO(body)
    cm.__exit__.return_value = False
    return cm


def test_enabled_verify_success(monkeypatch):
    monkeypatch.setenv("RPG_TURNSTILE_SECRET", "sek")
    with mock.patch("urllib.request.urlopen", return_value=_fake_resp({"success": True})):
        assert ts.verify("good-token", ip="1.2.3.4") is True


def test_enabled_verify_failure(monkeypatch):
    monkeypatch.setenv("RPG_TURNSTILE_SECRET", "sek")
    with mock.patch("urllib.request.urlopen", return_value=_fake_resp({"success": False, "error-codes": ["invalid-input-response"]})):
        assert ts.verify("bad-token") is False


def test_enabled_network_error_fail_closed(monkeypatch):
    monkeypatch.setenv("RPG_TURNSTILE_SECRET", "sek")
    with mock.patch("urllib.request.urlopen", side_effect=OSError("network down")):
        # fail-closed:网络异常时拒绝,宁可挡真人也不放过机器人
        assert ts.verify("token") is False
