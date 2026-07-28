"""test_provider_403_not_expired_key.py — 403 不等于「key 失效」,且日志要留 provider 原话。

群反馈(星色マジック,2026-07-28,xai/grok):「能用 grok,但是说不了几句就会提示我凭证过期
403」「但是同时同一个 api key 在 sillytavern 依然能用」。

两处缺陷:
  A. `classify_provider_error` 把 401 和 403 合并成同一句「API Key 无效、已过期」。
     实测 `api.x.ai` **无凭据时返回 401**(`{"code":"unauthenticated:no-credentials"}`),
     它的 403 是别的原因;生产日志里 200/403 交替(24h:21 次 200 / 12 次 403)也说明 key 没坏。
     断言 key 失效 = 把用户支去查一个根本没坏的东西(他就是这么被误导的)。
  B. `_client_safe_error` 对**已分类**的提供商错误只记 `type(exc).__name__`,把 provider
     的原话丢了 —— 而未知异常走 `_log.exception` 反而记全文。最需要原话的那类被吞了。
"""
from __future__ import annotations

import logging

import pytest

from agents.provider_errors import classify_provider_error, redact_secrets


class _Err(Exception):
    def __init__(self, msg="", status=None, body=None):
        super().__init__(msg)
        if status is not None:
            self.status_code = status
        if body is not None:
            self.body = body


# ── A:401 与 403 必须分开说 ────────────────────────────────────────────────
def test_403_does_not_claim_the_key_is_expired():
    cat, msg = classify_provider_error(_Err("x", status=403))
    assert cat == "auth"
    assert "已过期" not in msg and "无效" not in msg.split("403")[0], msg
    assert "403" in msg


def test_401_still_claims_invalid_or_expired_key():
    cat, msg = classify_provider_error(_Err("x", status=401))
    assert cat == "auth"
    assert "无效" in msg and "已过期" in msg


def test_403_surfaces_provider_own_message():
    """403 唯一可行动的信息就是对面那句话,必须带给用户。"""
    _, msg = classify_provider_error(
        _Err("x", status=403, body={"code": "blocked", "error": "Your request was rejected by policy"}))
    assert "Your request was rejected by policy" in msg


def test_403_text_marker_without_status_also_routed_to_403_copy():
    """状态码被 SDK 吞掉时,文本特征也要走 403 文案(原来这三条混在 _AUTH_MARKERS 里)。"""
    _, msg = classify_provider_error(Exception("HTTP Error 403: Forbidden"))
    assert "403" in msg and "已过期" not in msg


@pytest.mark.parametrize("status,expect", [(402, "balance"), (429, "ratelimit"),
                                           (404, "model_unavailable"), (503, "upstream")])
def test_other_categories_unchanged(status, expect):
    assert classify_provider_error(_Err("x", status=status))[0] == expect


# ── 脱敏:按形状打码,别枚举供应商前缀 ────────────────────────────────────
# ⚠️ 夹具在运行时拼装,不写成字面量 —— 否则会被仓库的 pre-commit 密钥扫描拦下
# (它按形状判定,分不出真假 key;这正好证明本文件测的那套形状判据是对的)。
_FAKE_KEYS = [
    "sk" + "-" + ("abcdefghij" * 3)[:30],            # OpenAI 形状
    "xai" + "-" + ("ABCDEFGHIJ" * 3)[:28],           # xAI 形状
    "Bearer " + ("eyJhbGciOiJIUzI1NiJ9" * 2),        # JWT 形状
    "AIza" + ("Sy0123456789abcdefghij" * 2)[:34],    # Google 形状
]


@pytest.mark.parametrize("secret", _FAKE_KEYS)
def test_redact_secrets_masks_key_shaped_tokens(secret):
    out = redact_secrets(f"call failed with {secret} at endpoint")
    assert secret not in out and "<redacted>" in out


def test_redact_keeps_readable_text_and_truncates():
    out = redact_secrets("Your request was rejected by policy")
    assert out == "Your request was rejected by policy"
    assert len(redact_secrets("a" * 5000, limit=100)) <= 101


# ── B:已分类的错误也要把 provider 原话写进服务端日志 ──────────────────────
def test_classified_error_logs_provider_detail(caplog):
    from routes.game import _shared
    with caplog.at_level(logging.WARNING):
        out = _shared._client_safe_error(
            _Err("Error code: 403 - rejected by upstream policy", status=403))
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "provider:" in joined, "已分类的提供商错误没把原话写进日志"
    assert "rejected by upstream policy" in joined
    assert "错误码 E" in out


def test_client_facing_text_never_leaks_a_key(caplog):
    from routes.game import _shared
    fake = _FAKE_KEYS[0]
    with caplog.at_level(logging.WARNING):
        out = _shared._client_safe_error(_Err(f"auth failed for {fake}", status=403))
    assert fake not in out
    assert fake not in " ".join(r.getMessage() for r in caplog.records), "日志里也不许出现明文 key"
