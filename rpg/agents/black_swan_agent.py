"""agents.black_swan_agent — 主动触发世界事件的子代理。

5 层 validator 管线 (设计 ready,本次 MVP 实现 Layer 1+2+3a+3c+3d+4+5):
1. 现实切片快照
2. native tool_use 强 schema
3. 5 个 validator (本次 3 个: 3a token blacklist / 3c hard constraint / 3d timeline anchor;
   3b NPC presence / 3e independent critic 留接口)
4. retry (max 2 次)
5. dispatcher 落地,origin="autonomous_agent"

触发方式: Path B (post-GM chat handler hook)。
"""
from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Callable
from typing import Any, cast

log = logging.getLogger(__name__)

# ─── Layer 1: 现实切片 ────────────────────────────────────────────

def reality_snapshot(state: Any, script_id: int | None = None) -> dict:
    """暴露当前剧情快照给 LLM,不含敏感字段。

    返回字段:
      - current_phase: str
      - current_location: str
      - current_time: str
      - active_npcs: list[dict] (id + name + disposition)
      - locked_variables: dict[str, str]
      - recent_events: list[str] (最近 5 条 known_events)
      - chapter_window: dict (script_id 时含 chapter_min/max)
    """
    data = getattr(state, 'data', {}) or {}
    world = data.get('world', {}) or {}
    timeline = world.get('timeline', {}) or {}
    player = data.get('player', {}) or {}
    worldline = data.get('worldline', {}) or {}

    # locked vars (user 硬约束)
    locked_vars: dict[str, str] = {}
    user_vars = worldline.get('user_variables', {}) or {}
    for key, info in user_vars.items():
        if isinstance(info, dict) and info.get('locked'):
            locked_vars[key] = info.get('value', '')

    # active NPCs (轻量索引)
    active_entities = data.get('active_entities', []) or []
    active_npcs = [
        {
            'id': str(e.get('id', '')),
            'name': str(e.get('name', '')),
            'disposition': str(e.get('disposition', 'unknown')),
            'kind': str(e.get('kind', 'unknown')),
        }
        for e in active_entities
        if e.get('kind') in ('npc', 'enemy', 'unknown') or not e.get('kind')
    ][:8]  # 最多 8 个

    # recent events
    known_events = world.get('known_events', []) or []
    recent_events = [str(e) for e in known_events[-5:]]

    return {
        'current_phase': str(timeline.get('current_phase', '')),
        'current_location': str(player.get('current_location', '')),
        'current_time': str(world.get('time', '')),
        'active_npcs': active_npcs,
        'locked_variables': locked_vars,
        'recent_events': recent_events,
        'chapter_window': {
            'min': timeline.get('chapter_min'),
            'max': timeline.get('chapter_max'),
        },
        'turn': data.get('turn', 0),
    }


# ─── Layer 2: 强 schema tool_use ────────────────────────────────

def proposal_tool_schema(snapshot: dict) -> dict:
    """生成 LLM tool_use schema,enum 限定 phase/character/location 取值。

    返回 Anthropic tool_use 兼容的 dict (name + description + input_schema)。
    """
    # 从 snapshot 抽 enum
    valid_npc_ids = [n['id'] for n in snapshot.get('active_npcs', []) if n.get('id')]

    return {
        "name": "propose_black_swan_event",
        "description": (
            "Propose a black swan event for the current game phase. "
            "Use ONLY entities, locations, and concepts that appear in the snapshot. "
            "DO NOT invent new NPCs, locations, or cross-phase events. "
            "If no suitable event fits the current situation, return event_kind='no_op'."
        ),
        "input_schema": {
            "type": "object",
            "required": ["event_kind", "summary"],
            "properties": {
                "event_kind": {
                    "type": "string",
                    "enum": ["new_event", "npc_action", "environment_change", "no_op"],
                },
                "summary": {
                    "type": "string",
                    "description": "1-2 sentence narrative summary (Chinese OK)",
                    "maxLength": 200,
                },
                "involved_npcs": {
                    "type": "array",
                    "items": {"type": "string", "enum": valid_npc_ids or [""]},
                    "default": [],
                },
                "location": {
                    "type": "string",
                    "description": "Must be current_location or a sub-location of it",
                },
                "tools_to_call": {
                    "type": "array",
                    "description": "Optional: tool calls to dispatch (e.g. upsert_active_entity)",
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {"type": "string"},
                            "args": {"type": "object"},
                        },
                        "required": ["tool"],
                    },
                    "default": [],
                },
            },
        },
    }


