"""gm_serving/serve.py — Phase D GM serving 集成面(供 chat_pipeline 采用)。

把已验证的组件(三层注入①常驻 / 规范世界线 steering / 影响因子)装配成 chat 回合可直接用的
上下文 + 有界 loop 护栏 + GM 可用 KB 工具清单。chat_pipeline.run_gm_phase 采用此面即接通 Phase D。

设计 D_gm_serving.md §1/§3。世界知识读路径 = resolve_world_view(canon∪live);
写路径 = GM 调 kb_* 工具(D-1,走 dispatcher 打 born_commit)。
"""
from __future__ import annotations

# 有界 agentic loop 护栏(D §1):模型出 end_turn 自终止 + 硬上限
MAX_GM_TOOL_TURNS = 3          # 回合内工具调用最多轮数(防绕圈)
MAX_GM_BUDGET_USD = 0.05       # 每回合预算(BYOK 用户掏钱)

# GM 在 chat 回合可用的 KB 工具(注册名,见 command_tools_kb)
GM_KB_QUERY_TOOLS = ("lookup_entity", "search_canon", "lookup_timeline", "graph_neighbors")
GM_KB_WRITE_TOOLS = ("kb_upsert_entity", "kb_record_event", "kb_set_relationship", "kb_set_worldline_var")
# 复用已有收束工具
GM_ANCHOR_TOOLS = ("list_pending_anchors", "mark_anchor_satisfied", "mark_anchor_superseded")
GM_ALL_KB_TOOLS = GM_KB_QUERY_TOOLS + GM_KB_WRITE_TOOLS + GM_ANCHOR_TOOLS


def assemble_gm_context(db, *, save_id: int, user_id: int, user_input: str = "",
                        scene_summary: str = "") -> dict:
    """组装一回合 GM 上下文(第①层常驻 + 软目标),并给出本回合动作的影响分级。

    返回 {injection_text, tokens, budget, steering, impact, world_view, kb_tools}。
    供 chat_pipeline 注入到 GM system/上下文;GM 再按需调 KB 查询/写工具(②③层)。
    """
    from gm_serving import context_inject as CI
    from gm_serving import impact as IM
    from gm_serving import steering as ST
    from tools_dsl.command_tools_kb import _save_ctx

    ctx = _save_ctx(db, save_id, user_id)
    if not ctx:
        return {"error": "无权访问该存档", "injection_text": "", "kb_tools": []}
    script_id = ctx["script_id"]

    steer = ST.resolve_steering_target(
        db, save_id=save_id, script_id=script_id, progress_chapter=ctx["progress_chapter"]
    )
    inj = CI.build_injection(
        db, script_id=script_id, scene_summary=scene_summary,
        steering_hint=steer.get("soft_goal", ""),
    )
    level = IM.classify_impact(user_input)

    return {
        "injection_text": inj["text"],
        "tokens": inj["tokens"],
        "budget": inj["budget"],
        "steering": steer,
        "impact": {"level": level, "needs_offband_sim": IM.needs_offband_sim(level)},
        "kb_tools": list(GM_ALL_KB_TOOLS),
        "loop_guards": {"max_turns": MAX_GM_TOOL_TURNS, "max_budget_usd": MAX_GM_BUDGET_USD},
        "_ctx": ctx,
    }
