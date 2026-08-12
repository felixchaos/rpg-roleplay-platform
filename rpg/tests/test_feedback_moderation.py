"""FB-04 反馈通道 NSFW 预审单元测试。

测试 moderation.py 的 moderate_feedback() 纯逻辑，
全量 mock httpx.AsyncClient，不发真实网络请求。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# REPO_ROOT = rpg/  — 与其他测试保持一致
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from platform_app.moderation import (  # noqa: E402
    ModerationVerdict,
    moderate_feedback,
)

# ─── 辅助 ───────────────────────────────────────────────────────────────────

def _mock_openai_response(scores: dict[str, float]) -> MagicMock:
    """构造 OpenAI moderation API 响应 mock。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [
            {
                "flagged": any(v > 0.5 for v in scores.values()),
                "category_scores": scores,
                "categories": {k: (v > 0.5) for k, v in scores.items()},
            }
        ]
    }
    return mock_resp


def _patch_client(mock_resp: MagicMock):
    """patch httpx.AsyncClient.post 返回 mock_resp。"""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return patch("platform_app.moderation.httpx.AsyncClient", return_value=mock_client)


# ─── 测试：空文本 ────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_empty_text_passes():
    """空文本不调用 API，直接 pass。"""
    with patch("platform_app.moderation.MODERATION_API_KEY", "sk-test"):
        verdict = await moderate_feedback("   ")
    assert verdict.action == "pass"
    assert verdict.categories == {}


# ─── 测试：API key 缺失降级 ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_no_api_key_falls_back_to_manual_review():
    """未配置 OPENAI_MODERATION_API_KEY → 全量 manual_review，不拦截。"""
    with patch("platform_app.moderation.MODERATION_API_KEY", ""):
        verdict = await moderate_feedback("正常的用户反馈文字")
    assert verdict.action == "manual_review"
    assert verdict.categories == {}
    assert verdict.scores == {}


# ─── 测试：干净文本 pass ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_clean_text_passes():
    """所有类别低于阈值 → pass。"""
    clean_scores = {
        "sexual": 0.01,
        "sexual/minors": 0.001,
        "violence": 0.02,
        "violence/graphic": 0.005,
        "self-harm/instructions": 0.003,
        "hate": 0.01,
    }
    mock_resp = _mock_openai_response(clean_scores)
    with patch("platform_app.moderation.MODERATION_API_KEY", "sk-test"):
        with _patch_client(mock_resp):
            verdict = await moderate_feedback("这个游戏很有意思，建议加更多支线任务。")
    assert verdict.action == "pass"
    assert verdict.categories == {}
    assert "sexual" in verdict.scores


# ─── 测试：sexual/minors CSAM → auto_reject ──────────────────────────────────

@pytest.mark.anyio
async def test_csam_triggers_auto_reject():
    """sexual/minors 超阈值 → auto_reject（CSAM 零容忍）。"""
    csam_scores = {
        "sexual": 0.3,
        "sexual/minors": 0.9,   # 超过 0.05 阈值
        "violence": 0.0,
        "violence/graphic": 0.0,
        "self-harm/instructions": 0.0,
        "hate": 0.0,
    }
    mock_resp = _mock_openai_response(csam_scores)
    with patch("platform_app.moderation.MODERATION_API_KEY", "sk-test"):
        with _patch_client(mock_resp):
            verdict = await moderate_feedback("some text")
    assert verdict.action == "auto_reject"
    assert "sexual/minors" in verdict.categories
    assert verdict.categories["sexual/minors"] == pytest.approx(0.9)


# ─── 测试：sexual/minors 边界恰好在阈值上 → 不触发 ──────────────────────────

@pytest.mark.anyio
async def test_csam_at_threshold_does_not_reject():
    """sexual/minors 等于阈值（非大于）→ 不触发 auto_reject。"""
    scores = {
        "sexual": 0.0,
        "sexual/minors": 0.05,  # == 阈值，不超过
        "violence": 0.0,
        "violence/graphic": 0.0,
        "self-harm/instructions": 0.0,
        "hate": 0.0,
    }
    mock_resp = _mock_openai_response(scores)
    with patch("platform_app.moderation.MODERATION_API_KEY", "sk-test"):
        with _patch_client(mock_resp):
            verdict = await moderate_feedback("some text")
    assert verdict.action == "pass"


# ─── 测试：sexual 超阈值 → manual_review（非 CSAM） ─────────────────────────

@pytest.mark.anyio
async def test_sexual_above_threshold_triggers_manual_review():
    """sexual 超 0.5 但无 CSAM → manual_review（进人工队列，不自动拒）。"""
    scores = {
        "sexual": 0.85,         # 超 0.5
        "sexual/minors": 0.001, # 低于 CSAM 阈值
        "violence": 0.0,
        "violence/graphic": 0.0,
        "self-harm/instructions": 0.0,
        "hate": 0.0,
    }
    mock_resp = _mock_openai_response(scores)
    with patch("platform_app.moderation.MODERATION_API_KEY", "sk-test"):
        with _patch_client(mock_resp):
            verdict = await moderate_feedback("some text")
    assert verdict.action == "manual_review"
    assert "sexual" in verdict.categories
    assert "sexual/minors" not in verdict.categories


# ─── 测试：violence/graphic 超阈值 → manual_review ──────────────────────────

@pytest.mark.anyio
async def test_graphic_violence_triggers_manual_review():
    """violence/graphic 超 0.8 → manual_review。"""
    scores = {
        "sexual": 0.01,
        "sexual/minors": 0.001,
        "violence": 0.5,
        "violence/graphic": 0.95,  # 超 0.8
        "self-harm/instructions": 0.0,
        "hate": 0.0,
    }
    mock_resp = _mock_openai_response(scores)
    with patch("platform_app.moderation.MODERATION_API_KEY", "sk-test"):
        with _patch_client(mock_resp):
            verdict = await moderate_feedback("some text")
    assert verdict.action == "manual_review"
    assert "violence/graphic" in verdict.categories


# ─── 测试：API 返回 500 → 降级 manual_review ─────────────────────────────────

@pytest.mark.anyio
async def test_api_500_falls_back_to_manual_review():
    """OpenAI API 返回 5xx → 保守降级 manual_review，不拦截用户。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    with patch("platform_app.moderation.MODERATION_API_KEY", "sk-test"):
        with _patch_client(mock_resp):
            verdict = await moderate_feedback("some text")
    assert verdict.action == "manual_review"
    assert verdict.categories == {}


# ─── 测试：网络异常 → 降级 manual_review ────────────────────────────────────

@pytest.mark.anyio
async def test_network_exception_falls_back_to_manual_review():
    """httpx 网络异常 → 保守降级 manual_review。"""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(side_effect=Exception("connection timeout"))

    with patch("platform_app.moderation.MODERATION_API_KEY", "sk-test"):
        with patch("platform_app.moderation.httpx.AsyncClient", return_value=mock_client):
            verdict = await moderate_feedback("some text")
    assert verdict.action == "manual_review"


# ─── 测试：ModerationVerdict 数据完整性 ──────────────────────────────────────

def test_verdict_dataclass_defaults():
    """ModerationVerdict 默认字段不共享可变对象。"""
    v1 = ModerationVerdict(action="pass")
    v2 = ModerationVerdict(action="manual_review")
    v1.categories["x"] = 0.5
    assert "x" not in v2.categories, "dataclass field 不应共享默认 dict"
