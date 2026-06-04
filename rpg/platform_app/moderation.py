"""反馈通道 NSFW 预审 — 仅用于 /api/feedback。

不对 GM / 剧本 / 对话主通道使用（成人内容产品允许 NSFW）。
AUP §2.J 规定反馈渠道不能投递 NSFW，防止侧通道滥用。

后端：OpenAI /v1/moderations（免费，无 token 计费）。
缺 OPENAI_MODERATION_API_KEY 时降级：反馈仍写入，全进人工审查队列。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

MODERATION_API_KEY: str = os.environ.get("OPENAI_MODERATION_API_KEY", "")
MODERATION_ENDPOINT: str = "https://api.openai.com/v1/moderations"

# AUP §2.J 红线类别 → 阈值
# sexual/minors: 零容忍（CSAM），极低阈值
# 其余命中 → 人工审查
BLOCKED_CATEGORIES: dict[str, float] = {
    "sexual": 0.5,
    "sexual/minors": 0.05,
    "violence/graphic": 0.8,
    "self-harm/instructions": 0.5,
}

# 命中 CSAM 类别时触发 auto_reject（其余命中 manual_review）
_CSAM_CATEGORIES = {"sexual/minors"}


@dataclass
class ModerationVerdict:
    """预审结果。

    action:
      'pass'          — 所有类别低于阈值，自动通过
      'auto_reject'   — CSAM 命中，立刻终止账号
      'manual_review' — 命中其他阈值，或 API key 缺失，进人工审查队列
    categories:
      命中的类别及得分（>阈值的子集）
    scores:
      全部 OpenAI 类别原始分数（API key 缺失时为空）
    """

    action: str
    categories: dict[str, float] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)


async def moderate_feedback(text: str) -> ModerationVerdict:
    """对反馈文本做 NSFW 预审。

    降级策略：
    - API key 未配置 → manual_review（不拦截，进人工队列）
    - text 为空 → pass（空内容不审）
    - OpenAI 调用失败 → manual_review（保守降级）

    中文局限：OpenAI moderation 对中文 NSFW 检出率有限；
    后续可补 toxic-bert 中文模型作 fallback。
    """
    if not text.strip():
        return ModerationVerdict(action="pass")

    if not MODERATION_API_KEY:
        logger.info("OPENAI_MODERATION_API_KEY 未配置，反馈进人工审查队列")
        return ModerationVerdict(action="manual_review")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                MODERATION_ENDPOINT,
                headers={"Authorization": f"Bearer {MODERATION_API_KEY}"},
                json={"input": text[:30000]},  # OpenAI 单次上限
            )

        if resp.status_code != 200:
            logger.warning(
                "moderation API 返回非 200: status=%s body=%.200s",
                resp.status_code,
                resp.text,
            )
            return ModerationVerdict(action="manual_review")

        result = resp.json()["results"][0]
        scores: dict[str, float] = result.get("category_scores", {})

        # 零容忍：CSAM → auto_reject
        for cat in _CSAM_CATEGORIES:
            threshold = BLOCKED_CATEGORIES.get(cat, 0.05)
            if scores.get(cat, 0.0) > threshold:
                logger.error(
                    "moderation CSAM 命中: category=%s score=%.4f",
                    cat,
                    scores[cat],
                )
                return ModerationVerdict(
                    action="auto_reject",
                    categories={cat: scores[cat]},
                    scores=scores,
                )

        # 其余阈值命中 → manual_review
        hit: dict[str, float] = {
            cat: scores[cat]
            for cat, threshold in BLOCKED_CATEGORIES.items()
            if cat not in _CSAM_CATEGORIES and scores.get(cat, 0.0) > threshold
        }
        if hit:
            logger.warning("moderation 命中阈值类别: %s", hit)
            return ModerationVerdict(
                action="manual_review",
                categories=hit,
                scores=scores,
            )

        return ModerationVerdict(action="pass", scores=scores)

    except Exception:
        logger.exception("moderation API 调用失败，降级到人工审查")
        return ModerationVerdict(action="manual_review")