# ─── Layer 3a: token 黑名单 ──────────────────────────────────────

def validator_token_blacklist(
    proposal: dict, snapshot: dict, script_overrides: dict | None = None
) -> tuple[bool, str]:
    """检查 proposal 文本是否含跨 phase 的不允许 token。

    从 modules/_script_overrides/<key>.json 的 phase_inference rules 推断:
    如果 proposal.summary 含其他 phase 的 needles,reject。

    返回 (passed, reason)。
    """
    summary = (proposal.get('summary') or '').strip()
    if not summary:
        return True, ""  # empty summary 算 noop

    if not script_overrides:
        return True, ""  # 无 overrides 通过 (通用底座默认允许)

    current_phase = snapshot.get('current_phase') or ''
    if not current_phase:
        return True, ""  # 无 phase 信息无法验证

    rules = (script_overrides.get('phase_inference') or {}).get('rules') or []
    # 找当前 phase 之外的 needles → 黑名单
    blacklist: list[str] = []
    for rule in rules:
        if rule.get('phase') and rule['phase'] != current_phase:
            blacklist.extend(rule.get('or_text_needles') or [])

    for token in blacklist:
        if token in summary:
            return False, f"含跨 phase token '{token}' (当前 {current_phase})"
    return True, ""


# ─── Layer 3c: 硬约束 critic (简化版) ─────────────────────────────

def validator_hard_constraints(proposal: dict, snapshot: dict) -> tuple[bool, str]:
    """检查 proposal 是否违反 user locked variables。

    locked_variables 是玩家用 /set 锁定的硬约束,黑天鹅事件不得违反。

    简化版: 检查 summary 是否包含某 locked var key 的反义/否定模式。
    完整版应接 LLM critic,这里留接口。
    """
    locked = snapshot.get('locked_variables') or {}
    if not locked:
        return True, ""

    summary = (proposal.get('summary') or '').strip()
    if not summary:
        return True, ""

    # 简化: 如果 locked var 的 value 在 summary 中被否定 (出现"不"/"没"/"非"近邻)
    for key, value in locked.items():
        if not value:
            continue
        # 粗略匹配: value 后或前 5 字内出现否定词
        for neg in ("不", "没", "非", "未"):
            pat = re.compile(rf"{neg}.{{0,3}}{re.escape(str(value))}|{re.escape(str(value))}.{{0,3}}{neg}")
            if pat.search(summary):
                return False, f"violates locked var: {key}={value}"
    return True, ""


# ─── Layer 3d: timeline 锚点 ─────────────────────────────────────

def validator_timeline_anchor(proposal: dict, snapshot: dict) -> tuple[bool, str]:
    """检查 proposal 涉及的 NPC 是否都在 active_npcs 列表里 (即当前 phase 在场)。

    proposal.involved_npcs 必须 ⊆ snapshot.active_npcs.ids。
    """
    proposed_npcs = set(proposal.get('involved_npcs') or [])
    if not proposed_npcs:
        return True, ""

    active_ids = {n['id'] for n in snapshot.get('active_npcs', []) if n.get('id')}

    invalid = proposed_npcs - active_ids
    if invalid:
        return False, f"涉及未在场 NPC: {invalid}"
    return True, ""


# ─── Layer 3b/3e: 留接口 (TODO) ──────────────────────────────────

def validator_npc_presence(
    proposal: dict, snapshot: dict, script_id: int | None
) -> tuple[bool, str]:
    """TODO: 接 script_character_cards.available_in_phases 字段。

    当前 short-circuit 通过。等剧本元数据扩展后实现。
    """
    return True, ""  # TODO


def validator_independent_critic(proposal: dict, snapshot: dict) -> tuple[bool, str]:
    """TODO: 接 independent LLM critic (二次 LLM 评分一致性)。

    当前 short-circuit 通过。需要 prompt tuning + LLM 调用。
    """
    return True, ""  # TODO


