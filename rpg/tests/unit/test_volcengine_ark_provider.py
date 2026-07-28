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


def test_persisted_catalog_display_name_self_heals():
    """只同步 enabled 会留下「露出来了但名字还是旧的」—— 生产 DB 里存的就是旧名 "Doubao",
    用户按「火山」照样搜不到,等于改名没生效。"""
    stale = {"apis": [{"id": "doubao", "enabled": True, "display_name": "Doubao",
                       "base_url": _ARK, "models": []}]}
    healed = _ensure_curated_apis(stale)
    assert "火山" in healed["apis"][0]["display_name"]


def test_admin_customized_base_url_is_not_clobbered():
    """base_url 可能被管理员按区域/中转有意改过 —— 强制同步只覆盖 display_name,不碰它。"""
    custom = "https://my-ark-proxy.example.com/api/v3"
    healed = _ensure_curated_apis({"apis": [{"id": "doubao", "enabled": False,
                                             "display_name": "x", "base_url": custom, "models": []}]})
    assert healed["apis"][0]["base_url"] == custom


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


# ── ①b Agent Plan 是同域名下的另一个产品(反馈者实测确认) ──────────────────
_PLAN = "https://ark.cn-beijing.volces.com/api/plan/v3"


def test_agent_plan_is_a_separate_provider():
    """实测结论:订阅套餐走 /api/plan/v3(不是 /api/plan、也不能加 /v1),
    deepseek-v4-pro 返 200。与 Ark 的 /api/v3 是两条不同的产品线,必须各自成条目。"""
    api = default_api_for("ark_agent_plan")
    assert api is not None, "Agent Plan 没进 catalog → GM 解析不到这个 api_id"
    assert api["base_url"] == _PLAN
    assert api["enabled"] is True
    assert "ark_agent_plan" in _CURATED_REQUIRED_APIS


def test_agent_plan_and_ark_do_not_collide():
    """两条不能互相覆盖 —— 它们只是同域名,产品与路径都不同。"""
    assert default_api_for("doubao")["base_url"] != default_api_for("ark_agent_plan")["base_url"]


def test_deprecated_provider_still_forced_off():
    """别把下架逻辑一起改坏:google_ai_studio 仍必须强制 disabled。"""
    healed = _ensure_curated_apis({"apis": [{"id": "google_ai_studio", "enabled": True, "models": []}]})
    assert healed["apis"][0]["enabled"] is False


# ── ② base_url **不许**被路径改写(v1.74.2 撤回) ────────────────────────────
# v1.74.0 曾按 host 把 *.volces.com 一律改写成 /api/v3,以为用户把 /api/plan 写错了。
# 那是错的:火山方舟同一域名下至少有两个不同产品的入口 ——
#   · /api/v3   Ark OpenAI 兼容(doubao 系模型)
#   · /api/plan Agent Plan(Anthropic Messages 原生 + AUTH_TOKEN)
# 反馈者原话「agent plan 给的是这个地址」,即 /api/plan 是火山控制台真给的合法端点。
# 一刀切改写 = 把人家正确的配置悄悄改成打不开的地址,而 ark 网关先验鉴权再路由
# (任意路径都回 401),用户根本看不出地址被动过手脚。

@pytest.mark.parametrize("typed", [
    "https://ark.cn-beijing.volces.com/api/plan",       # Agent Plan:合法端点,绝不能被改
    "https://ark.cn-beijing.volces.com/api/v3",         # Ark OpenAI 兼容
    "https://ark.cn-beijing.volces.com/api",
    "https://ark.ap-southeast.volces.com/api/plan",
])
def test_volces_base_urls_are_never_rewritten(typed):
    assert _norm(typed) == typed.rstrip("/")


def test_only_the_universally_wrong_tail_is_still_stripped():
    """既有的通用规则不受影响:整段贴完整端点时仍剥 /chat/completions。"""
    assert _norm(_ARK + "/chat/completions") == _ARK
    assert _norm(_ARK + "/") == _ARK


def test_non_volces_hosts_untouched():
    for u in ["https://api.deepseek.com/v1", "https://api.x.ai/v1",
              "https://openrouter.ai/api/v1", "https://api.siliconflow.cn/v1"]:
        assert _norm(u) == u


def test_google_self_heal_unchanged():
    """Google 那条自愈是**同一产品**的公认写法歧义(少写 /openai),仍然保留。"""
    assert _norm("https://generativelanguage.googleapis.com/v1beta") == \
        "https://generativelanguage.googleapis.com/v1beta/openai"


# ── ③ /models 404 必须说清「没有这个接口」,而不是猜 base_url ────────────────
# 反馈者实测:方舟订阅套餐地址没有 /models(恒 404)。原文案把 404 也归到
# 「base_url 可能缺 /v1」,他照着加了 /v1 → 连本来能用的 chat/completions 一起挂掉。
def test_models_404_message_does_not_blame_base_url():
    import pathlib as _p
    import model_probe as _mp
    src = _p.Path(_mp.__file__).read_text(encoding="utf-8")
    assert "if _code == 404:" in src, "404 没有单独分支,仍会落进「base_url 可能缺 /v1」的通用猜测"
    i = src.index("if _code == 404:")
    branch = src[i: i + 700]
    assert "没有提供模型列表接口" in branch
    assert "不要**在 base_url 后面加 /v1" in branch or "不要" in branch and "/v1" in branch
    assert "手动填写模型 ID" in branch


def test_generic_guess_still_exists_for_non_404():
    """非 404 的拒绝仍保留原来的通用提示,别把有用的猜测一起删了。"""
    import pathlib as _p
    import model_probe as _mp
    src = _p.Path(_mp.__file__).read_text(encoding="utf-8")
    assert "base_url 可能缺 /v1 版本段" in src
