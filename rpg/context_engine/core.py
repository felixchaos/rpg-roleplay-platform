"""context_engine.core — 主入口 build_context_bundle."""
from __future__ import annotations

from typing import Any

from context_engine._constants import MAX_LAYER_CHARS
from context_engine._utils import _cache_plan, _estimate_tokens, _layer, _preview, _trim
from context_engine.helpers import (
    _neutralize_state_write_tags,
    _pending_jump_warning_text,
)
from context_engine.layers import (
    _active_hypotheses_layer,
    _candidate_actions_layer,
    _fact_groups_layer,
    _state_schema_layer,
    _write_results_layer,
)
from context_engine.loaders import _safe_load_chars
from context_engine.rules_text import (
    _agent_runtime_rules,
    _context_agent_debug,
    _context_agent_decision,
    _story_rules,
)
from timeline_index import timeline_filter_for_label


def _format_history(history: list[dict]) -> str:
    if not history:
        return "（暂无最近对话）"
    lines = []
    for msg in history:
        role = "玩家" if msg.get("role") == "user" else "GM"
        lines.append(f"{role}：{msg.get('content', '')}")
    return "\n\n".join(lines)


def _recent_text(history: list[dict]) -> str:
    return "\n".join(str(msg.get("content", "")) for msg in history)


