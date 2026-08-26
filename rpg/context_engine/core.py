"""context_engine.core — 主入口 build_context_bundle."""
from __future__ import annotations

from typing import Any

from context_engine._constants import (
    MAX_LAYER_CHARS,  # noqa: F401 — 老 caller / 测试仍直接读它
    NEVER_DROP_LAYERS,
    layer_cache_tier,
    layer_char_budget,
    layer_min_chars,
)
from context_engine._utils import _cache_plan, _estimate_tokens, _layer, _preview, _trim
from context_engine.budget import solve_layer_budgets

# 未登记层 id 只 warn 一次:每回合都会重复,刷屏没有信息量。
_WARNED_UNREGISTERED: set[str] = set()


def _warn_unregistered_layers(layer_ids: list[str]) -> None:
    """层 id 没进 MAX_LAYER_CHARS → 被砍到默认上限。这件事必须可见。

    历史上这样丢过 8 个层(novel_retrieval / novel_worldbook / timeline_pending /
    酒馆四层 / episodic_recall 等),每次都是几个月后靠玩家反馈发现的,因为症状
    (「GM 不记得」「世界书像摆设」)和「被截断」没有任何字面关联。
    """
    fresh = [i for i in layer_ids if i not in _WARNED_UNREGISTERED]
    if not fresh:
        return
    _WARNED_UNREGISTERED.update(fresh)
    import logging
    logging.getLogger("context_engine").warning(
        "[context] 这些层 id 未登记进 MAX_LAYER_CHARS,已按默认上限截断: %s "
        "—— 请在 context_engine/_constants.py 里补登记(守卫: "
        "rpg/tests/unit/test_context_layer_budget_registry.py)",
        ", ".join(sorted(fresh)),
    )


def _split_anchor_pending(retrieved_context: str) -> tuple[str, str]:
    """从 retrieve_context 拼出来的整段文本里,拆出"世界线收束·接下来的锚点"段。

    返回 (anchor_section, rag_body) — anchor_section 单独成 layer (高优先级 + 单独
    trim 上限),rag_body 是去掉该段后的剩余文本继续走 rag layer。
    没匹配到时返回 ("", retrieved_context)。

    用 "=== 世界线收束·接下来的锚点 ===" 起始标志,以下一个 "=== xxx ===" 或文末为止。
    """
    if not retrieved_context:
        return "", retrieved_context or ""
    marker = "=== 世界线收束·接下来的锚点 ==="
    start = retrieved_context.find(marker)
    if start < 0:
        return "", retrieved_context
    # 找下一个 "=== " 段头
    next_section = retrieved_context.find("\n=== ", start + len(marker))
    if next_section < 0:
        anchor_section = retrieved_context[start:]
        rag_body = retrieved_context[:start]
    else:
        anchor_section = retrieved_context[start:next_section]
        rag_body = retrieved_context[:start] + retrieved_context[next_section + 1:]
    # 清理多余空行
    return anchor_section.strip(), rag_body.strip()

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


def _safe(fn, default: str = "") -> str:
    """安全求值单个 layer 内容:任一 builder 抛异常只让该层降级为空,不连累整个上下文。
    universal_layers 是急切求值的 list 字面量 —— 不隔离则一个 builder 抛异常会让整个
    context bundle 构造失败(规则/状态/schema 全丢 → 丢整轮)。工具受 LLM 影响写 state,
    可能写入畸形数据使下一轮某 layer choke,故逐层隔离(与 provider 层 per-provider try 一致)。"""
    try:
        return fn() or ""
    except Exception:
        import logging
        logging.getLogger("context_engine").debug("layer build failed", exc_info=True)
        return default


