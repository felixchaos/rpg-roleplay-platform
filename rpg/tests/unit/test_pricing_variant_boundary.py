"""价格表前缀回退要有型号边界(巡检 2026-09-28 F9)。

v1.88.0 给 get_pricing 补了最长前缀回退,但没分「同一型号的日期 / 预览别名」和「另一个档位」:
- gpt-5-nano / gpt-5-pro / gpt-5-mini 全被套上 gpt-5 的价($2/$8),实际差 8-40 倍;
- siliconflow 的 DeepSeek-V3.2 被套上 V3 的 64K 窗口、hunyuan-large-longcontext 被套上
  hunyuan-large 的 28K 窗口 —— 窗口喂给层预算求解器后 GM 上下文被砍到 54% / 23%。
chat_pipeline/context 与 context_engine/budget 的契约是「定价表没这个型号 → 窗口 0 → 不求解」,
被前缀回退静默打破。
"""
from __future__ import annotations

import pytest

import model_probe
from context_engine.budget import layer_budget_chars
from platform_app.usage import context_window_for


@pytest.mark.parametrize("api_id,name", [
    ("openai", "gpt-5-nano"),
    ("openai", "gpt-5-mini"),
    ("openai", "gpt-5-pro"),
    ("openai", "gpt-5.1"),              # 小版本号 = 另一个型号
    ("openai", "gpt-4.1-mini"),
    ("openai", "gpt-5.6-luna-mini"),    # 逐级退到更短前缀也都不合格
    ("openrouter", "openai/gpt-4o-mini"),
    ("deepseek", "deepseek-v3.2"),
    ("siliconflow", "deepseek-ai/DeepSeek-V3.2"),
    ("hunyuan", "hunyuan-large-longcontext"),
    ("vertex_ai", "gemini-3-pro-image"),
    ("vertex_ai", "gemini-2.5-flash-tts"),
    ("anthropic", "claude-sonnet-5-1"),
    ("dashscope", "qwen-max-longcontext"),
])
def test_other_tier_or_minor_version_gets_no_price(api_id, name):
    assert model_probe.get_pricing(api_id, name) is None


@pytest.mark.parametrize("api_id,name,base", [
    ("deepseek", "deepseek-v4.1-flash-expires-on-0910", "deepseek-v4.1-flash"),
    ("vertex_ai", "gemini-3.8-flash-preview-09-01", "gemini-3.8-flash"),
    ("vertex_ai", "gemini-2.5-flash-preview-09-2025", "gemini-2.5-flash"),
    ("vertex_ai", "gemini-2.0-flash-001", "gemini-2.0-flash"),
    ("vertex_ai", "gemini-2.5-pro-exp-03-25", "gemini-2.5-pro"),
    ("anthropic", "claude-opus-4-8@20260801", "claude-opus-4-8"),
    ("anthropic", "claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
    ("openai", "gpt-4o-mini-2024-07-18", "gpt-4o-mini"),
    ("dashscope", "qwen-plus-latest", "qwen-plus"),
])
def test_dated_and_preview_aliases_still_priced(api_id, name, base):
    p = model_probe.get_pricing(api_id, name)
    assert p is not None, name
    assert p["source"] == "static-prefix"
    exact = model_probe.get_pricing(api_id, base)
    assert (p["input"], p["output"]) == (exact["input"], exact["output"])


def test_exact_hit_unchanged():
    p = model_probe.get_pricing("openai", "gpt-5")
    assert p["source"] == "static" and p["context"] == 400000


def test_context_window_only_from_exact_hits():
    """前缀命中不给窗口:给小了会静默砍掉 GM 上下文层,给不出只是回到不求解。"""
    assert context_window_for("openai", "gpt-5") == 400000
    assert context_window_for("deepseek", "deepseek-v4.1-flash-expires-on-0910") == 0
    assert context_window_for("siliconflow", "deepseek-ai/DeepSeek-V3.2") == 0


@pytest.mark.parametrize("api_id,name", [
    ("siliconflow", "deepseek-ai/DeepSeek-V3.2"),
    ("hunyuan", "hunyuan-large-longcontext"),
    ("openai", "gpt-5-nano"),
])
def test_layer_budget_not_solved_for_unknown_models(api_id, name):
    assert layer_budget_chars(api_id, name) == 0


def test_capabilities_prefix_fallback_left_alone():
    """能力是家族级特征,前缀继承是对的:档位变体照样有 tools / reasoning 标签。"""
    caps = model_probe.get_capabilities("deepseek", "deepseek-v4.1-flash-expires-on-0910")
    assert "tools" in caps and "reasoning" in caps