def build_context_bundle(
    state,
    user_input: str,
    retrieved_context: str = "",
    curator_plan: dict[str, Any] | None = None,
    script_id: int | None = None,
    book_id: int | None = None,
    contributions: list | None = None,
    manifest: dict | None = None,
    save_id: int | None = None,  # task 107E
) -> dict[str, Any]:
    """组装单轮 prompt 上下文。

    新架构：所有数据源（小说时间线/章节检索/角色卡/世界书/模组房间/规则状态等）
    都由 ContextProvider 贡献 ContextContribution，本函数只负责合并 contribution.layers
    与通用 GM 层（rules / agent_runtime / state schema / candidate_actions / user_input
    等 — 不属于任何具体数据源，是 GM 运行契约）。

    调用方式：
    - 新路径（推荐）：context_agent 跑完 providers 后传 contributions + manifest。
    - 旧路径（兼容）：caller 不传 contributions/manifest，本函数自动 resolve_content_pack
      + run_providers，以保证 /api/opening 等直接调用方继续工作。
    """
    # 自动 resolve manifest + run providers（旧 caller 兼容）
    if contributions is None or manifest is None:
        from context_providers import (
            ProviderServices,
            resolve_content_pack,
            run_providers,
        )
        if manifest is None:
            manifest = resolve_content_pack(state, script_id=script_id)
        if contributions is None:
            # 用空 Demand 跑（caller 未提供 curator_plan 时只是降级路径）
            from context_providers import Demand
            from retrieval import retrieve_context as _retrieve_fn
            services = ProviderServices(
                user_id=None, script_id=script_id, book_id=book_id,
                save_id=save_id,  # task 107E
                retrieve_fn=_retrieve_fn,
                timeline_filter_fn=timeline_filter_for_label,
            )
            demand = Demand(player_intent=user_input or "",
                            retrieval_query=user_input or "")
            contributions, _used = run_providers(state, manifest, demand, services)

    # 把 retrieved_context 当 fallback：仅在 contributions 没贡献 novel_retrieval 时用。
    has_retrieval_layer = any(
        c.applied and c.provider_id == "novel_retrieval"
        for c in (contributions or [])
    )

    # 通用 GM 层（不依赖具体 ContentPack；GM 运行契约）
    universal_layers = [
        _layer("rules", "剧情规则", _story_rules(), sticky=True, priority=100),
        _layer("agent_runtime", "主GM代理运行契约", _agent_runtime_rules(), sticky=True, priority=99),
        # pending_jump 警告是通用 GM 约束，任何 ContentPack 都得遵守
        _layer("timeline_pending", "时间跳跃待确认", _pending_jump_warning_text(state),
               sticky=False, priority=86),
        _layer("state", "当前状态", state.short_summary(), sticky=True, priority=55),
        _layer("fact_groups", "事实分组（按 kind）", _fact_groups_layer(state), sticky=False, priority=50),
        _layer("state_schema", "状态字段 schema",
               _state_schema_layer(state, _safe_load_chars(script_id, book_id, manifest)),
               sticky=True, priority=45),
        _layer("write_results", "上轮标签处理结果", _write_results_layer(state), sticky=False, priority=35),
        _layer("hypotheses", "未确认推测", _active_hypotheses_layer(state), sticky=False, priority=32),
        _layer("context_agent", "子代理上下文决议",
               _context_agent_decision(curator_plan),
               priority=30, items=[_context_agent_debug(curator_plan)]),
        _layer("candidate_actions", "本轮候选动作",
               _candidate_actions_layer(curator_plan), sticky=False, priority=28),
    ]

    # Provider contribution 层
    provider_layers: list[dict] = []
    contribution_meta: list[dict] = []
    for contrib in (contributions or []):
        if not contrib.applied:
            continue
        contribution_meta.append({
            "provider_id": contrib.provider_id,
            "kind": contrib.kind,
            "priority": contrib.priority,
            "facts": list(contrib.facts),
            "warnings": list(contrib.warnings),
            "tokens_estimate": contrib.tokens_estimate,
            "debug": dict(contrib.debug),
        })
        for layer in contrib.layers:
            lyr = dict(layer)
            lyr.setdefault("priority", contrib.priority)
            lyr.setdefault("source", contrib.provider_id)
            provider_layers.append(lyr)

    # 兜底 rag 层：若 contributions 没注入 retrieval，但 caller 传了 retrieved_context（旧 caller）
    if not has_retrieval_layer and retrieved_context:
        provider_layers.append(_layer(
            "rag", "检索参考",
            _neutralize_state_write_tags(retrieved_context),
            sticky=False, priority=40,
        ))

    # user_input 永远最后
    tail_layers = [
        _layer("user_input", "玩家本轮输入", user_input or "（空）", priority=0),
    ]

    # 合并 + 按 priority 降序排序（高优先级在前 = 稳定前缀，利于 prompt cache）
    all_layers = universal_layers + provider_layers + tail_layers
    all_layers.sort(key=lambda lyr: -int(lyr.get("priority", 50)))

    prompt_parts = []
    debug_layers = []
    for layer in all_layers:
        trimmed = _trim(layer["content"], MAX_LAYER_CHARS.get(layer["id"], 1800))
        if not trimmed:
            continue
        prompt_parts.append(f"【{layer['title']}】\n{trimmed}")
        debug_layers.append({
            "id": layer["id"],
            "title": layer["title"],
            "chars": len(trimmed),
            "estimated_tokens": _estimate_tokens(trimmed),
            "sticky": layer.get("sticky", False),
            "priority": layer.get("priority", 50),
            "source": layer.get("source", ""),
            "preview": _preview(trimmed),
            "items": layer.get("items", []),
        })

    prompt = "\n\n".join(prompt_parts)
    cache_plan = _cache_plan(debug_layers, prompt_parts)
    debug = {
        "total_chars": len(prompt),
        "estimated_tokens": _estimate_tokens(prompt),
        "layers": debug_layers,
        "cache_plan": cache_plan,
        "curator_plan": curator_plan or {},
        "manifest": {
            "id": (manifest or {}).get("id"),
            "kind": (manifest or {}).get("kind"),
            "context_providers": list((manifest or {}).get("context_providers") or []),
            "retrieval_policy": (manifest or {}).get("retrieval_policy"),
            "gm_policy": (manifest or {}).get("gm_policy"),
        },
        "contributions": contribution_meta,
    }
    return {"prompt": prompt, "debug": debug}