def _safe_list(fn, default: list | None = None) -> list:
    """同 _safe 但用于 list 类型的 layer 字段(如 items),异常降级为空列表。"""
    try:
        return fn() or []
    except Exception:
        return default if default is not None else []


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
    budget_chars: int | None = None,  # v1.82.0 全局预算求解;None/0 = 每层各拿 want(旧行为)
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
    # 每层内容经 _safe 隔离:某 builder 抛异常只让该层为空,保住其余层(规则/状态等)与整轮
    universal_layers = [
        _layer("rules", "剧情规则", _safe(lambda: _story_rules()), sticky=True, priority=100),
        _layer("agent_runtime", "主GM代理运行契约", _safe(lambda: _agent_runtime_rules()), sticky=True, priority=99),
        # pending_jump 警告是通用 GM 约束，任何 ContentPack 都得遵守
        _layer("timeline_pending", "时间跳跃待确认", _safe(lambda: _pending_jump_warning_text(state)),
               sticky=False, priority=86),
        _layer("state", "当前状态", _safe(lambda: state.short_summary()), sticky=True, priority=55),
        _layer("fact_groups", "事实分组（按 kind）", _safe(lambda: _fact_groups_layer(state)), sticky=False, priority=50),
        _layer("state_schema", "状态字段 schema",
               _safe(lambda: _state_schema_layer(state, _safe_load_chars(script_id, book_id, manifest))),
               sticky=True, priority=45),
        _layer("write_results", "上轮标签处理结果", _safe(lambda: _write_results_layer(state)), sticky=False, priority=35),
        _layer("hypotheses", "未确认推测", _safe(lambda: _active_hypotheses_layer(state)), sticky=False, priority=32),
        _layer("context_agent", "子代理上下文决议",
               _safe(lambda: _context_agent_decision(curator_plan)),
               priority=30, items=_safe_list(lambda: [_context_agent_debug(curator_plan)])),
        _layer("candidate_actions", "本轮候选动作",
               _safe(lambda: _candidate_actions_layer(curator_plan)), sticky=False, priority=28),
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
        # 把 retrieve_context 输出的 "世界线收束·接下来的锚点" 段拆出来独立成 high-priority layer。
        # 不拆的话整个 retrieved_context 作为单一 rag layer 被 trim 到 MAX_LAYER_CHARS["rag"]=2200,
        # 而 "世界线收束" 段通常在中后部 (pos 3000+) → 100% 被截掉 → GM 拿不到 pending anchors,
        # 玩家进入 ch1 也不知道该让 [卡切尔] 登场。修法:作为独立 layer 给单独的 trim 上限和优先级。
        anchor_section, rag_body = _split_anchor_pending(retrieved_context)
        if anchor_section:
            provider_layers.append(_layer(
                "anchor_pending", "世界线收束·接下来的锚点",
                anchor_section,
                sticky=False, priority=72,  # 高于 worldbook(70),低于玩家 directive(95)
            ))
        provider_layers.append(_layer(
            "rag", "检索参考",
            _neutralize_state_write_tags(rag_body),
            sticky=False, priority=40,
        ))

    # user_input 永远最后
    tail_layers = [
        _layer("user_input", "玩家本轮输入", user_input or "（空）", priority=0),
    ]

    # 合并 + 按 priority 降序排序（高优先级在前 = 稳定前缀，利于 prompt cache）
    all_layers = universal_layers + provider_layers + tail_layers
    all_layers.sort(key=lambda lyr: -int(lyr.get("priority", 50)))

    # ── 每层上限:先查登记表(命中与否要交出去,不许静默),再做全局求解 ────────────
    want_by_id: dict[str, int] = {}
    unregistered: list[str] = []
    for layer in all_layers:
        lid = layer["id"]
        if lid in want_by_id:
            continue
        want, registered = layer_char_budget(lid)
        want_by_id[lid] = want
        if not registered:
            unregistered.append(lid)
    if unregistered:
        # 动态生成的层 id 绕得过 AST 守卫,绕不过这里。warn 一次即可(每轮都会重复)。
        _warn_unregistered_layers(unregistered)

    granted = want_by_id
    dropped_ids: list[str] = []
    if budget_chars and budget_chars > 0:
        specs = [
            {"id": lid,
             "want": want_by_id[lid],
             "min": layer_min_chars(lid, want_by_id[lid]),
             "priority": int(next((lyr.get("priority", 50) for lyr in all_layers
                                   if lyr["id"] == lid), 50))}
            for lid in want_by_id
        ]
        granted, dropped_ids = solve_layer_budgets(
            specs, int(budget_chars), protected=NEVER_DROP_LAYERS)
    dropped_set = set(dropped_ids)

    prompt_parts = []
    debug_layers = []
    # Q 分层缓存:按 tier 收集已发出的层块(段内仍保持上面的 priority 降序)。
    # 注意 prompt(扁平串)继续按 priority 顺序拼接 → 1a 行为零变化;prompt_segments 是
    # 附加产出,1b 才由 master/backends 据此构造多 block + cache_control 断点。
    seg_parts: dict[str, list[str]] = {"A": [], "B": [], "C": []}
    for layer in all_layers:
        lid = layer["id"]
        if lid in dropped_set:
            continue
        cap = granted.get(lid, want_by_id.get(lid, 0))
        raw = (layer["content"] or "").strip()
        trimmed = _trim(layer["content"], cap)
        if not trimmed:
            continue
        part = f"【{layer['title']}】\n{trimmed}"
        prompt_parts.append(part)
        tier = layer_cache_tier(layer)  # 始终返回 A/B/C
        seg_parts[tier].append(part)
        debug_layers.append({
            "id": lid,
            "title": layer["title"],
            "chars": len(trimmed),
            # 截断可观测(v1.82.0):在此之前「这层被砍了」只以正文里一句中文标记存在,
            # 统计不了、告警不了。context_runs 表已存 debug,补这三个字段就能直接查
            # 「哪一层最常被砍、平均砍掉多少字」。
            "original_chars": len(raw),
            "budget_chars": cap,
            "truncated": len(raw) > cap,
            "budget_registered": lid not in unregistered,
            "estimated_tokens": _estimate_tokens(trimmed),
            "sticky": layer.get("sticky", False),
            "priority": layer.get("priority", 50),
            "cache_tier": tier,
            "source": layer.get("source", ""),
            "preview": _preview(trimmed),
            "items": layer.get("items", []),
        })

    prompt = "\n\n".join(prompt_parts)
    # A→B→C 段;空段省略。每段是该 tier 内所有层块按 priority 降序拼接。
    prompt_segments = [
        {"tier": t, "text": "\n\n".join(seg_parts[t]),
         "estimated_tokens": _estimate_tokens("\n\n".join(seg_parts[t]))}
        for t in ("A", "B", "C") if seg_parts[t]
    ]
    cache_plan = _cache_plan(debug_layers, prompt_parts)
    debug = {
        "total_chars": len(prompt),
        "estimated_tokens": _estimate_tokens(prompt),
        "layers": debug_layers,
        "cache_plan": cache_plan,
        "tier_tokens": {t: _estimate_tokens("\n\n".join(seg_parts[t])) for t in ("A", "B", "C")},
        # v1.82.0:预算求解报告。budget_chars=0 表示没做求解(拿不到模型窗口 → 旧行为)。
        "budget": {
            "budget_chars": int(budget_chars or 0),
            "want_total_chars": sum(want_by_id.values()),
            "granted_total_chars": sum(granted.get(i, 0) for i in want_by_id),
            "dropped_layers": dropped_ids,
            "unregistered_layers": unregistered,
            "truncated_layers": [d["id"] for d in debug_layers if d.get("truncated")],
        },
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
    return {"prompt": prompt, "prompt_segments": prompt_segments, "debug": debug}
