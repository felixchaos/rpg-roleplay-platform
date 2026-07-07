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
    from gm_serving import settings as _set
    from tools_dsl.command_tools_kb import _save_ctx

    ctx = _save_ctx(db, save_id, user_id)
    if not ctx:
        return {"error": "无权访问该存档", "injection_text": "", "kb_tools": []}
    script_id = ctx["script_id"]
    if not script_id:
        # 无剧本存档(酒馆未绑剧本/自由模式):常驻世界书/steering/KB 全是 script 域数据,
        # 无可注入。不早退的话 script_id=None 会在下游 int() 处炸掉,Phase D 整轮静默跳过。
        level = IM.classify_impact(user_input)
        return {
            "injection_text": "", "tokens": 0, "budget": 0, "steering": {},
            "impact": {"level": level, "needs_offband_sim": IM.needs_offband_sim(level)},
            "kb_tools": [],
            "loop_guards": {"max_turns": MAX_GM_TOOL_TURNS, "max_budget_usd": MAX_GM_BUDGET_USD},
            "_ctx": ctx,
        }

    save_settings = _set.read_settings(db, save_id)
    steering_strength = save_settings.get("steering_strength", "guided")

    # M5(进度信号矩阵审计):此前用 _save_ctx 的裸标量 ctx["progress_chapter"]
    # (command_tools_kb._save_ctx 只读 worldline.progress_chapter 原始值,default=1,
    # 不做 P4 前沿派生/floor clamp)。read_settings 已把 floor(已到达锚点最大原著章)/
    # derived(前沿派生)/史官估章三者取 max 合并为权威进度(见 gm_serving/settings.py
    # read_settings),steer 应采用这个权威值,而非绕过它的裸标量。裸标量只作 fallback
    # (read_settings 异常/未返回时)。
    _progress_chapter = save_settings.get("progress_chapter")
    if _progress_chapter is None:
        _progress_chapter = ctx["progress_chapter"]

    steer = ST.resolve_steering_target(
        db, save_id=save_id, script_id=script_id, progress_chapter=_progress_chapter,
        steering_strength=steering_strength,
    )
    inj = CI.build_injection(
        db, script_id=script_id, scene_summary=scene_summary,
        steering_hint=steer.get("soft_goal", ""),
        steering_strength=steering_strength,
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