# ─── 全套 validator 跑 ────────────────────────────────────────────

def run_validators(
    proposal: dict, snapshot: dict,
    script_id: int | None, script_overrides: dict | None
) -> list[tuple[str, bool, str]]:
    """跑所有 validator,返回 [(name, passed, reason), ...]"""
    return [
        ("3a_token_blacklist", *validator_token_blacklist(proposal, snapshot, script_overrides)),
        ("3b_npc_presence", *validator_npc_presence(proposal, snapshot, script_id)),
        ("3c_hard_constraints", *validator_hard_constraints(proposal, snapshot)),
        ("3d_timeline_anchor", *validator_timeline_anchor(proposal, snapshot)),
        ("3e_independent_critic", *validator_independent_critic(proposal, snapshot)),
    ]


# ─── Layer 5: dispatcher 落地 ───────────────────────────────────

def dispatch_event(
    proposal: dict, state: Any,
    user_id: int, save_id: int, script_id: int | None,
) -> list[dict]:
    """把 proposal.tools_to_call 通过 dispatcher 落地。

    origin="autonomous_agent", trace_id="swan-<token>"。
    """
    tools_to_call = proposal.get('tools_to_call') or []
    if not tools_to_call:
        return []

    from tools_dsl.command_dispatcher import (
        ToolCallEnvelope,
        ToolDispatcher,
        get_registry,
    )

    trace_id = f"swan-{secrets.token_urlsafe(6)}"
    _sp = cast(Callable[[Any], Any], lambda env, _s=state: _s)
    dispatcher = ToolDispatcher(
        registry=get_registry(),
        state_provider=_sp,
    )
    results = []
    for call in tools_to_call:
        tool_name = call.get('tool') or ""
        args = call.get('args') or {}
        if not tool_name:
            continue
        env = ToolCallEnvelope(
            user_id=user_id,
            save_id=save_id,
            script_id=script_id,
            tool=tool_name,
            args=args,
            origin="autonomous_agent",
            trace_id=trace_id,
        )
        res = dispatcher.dispatch_sync(env)
        results.append({
            "tool": tool_name,
            "ok": res.ok,
            "result": res.result if res.ok else res.error,
        })
    return results


# ─── Introspection tools (multi-turn tool loop 自检工具) ────────────

def introspection_tools_schema(snapshot: dict) -> list[dict]:
    """LLM 可在 propose 前调用的自检工具。"""
    return [
        {
            "name": "check_npc_active",
            "description": "查询某 NPC 是否在当前 phase 活跃。返回 active=true/false + 在场 NPC 列表。",
            "input_schema": {
                "type": "object",
                "required": ["npc_id"],
                "properties": {"npc_id": {"type": "string"}},
            },
        },
        {
            "name": "check_locked_var",
            "description": "查询某个 locked variable 的当前值。返回 locked/value/exists。",
            "input_schema": {
                "type": "object",
                "required": ["key"],
                "properties": {"key": {"type": "string"}},
            },
        },
    ]


def handle_introspection_tool(name: str, args: dict, snapshot: dict) -> dict:
    """处理 introspection 工具调用,返回 dict(JSON-serializable)。"""
    if name == "check_npc_active":
        target = args.get("npc_id") or ""
        npcs = snapshot.get("active_npcs", []) or []
        ids = [n.get("id") for n in npcs]
        return {
            "active": target in ids,
            "available_ids": ids,
            "active_count": len(ids),
        }
    if name == "check_locked_var":
        key = args.get("key") or ""
        locked = snapshot.get("locked_variables") or {}
        return {
            "exists": key in locked,
            "locked": key in locked,
            "value": locked.get(key, ""),
            "all_keys": list(locked.keys()),
        }
    return {"error": f"unknown tool: {name}"}


# ─── Harness LLM caller ─────────────────────────────────────────

_SWAN_SYSTEM_PROMPT = """\
你是 RPG 世界事件子代理。你的唯一任务是在玩家闲置/转场时,提出一个符合
当前现实切片(snapshot)的"黑天鹅事件",可能让世界在玩家不发声时也产生
合理变化。

铁律:
1. **只使用 snapshot.active_npcs 里的 NPC**;不要捏造新角色。
2. **只使用 snapshot.current_location 或其子区域**;不要瞬移到其它 phase 的地点。
3. **不能违反 snapshot.locked_variables**(玩家用 /set 锁住的硬约束)。
4. **不能与 snapshot.recent_events 重复**。
5. 若无合适事件,event_kind 填 "no_op"。

必须通过 propose_black_swan_event 工具输出,不要写自然语言。
"""


