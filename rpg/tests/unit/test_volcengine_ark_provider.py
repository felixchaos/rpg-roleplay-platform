"""test_volcengine_ark_provider.py — 火山方舟(Volcengine Ark)接入。

群反馈(行者无疆,2026-07-28):「火山的能用吗()格式试了两个都不对,用的
https://ark.cn-beijing.volces.com/api/plan 和 https://ark.cn-beijing.volces.com/api/plan/v3」。

真因不是他填错:`doubao` 这条 provider 的 base_url 早就正确地写在 DEFAULT_MODEL_CATALOG
(`https://ark.cn-beijing.volces.com/api/v3`),但 `enabled=False` —— **根本不出现在供应商
列表里**,用户只能自己猜接口地址。而 ark 网关**先验鉴权再路由**(实测任意路径都回 401
AuthenticationError,不是 404),填错路径只会看到「key 无效」,没法从报错反推是路径错了。

两处修:
  ① `doubao` 进 `_CURATED_REQUIRED_APIS` → serve 时强制 enabled,存量 DB catalog 自愈;
     display_name 带上「火山方舟 / Volcengine Ark」(用户是按平台名找的)。
  ② `_normalize_openai_base_url` 按 host 确定性收敛 `*.volces.com` → `/api/v3`。
"""
from __future__ import annotations

import pytest

from model_registry import (DEFAULT_MODEL_CATALOG, _CURATED_REQUIRED_APIS,
                            _DEPRECATED_APIS, _ensure_curated_apis, default_api_for)
from platform_app.user_credentials import _normalize_openai_base_url as _norm

_ARK = "https://ark.cn-beijing.volces.com/api/v3"


# ── ① provider 必须露出来 ────────────────────────────────────────────────
def test_doubao_is_curated_and_not_deprecated():
    assert "doubao" in _CURATED_REQUIRED_APIS, "doubao 不在策展白名单 → 存量 catalog 里仍是 disabled"
    assert "doubao" not in _DEPRECATED_APIS


def test_doubao_default_entry_enabled_with_correct_base_url():
    api = default_api_for("doubao")
    assert api is not None
    assert api["enabled"] is True
    assert api["base_url"] == _ARK
    assert api["credential_env"] == "ARK_API_KEY"


def test_display_name_is_findable_by_platform_name():
    """用户是按「火山」找的,只写 Doubao 在列表里认不出来。"""
    name = default_api_for("doubao")["display_name"]
    assert "火山" in name and "Ark" in name


def test_persisted_catalog_with_doubao_disabled_self_heals():
    """存量 DB catalog 把它存成 disabled 时,serve 时必须被强制拉回 enabled(不落库)。"""
    stale = {"apis": [{"id": "doubao", "enabled": False,
                       "base_url": _ARK, "models": []}]}
    healed = _ensure_curated_apis(stale)
    assert healed["apis"][0]["enabled"] is True


def test_missing_doubao_is_merged_back():
    """更老的 catalog 里可能整条都没有 → 从 DEFAULT 并回来。"""
    healed = _ensure_curated_apis({"apis": [{"id": "deepseek", "enabled": True, "models": []}]})
    got = [a for a in healed["apis"] if a["id"] == "doubao"]
    assert got and got[0]["base_url"] == _ARK


def test_deprecated_provider_still_forced_off():
    """别把下架逻辑一起改坏:google_ai_studio 仍必须强制 disabled。"""
    healed = _ensure_curated_apis({"apis": [{"id": "google_ai_studio", "enabled": True, "models": []}]})
    assert healed["apis"][0]["enabled"] is False


# ── ② base_url 自愈:玩家填错的两个格式都要被纠正 ──────────────────────────
@pytest.mark.parametrize("typed", [
    "https://ark.cn-beijing.volces.com/api/plan",       # 群反馈原文其一
    "https://ark.cn-beijing.volces.com/api/plan/v3",    # 群反馈原文其二
    "https://ark.cn-beijing.volces.com",                # 裸域名
    "https://ark.cn-beijing.volces.com/",               # 带尾斜杠
    "https://ark.cn-beijing.volces.com/api",            # 只到 /api
    "ark.cn-beijing.volces.com/api/plan",               # 没写 scheme
    "https://ark.ap-southeast.volces.com/api/plan",     # 别的 region 同样兜住
])
def test_wrong_ark_base_urls_are_normalized(typed):
    assert _norm(typed).endswith("/api/v3")
    assert "/plan" not in _norm(typed)


def test_correct_ark_base_url_is_left_alone():
    assert _norm(_ARK) == _ARK
    assert _norm(_ARK + "/") == _ARK


def test_ark_full_endpoint_tail_still_stripped_first():
    """用户整段贴完整端点时,先剥 /chat/completions 的既有规则仍生效。"""
    assert _norm(_ARK + "/chat/completions") == _ARK


def test_non_volces_hosts_untouched():
    """别误伤其它 provider —— 只按 host 后缀匹配。"""
    for u in ["https://api.deepseek.com/v1", "https://api.x.ai/v1",
              "https://openrouter.ai/api/v1", "https://api.siliconflow.cn/v1"]:
        assert _norm(u) == u


def test_google_self_heal_unchanged():
    """既有的 Google 自愈规则不能被这次改动影响。"""
    assert _norm("https://generativelanguage.googleapis.com/v1beta") == \
        "https://generativelanguage.googleapis.com/v1beta/openai"
