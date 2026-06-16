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


def list_assistant_tools() -> list[dict[str, Any]]:
    """返回 console_assistant 给 LLM 看的工具列表。"""
    from tools_dsl.chat_tool_router import DISPATCHER_SENTINEL
    PRIMARY = {
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
        # MD 编辑器(剧本知识资产):直写库(script scope,严格 owner 闸 + 二次确认)
        "update_script_chapter", "upsert_worldbook_entry", "update_npc_card",
        "update_anchor", "upsert_canon_entity",
        # 设置
        "select_model", "set_preference", "list_available_models",
        # 游戏状态查询 (task 48: console_assistant 读当前 save 状态)
        "get_game_state",
        # 询问 + 长尾发现 + 导航
        "ask_user_choice",  # 等同 AskUserQuestion
        "ui_describe",      # 长尾工具发现
        "navigate_to_setting",
        # task 109b: UI Action — 代用户填表/点按钮 (零代码自动适配新页面)
        "ui_describe_page",  # 主动看页面结构 (实际 atlas 已在 system prompt)
        "ui_set_field",      # 填表单字段
        "ui_click",          # 点按钮 (destructive, default 模式会要求 confirm)
    }
    out: list[dict[str, Any]] = []
    for spec in get_registry().list_for_origin("console_assistant"):
        if spec.name not in PRIMARY:
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
