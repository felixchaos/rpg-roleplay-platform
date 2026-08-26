"""console_assistant.tools — 工具表 + dispatcher 入口。"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from tools_dsl.command_dispatcher import (
    ToolCallEnvelope,
    ToolDispatcher,
    ToolResult,
    get_registry,
)

# 进程级 dispatcher 单例 — 关键：旧实现每次 dispatch 都 new ToolDispatcher,
# 导致 _rate_buckets / _trace_seen 全为空, MAX_CALLS_PER_USER_PER_SECOND=20
# 和 trace 去重保护完全失效。单例后限流和 trace_seen 才真正生效。
# 注意: state_provider 会随每次请求变化, 因此把 state_provider 改成 per-call
# 通过 ToolCallEnvelope 注入路径（如果 dispatcher 支持），否则用一个动态包装。
_DISPATCHER_SINGLETON: ToolDispatcher | None = None
_DISPATCHER_LOCK = threading.Lock()
_CURRENT_STATE_PROVIDER: Callable[[ToolCallEnvelope], Any] | None = None


def _state_provider_proxy(env: ToolCallEnvelope) -> Any:
    """thread-local 不可用（FastAPI 跨线程），用 contextvars 也复杂；
    单例 dispatcher 通过这个 proxy 拿到当前请求绑定的 state_provider。
    每次 dispatch_assistant_tool 调用前在锁内 set 当前 provider, 调完清空。
    """
    if _CURRENT_STATE_PROVIDER is None:
        return None
    return _CURRENT_STATE_PROVIDER(env)


def _get_dispatcher() -> ToolDispatcher:
    global _DISPATCHER_SINGLETON
    if _DISPATCHER_SINGLETON is None:
        with _DISPATCHER_LOCK:
            if _DISPATCHER_SINGLETON is None:
                _DISPATCHER_SINGLETON = ToolDispatcher(
                    registry=get_registry(),
                    state_provider=_state_provider_proxy,
                )
    return _DISPATCHER_SINGLETON


# ── 助手工具的「面」划分(v1.82.1)──────────────────────────────────────────
# 在此之前这里是一份硬编码名单 `PRIMARY`,两个面貌完全不同的 agent 共用它:平台控制台助手
# 与剧本编辑器右栏的写作搭档(面的定义见 console_assistant/surfaces.py)。
#
# 问题不在名单本身,在**名单之外没有出口**:一个工具注册时明明声明了
# `origin="console_assistant"`,只因为名字没进名单就静默不可见 —— 不报错、不告警、
# 无从诊断。`get_chapter_facts` / `get_worldbook` 就是这样在编辑器里失踪的
# (记录见 docs/knowledge,现由本文件下方的守卫兜住)。这与 GM 侧直发工具窗口那个
# 「按名字查表授权,查不到就静默降级」是同一族缺陷。
#
# 现在每个注册给本 origin 的工具**必须**落进下面四个集合之一,否则守卫测试
# rpg/tests/unit/test_assistant_tool_surfaces.py 直接红:
#   SHARED         两个面都给
#   EDITOR_ONLY    只给编辑器写作搭档
#   CONSOLE_ONLY   只给平台控制台助手
#   NOT_SERVED     刻意不给任何面(必须写清理由)

SHARED: frozenset[str] = frozenset({
        # 角色卡
        "create_character_card", "list_my_character_cards", "delete_character_card",
        "generate_character_card_draft", "refine_character_card_draft",
        # persona
        "create_persona", "list_my_personas", "delete_persona",
        # 存档
        "create_save", "list_my_saves", "activate_save", "delete_save", "delete_saves", "rename_save",
        # 新建存档向导 — 推荐初始身份
        "recommend_player_identity",
        # 用量统计 (task 119)
        "list_my_usage",
        # 剧本
        "list_scripts",
        # MD 编辑器(剧本知识资产):读
        "get_script_chapters", "list_script_npcs", "get_script_character_card",
        "list_worldbook_entries", "list_anchors", "list_canon_entities",
        "get_chapter_context", "get_chapter_text", "search_manuscript", "extract_from_selection",
        # MD 编辑器(剧本知识资产):直写库(script scope,严格 owner 闸 + 二次确认)
        "update_script_chapter", "upsert_worldbook_entry", "upsert_worldbook_entries", "update_npc_card",
        "update_anchor", "create_anchor", "upsert_canon_entity",
        # 增删缺口补齐:新建章节 / 新建 NPC 卡 / 删除世界书·锚点
        "create_script_chapter", "create_npc_card", "delete_worldbook_entry", "delete_anchor",
        # 拖入文档 → 确定性拆章 / 读片段(原文不进上下文)
        "read_uploaded_document", "preview_document_split", "import_document_as_chapters",
        # 写作委派:派用户自己 BYOK 的子模型写一段/做特定任务
        "delegate_writing_task",
        # 设置
        "select_model", "set_preference", "list_available_models",
        # 游戏状态查询 (task 48: console_assistant 读当前 save 状态)
        "get_game_state",
        # cowork 写作搭档:向作者展示结构化计划 / 审稿问题(右栏面板;informational,不写库)
        "set_writing_plan", "report_writing_issues",
        # 询问 + 长尾发现 + 导航
        "ask_user_choice",  # 等同 AskUserQuestion
        "ui_describe",      # 长尾工具发现
        "navigate_to_setting",
        # task 109b: UI Action — 代用户填表/点按钮 (零代码自动适配新页面)
        "ui_describe_page",  # 主动看页面结构 (实际 atlas 已在 system prompt)
        "ui_set_field",      # 填表单字段
        "ui_click",          # 点按钮 (destructive, default 模式会要求 confirm)
    })

# 只给编辑器写作搭档 —— 剧本资产域,控制台用不上。
EDITOR_ONLY: frozenset[str] = frozenset({
    # 剧本知识资产读:章节事实表 / 世界书。**这两个就是记录在案的失踪工具** ——
    # 编辑器的系统提示词一直在教 agent「动笔前先把设定吃透」,而它够不到设定。
    "get_chapter_facts", "get_worldbook",
    # 自由文本提问。SHARED 里有 ask_user_choice(选项式)却没有它 —— 问书名 / 角色名 /
    # 一句话设定这类,选项式答不了,只能让 agent 改用旁白提问然后猜作者的回答。
    "ask_user_text",
    # 知识库维护:编辑器已有「知识库中心」抽屉(EditorKbPanel,作者点得到),agent 却调不到
    # 同一批动作 —— 面板能点、搭档不能,是同一个不对称的另一半。两者 destructive=True,
    # 走既有二次确认闸。
    "rebuild_script_module", "resplit_script",
    # 导入进度:编辑器有拖入文档拆章的入口(import_document_as_chapters 在 SHARED),
    # 却查不了自己刚触发的任务进度、也取消不了。
    "get_import_status", "list_my_import_jobs", "cancel_import_job",
})

# 只给平台控制台助手。目前为空 —— **刻意不做减法**:从编辑器面移除平台工具
# (管存档 / 改设置 / 页面导航)是行为变更,风险不对称,留给产品拍板。
# 现状因此是「编辑器 = SHARED + EDITOR_ONLY」,纯增量,零回归。
CONSOLE_ONLY: frozenset[str] = frozenset()

# 刻意不给任何面。每一组都要写清为什么 —— 这份清单的价值就在于「不给」是个决定,
# 而不是一次遗忘。
NOT_SERVED: frozenset[str] = frozenset({
    # 游戏运行时(GM 域):读写的是某个**存档**的活态世界,不是剧本资产
    "activate_branch", "ask_player_choice", "check_pending_anchor_drift",
    "claim_protagonist_pov", "continue_branch", "delete_branch", "get_current_scene",
    "get_known_events", "get_pending_questions", "get_pending_writes", "get_save_detail",
    "get_user_variables", "graph_neighbors", "kb_record_event", "kb_set_relationship",
    "kb_set_worldline_var", "kb_upsert_entity", "list_branches", "list_modules",
    "list_pending_anchors", "list_recent_history", "list_relationships", "lookup_entity",
    "lookup_timeline", "mark_anchor_satisfied", "mark_anchor_superseded", "phase_advance",
    "phase_list", "phase_rebuild", "query_memory", "record_history_anchor",
    "revoke_protagonist_pov", "schedule_consequence", "search_canon", "summarize_anchors",
    "worldbook_add", "worldbook_list_save_overlay", "worldbook_retire",
    # 酒馆域:角色扮演运行时资产,与剧本编辑是设计分野(见 project_editor_copilot_parity「边界澄清」)
    "clone_npc_to_user_card", "edit_tavern_character", "export_character_card",
    "import_attached_script", "import_character_card", "read_attached_text",
    "set_tavern_character", "set_tavern_immersive", "set_tavern_persona",
    "switch_tavern_persona_card", "tavern_bind_script", "tavern_list_scripts",
    # 平台/账户元信息:助手不需要,也不该替用户翻账本
    "get_my_stats", "get_my_usage", "list_available_tools", "list_my_credentials_meta",
    "probe_models", "recent_audit_log",
    # 不可逆且无审阅路径:必须走用户自己的 UI
    "delete_script",
    # 建新剧本属控制台流程:编辑器里作者已经身处某个剧本内,不该从这里开新的
    "start_script_import",
})


def _surface_names(surface: str) -> frozenset[str]:
    """某个面能看到的工具名集合。未知 surface 退化成 console(最保守)。"""
    if surface == "editor":
        return SHARED | EDITOR_ONLY
    return SHARED | CONSOLE_ONLY


def list_assistant_tools(surface: str = "console") -> list[dict[str, Any]]:
    """返回 console_assistant 给 LLM 看的工具列表。

    surface: "console"(平台控制台助手,默认)/ "editor"(剧本编辑器写作搭档)。
             判据见 console_assistant.surfaces.surface_of(page_context)。
    """
    from tools_dsl.chat_tool_router import DISPATCHER_SENTINEL
    allowed = _surface_names(surface)
    out: list[dict[str, Any]] = []
    for spec in get_registry().list_for_origin("console_assistant"):
        if spec.name not in allowed:
            continue
        out.append({
            "server_id": DISPATCHER_SENTINEL,
            "name": spec.name,
            "description": spec.description + (
                "\n示例:\n" + "\n".join(
                    f"  调用 {spec.name}(" + ", ".join(
                        f"{k}={repr(v)}" for k, v in ex.items()
                    ) + ")"
                    for ex in (spec.input_examples or ())[:2]
                ) if spec.input_examples else ""
            ),
            "schema": spec.input_schema,
            "destructive": spec.destructive,
            "scope": spec.scope,
        })
    return out


def get_tool_spec(name: str):
    return get_registry().get(name)


def dispatch_assistant_tool(
    *,
    user_id: int,
    tool: str,
    args: dict[str, Any],
    save_id: int | None,
    script_id: int | None,
    trace_id: str,
    call_id: str,
    state_provider: Callable[[ToolCallEnvelope], Any] | None = None,
) -> ToolResult:
    """统一入口:把一次工具调用包装成 ToolCallEnvelope 走 dispatcher (单例)。

    单例化后 dispatcher 内部的 _rate_buckets / _trace_seen 才真正跨调用生效。
    state_provider 通过 _DISPATCHER_LOCK 在 set/dispatch/clear 三段中临时注入。
    """
    global _CURRENT_STATE_PROVIDER
    env = ToolCallEnvelope(
        user_id=user_id,
        save_id=save_id,
        script_id=script_id,
        tool=tool,
        args=args or {},
        origin="console_assistant",
        trace_id=trace_id,
        call_id=call_id,
        depth=1,
    )
    dispatcher = _get_dispatcher()
    with _DISPATCHER_LOCK:
        _CURRENT_STATE_PROVIDER = state_provider or (lambda _env: None)
        try:
            return dispatcher.dispatch_sync(env)
        finally:
            _CURRENT_STATE_PROVIDER = None
