"""跨渠道 fallback v0 单测(docs/design/channel_fallback_v0.md)。

三层:①resolve_fallback_channel 候选解析;②stream_with_channel_fallback 组合包装器
触发四连判;③源码守卫(接线在生产路径+知情事件存在)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import core.channel_fallback as cf  # noqa: E402
from agents.gm.stream_retry import stream_with_channel_fallback  # noqa: E402


class _Exc502(Exception):
    status_code = 502


class _Exc402(Exception):
    status_code = 402


# ── ① 候选解析 ─────────────────────────────────────────────────────────

def _patch_resolver(monkeypatch, *, creds, degraded=frozenset(), models=None, pref=""):
    import model_probe
    from core import llm_backend
    from platform_app import user_credentials

    monkeypatch.setattr(user_credentials, "list_credentials",
                        lambda uid: {"ok": True, "items": creds})
    monkeypatch.setattr(model_probe, "is_channel_degraded",
                        lambda api_id, clock=None: api_id in degraded)
    monkeypatch.setattr(llm_backend, "first_user_model",
                        lambda uid, api_id=None: (models or {}).get(api_id))
    import core.request_cache as rc
    monkeypatch.setattr(rc, "get_user_prefs_cached",
                        lambda uid: {"gm.fallback_api_id": pref} if pref else {})


def _cred(api_id, enabled=True, has_key=True):
    return {"api_id": api_id, "enabled": enabled, "has_credential": has_key}


def test_resolver_no_creds_returns_none(monkeypatch):
    _patch_resolver(monkeypatch, creds=[])
    assert cf.resolve_fallback_channel(1, "deepseek") is None


def test_resolver_excludes_failed_channel(monkeypatch):
    _patch_resolver(monkeypatch, creds=[_cred("deepseek")],
                    models={"deepseek": ("deepseek", "dv4")})
    assert cf.resolve_fallback_channel(1, "deepseek") is None


def test_resolver_skips_degraded(monkeypatch):
    _patch_resolver(monkeypatch,
                    creds=[_cred("openai"), _cred("anthropic")],
                    degraded={"openai"},
                    models={"openai": ("openai", "gpt"), "anthropic": ("anthropic", "claude")})
    assert cf.resolve_fallback_channel(1, "deepseek") == ("anthropic", "claude")


def test_resolver_pref_priority(monkeypatch):
    _patch_resolver(monkeypatch,
                    creds=[_cred("openai"), _cred("anthropic")],
                    models={"openai": ("openai", "gpt"), "anthropic": ("anthropic", "claude")},
                    pref="anthropic")
    assert cf.resolve_fallback_channel(1, "deepseek") == ("anthropic", "claude")


def test_resolver_skips_channel_without_model(monkeypatch):
    _patch_resolver(monkeypatch,
                    creds=[_cred("openai"), _cred("anthropic")],
                    models={"anthropic": ("anthropic", "claude")})  # openai 解析不出模型
    assert cf.resolve_fallback_channel(1, "deepseek") == ("anthropic", "claude")


# ── ② 组合包装器 ────────────────────────────────────────────────────────

def _gen_fail(exc):
    def g():
        raise exc
        yield  # pragma: no cover
    return g


def _gen_ok(*events):
    def g():
        yield from events
    return g


def _wire(monkeypatch, *, flag=True, candidate=("anthropic", "claude")):
    import core.feature_flags as ff
    monkeypatch.setattr(ff, "feature_enabled",
                        lambda key, uid=None: flag if key == "channel_fallback" else False)
    monkeypatch.setattr(cf, "resolve_fallback_channel", lambda uid, ex: candidate)


def test_fallback_switches_on_uncommitted_upstream(monkeypatch):
    _wire(monkeypatch)
    made = {}

    def make_backup(api, model):
        made["cand"] = (api, model)
        return _gen_ok({"type": "text", "text": "备用正文"})

    out = list(stream_with_channel_fallback(
        _gen_fail(_Exc502("gw")), user_id=1, primary_api_id="deepseek",
        make_backup_factory=make_backup, sleep=lambda _s: None,
    ))
    assert made["cand"] == ("anthropic", "claude")
    notices = [e for e in out if e.get("type") == "fallback_notice"]
    assert len(notices) == 1 and notices[0]["api_id"] == "anthropic"
    assert {"type": "text", "text": "备用正文"} in out


def test_no_fallback_after_commit(monkeypatch):
    _wire(monkeypatch)

    def gen():
        yield {"type": "text", "text": "已开写"}
        raise _Exc502("mid")

    with pytest.raises(_Exc502):
        list(stream_with_channel_fallback(
            gen, user_id=1, primary_api_id="deepseek",
            make_backup_factory=lambda a, m: _gen_ok(), sleep=lambda _s: None,
        ))


def test_no_fallback_when_flag_off(monkeypatch):
    _wire(monkeypatch, flag=False)
    with pytest.raises(_Exc502):
        list(stream_with_channel_fallback(
            _gen_fail(_Exc502("gw")), user_id=1, primary_api_id="deepseek",
            make_backup_factory=lambda a, m: _gen_ok(), sleep=lambda _s: None,
        ))


def test_no_fallback_on_non_retryable(monkeypatch):
    _wire(monkeypatch)
    with pytest.raises(_Exc402):
        list(stream_with_channel_fallback(
            _gen_fail(_Exc402("no balance")), user_id=1, primary_api_id="deepseek",
            make_backup_factory=lambda a, m: _gen_ok(), sleep=lambda _s: None,
        ))


def test_no_candidate_reraises_original(monkeypatch):
    _wire(monkeypatch, candidate=None)
    with pytest.raises(_Exc502):
        list(stream_with_channel_fallback(
            _gen_fail(_Exc502("gw")), user_id=1, primary_api_id="deepseek",
            make_backup_factory=lambda a, m: _gen_ok(), sleep=lambda _s: None,
        ))


def test_backup_failure_reraises(monkeypatch):
    _wire(monkeypatch)
    with pytest.raises(_Exc502):
        list(stream_with_channel_fallback(
            _gen_fail(_Exc502("primary")), user_id=1, primary_api_id="deepseek",
            make_backup_factory=lambda a, m: _gen_fail(_Exc502("backup-dead")),
            sleep=lambda _s: None,
        ))


# ── ③ 源码守卫 ─────────────────────────────────────────────────────────

def test_wired_in_gm_phase_and_notice_handled():
    src = "\n".join(_p.read_text(encoding="utf-8") for _p in sorted((Path(__file__).resolve().parents[2] / "chat_pipeline").glob("*.py")))
    assert "stream_with_channel_fallback" in src, "GM 主流必须走跨渠道 fallback 包装器"
    assert 'etype == "fallback_notice"' in src, "事件循环必须处理 fallback_notice"
    assert "gm_fallback" in src
    assert "fallback_note" in src, "done 前 updates 必须附带备用模型知情标注"