def _build_swan_user_prompt(snapshot: dict,
                            prev_failure: list[tuple[str, bool, str]] | None) -> str:
    """组装 swan agent 的 user prompt:snapshot + 上次失败的 validator 反馈。"""
    import json as _json
    parts = [
        "## 当前现实切片",
        _json.dumps(snapshot, ensure_ascii=False, indent=2),
    ]
    if prev_failure:
        parts.extend([
            "",
            "## 上一次提议被拒原因(请避免重复):",
        ])
        for name, passed, reason in prev_failure:
            if not passed:
                parts.append(f"- [{name}] {reason}")
    parts.extend([
        "",
        "请提出一个**单一**黑天鹅事件,使用 propose_black_swan_event 工具输出。",
    ])
    return "\n".join(parts)


def _make_harness_caller(
    *,
    user_id: int,
    api_id_override: str | None = None,
    model_override: str | None = None,
    use_tool_loop: bool = True,
) -> Callable[..., dict] | None:
    """返回一个 callable(snapshot, schema, prev_failure=None) -> proposal_dict。

    use_tool_loop=True + anthropic provider → multi-turn tool loop(LLM 可先自检再 propose)。
    其它 provider 或 use_tool_loop=False → 单次 call_agent_json。
    出问题(无凭证/import 失败)→ 返回 None,maybe_trigger 跳过。
    """
    try:
        from agents._harness import call_agent_json, resolve_api_and_model
    except Exception:
        return None

    api_id, model = resolve_api_and_model(
        user_id,
        api_pref_key="black_swan_agent.api_id",
        model_pref_key="black_swan_agent.model_real_name",
        api_id_override=api_id_override,
        model_override=model_override,
    )

    def _call_single_shot(
        snapshot: dict, schema: dict,
        prev_failure: list[tuple[str, bool, str]] | None = None,
    ) -> dict:
        user_prompt = _build_swan_user_prompt(snapshot, prev_failure)
        text, _usage = call_agent_json(
            api_id=api_id,
            model=model,
            system_prompt=_SWAN_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            user_id=user_id,
            tool_schema=schema,  # 三通道都启用强 schema(enum 锁 NPC ID 等)
            max_tokens=600,
            timeout_sec=20,
            agent_kind="black_swan",
        )
        # text 来自 tool_use input JSON(anthropic)或 JSON 字符串(其它通道)
        try:
            import json as _json
            obj = _json.loads(text) if text else {}
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    # tool_loop 仅 anthropic 支持
    if use_tool_loop and api_id == "anthropic":
        def _call_loop(
            snapshot: dict, schema: dict,
            prev_failure: list[tuple[str, bool, str]] | None = None,
        ) -> dict:
            user_prompt = _build_swan_user_prompt(snapshot, prev_failure)
            intro_tools = introspection_tools_schema(snapshot)
            all_tools = intro_tools + [schema]
            try:
                from agents._harness import call_agent_tool_loop
                args, _usage, _trace = call_agent_tool_loop(
                    api_id=api_id,
                    model=model,
                    system_prompt=(
                        _SWAN_SYSTEM_PROMPT
                        + "\n\n你可以在提议前先调用 check_npc_active / check_locked_var"
                          " 自检,确认无误后再调 propose_black_swan_event 提交最终事件。"
                    ),
                    user_prompt=user_prompt,
                    user_id=user_id,
                    tools=all_tools,
                    terminal_tool_name=schema["name"],
                    tool_handler=lambda name, a: handle_introspection_tool(name, a, snapshot),
                    max_iterations=4,
                    max_tokens=800,
                    agent_kind="black_swan",
                )
                return args or {"event_kind": "no_op", "summary": ""}
            except Exception as exc:
                log.warning(f"[black_swan] tool_loop failed, fallback to single-shot: {exc}")
                return _call_single_shot(snapshot, schema, prev_failure)

        return _call_loop

    # 非 anthropic 或 use_tool_loop=False → 原单次 call
    return _call_single_shot


