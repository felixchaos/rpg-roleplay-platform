"""import_pipeline.rebuild_registry — rebuild 模块注册表 + 嵌入凭证预检 helpers

来源: 原 rpg/platform_app/import_pipeline.py REBUILD_MODULES / normalize_rebuild_module / _embedding_preflight_or_raise / _embedding_prereq(原 L2409-2469) 区段,纯机械搬家(函数体逐字未动),零行为变化。
"""
from __future__ import annotations

from typing import Any

from .errors import MissingEmbeddingCredentialError


# ══════════════════════════════════════════════════════════════════════
#  phase_backend: rebuild job 调度器 (kind='rebuild_*' 写 import_jobs)
# ══════════════════════════════════════════════════════════════════════
REBUILD_MODULES = {
    "chunks":        ("rebuild_chunks",       "切块重建",     False),
    "chapter-facts": ("rebuild_facts",        "章节事实重建", False),
    "canon":         ("rebuild_canon",        "规范实体重建", True),
    # cards = rebuild_cards_from_canon,**零 LLM**(从 canon/facts 反推,canon 空则退化 facts 词频),
    # 恒免费、不需任何 LLM 凭证。之前误标 True → schedule_module_rebuild 的 needs_llm 闸会
    # require_user_llm_credential 拦截,导致没配 key(或 key 校验不过)的用户「角色卡重置不了」
    # (而 anchors=False 的时间线却能重做)。修正为 False,与 estimate 路径(本就 force False)对齐。
    "cards":         ("rebuild_cards",        "角色卡重建",   False),
    "worldbook":     ("rebuild_worldbook",    "世界书重建",   True),  # may be True or False depending on source
    "anchors":       ("rebuild_anchors",      "时间线重建",   False),
    "embeddings":    ("rebuild_embeddings",   "向量重嵌入",   False),
    # 三个新模块(原 CLI-only 提取能力接入 rebuild 框架,补 API/UI 缺口):
    # · facts_refine: chapter_facts 摘要/故事内时间 LLM 精炼(extract.facts_refine.refine_script),
    #   恒需 BYOK LLM。
    "facts_refine":  ("rebuild_facts_refine", "章节摘要精炼", True),
    # · worldbook_enrich: 命中 pattern 的世界书条目 LLM 充实(extract.worldbook_enrich)，
    #   恒需 BYOK LLM。
    "worldbook_enrich": ("rebuild_worldbook_enrich", "世界书条目充实", True),
    # · world_key: 结构先验回填(extract.world_key_backfill.backfill_worldlines)默认零 LLM，
    #   仅 options.use_llm=True 时追加 LLM 窄确认 —— 与 worldbook/cards 同款「按 body 覆盖
    #   needs_llm」范式,故此处基线登记为 False,下方 estimate/schedule 按 use_llm 校正。
    "world_key":     ("rebuild_world_key",    "世界线回填",   False),
}


def normalize_rebuild_module(module: str) -> str:
    value = str(module or "").strip()
    if value == "chapter_facts":
        return "chapter-facts"
    if value == "full_pipeline":
        return "full-pipeline"
    return value


def _embedding_preflight_or_raise(user_id: int) -> dict[str, Any]:
    from ..knowledge.embedding import embedding_preflight

    payload = embedding_preflight(user_id)
    if not payload.get("ok"):
        raise MissingEmbeddingCredentialError(payload)
    return payload


def _embedding_prereq(user_id: int) -> dict[str, Any]:
    from ..knowledge.embedding import embedding_preflight

    payload = embedding_preflight(user_id)
    return {
        "key": "embedding_credentials",
        "label": "向量嵌入凭证",
        "ok": bool(payload.get("ok")),
        "hint": payload.get("hint") or payload.get("error") or "",
        "api_id": payload.get("api_id"),
        "model": payload.get("model"),
        "credential_api_id": payload.get("credential_api_id"),
        "needs_credentials": bool(payload.get("needs_credentials")),
    }