# ─── 入口: maybe_trigger ─────────────────────────────────────────

def maybe_trigger(
    state: Any,
    *,
    user_id: int,
    save_id: int,
    script_id: int | None = None,
    max_retries: int = 2,
    llm_caller: Any | None = None,
    api_id_override: str | None = None,
    model_override: str | None = None,
    enable_llm: bool = True,
) -> dict:
    """post-GM hook 调用入口。

    LLM 调用优先级(harness 适配):
    1. 显式传入 `llm_caller` 回调 → 使用回调(兼容老 caller / 测试)
    2. `enable_llm=True` 且 user_id 有 → 内部走 `agents._harness.call_agent_json`
       走 Anthropic native tool_use(用 proposal_tool_schema 作 input_schema 强校验)
    3. `enable_llm=False` 或无凭证 → 直接跳过(no llm)

    返回结果 dict:
      - triggered: bool (是否成功产生 + dispatch 了 black swan event)
      - proposal: dict (最终被接受的 proposal,或最后一次 reject 的 proposal)
      - validator_results: list (Layer 3 检查结果)
      - dispatch_results: list (Layer 5 落地结果)
      - retries: int (实际重试次数)
      - reason: str (跳过/失败原因)
    """
    snapshot = reality_snapshot(state, script_id)

    # 选定 LLM 通道:外部 callable 优先,否则使用 harness 自调,enable_llm=False 跳过
    effective_caller = llm_caller
    harness_used = False
    if effective_caller is None and enable_llm and user_id:
        effective_caller = _make_harness_caller(
            user_id=user_id,
            api_id_override=api_id_override,
            model_override=model_override,
        )
        harness_used = effective_caller is not None
    if effective_caller is None:
        return {
            "triggered": False,
            "reason": "no llm_caller and harness disabled or unavailable",
            "snapshot": snapshot,
        }
    llm_caller = effective_caller  # below loop uses llm_caller name

    # 加载 script overrides (Layer 3a 需要)
    script_overrides = None
    if snapshot.get('current_phase'):
        try:
            from state.core import _load_script_overrides
            all_overrides = _load_script_overrides()
            # 找含当前 phase 的 override
            for _key, ov in all_overrides.items():
                rules = (ov.get('phase_inference') or {}).get('rules') or []
                if any(r.get('phase') == snapshot['current_phase'] for r in rules):
                    script_overrides = ov
                    break
        except Exception:
            pass

    schema = proposal_tool_schema(snapshot)

    last_proposal: dict | None = None
    last_validator_results: list = []

    for attempt in range(max_retries + 1):
        # Layer 2: LLM call with strong schema
        try:
            proposal = llm_caller(
                snapshot, schema,
                prev_failure=last_validator_results if attempt > 0 else None
            )
        except Exception as e:
            return {
                "triggered": False,
                "reason": f"llm_caller failed: {e}",
                "snapshot": snapshot,
                "retries": attempt,
            }

        if not proposal or proposal.get('event_kind') == 'no_op':
            return {
                "triggered": False,
                "reason": "llm chose no_op",
                "proposal": proposal,
                "snapshot": snapshot,
                "retries": attempt,
            }

        last_proposal = proposal
        # Layer 3: validators
        last_validator_results = run_validators(proposal, snapshot, script_id, script_overrides)
        all_passed = all(v[1] for v in last_validator_results)

        if all_passed:
            # Layer 5: dispatch
            dispatch_results = dispatch_event(
                proposal, state,
                user_id=user_id, save_id=save_id, script_id=script_id,
            )
            return {
                "triggered": True,
                "proposal": proposal,
                "validator_results": last_validator_results,
                "dispatch_results": dispatch_results,
                "retries": attempt,
                "snapshot": snapshot,
                "harness_used": harness_used,
            }
        # Layer 4: retry feedback — loop continues

    # max_retries 用完
    return {
        "triggered": False,
        "reason": f"validators rejected after {max_retries + 1} attempts",
        "proposal": last_proposal,
        "validator_results": last_validator_results,
        "retries": max_retries + 1,
        "snapshot": snapshot,
    }
