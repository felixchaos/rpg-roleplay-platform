"""console_assistant.tools — 工具表 + dispatcher 入口。"""
from __future__ import annotations

from typing import Any, Callable

from tools_dsl.command_dispatcher import (
    ToolCallEnvelope, ToolDispatcher, ToolResult, get_registry,
)


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
        # 设置
        "select_model", "set_preference", "list_available_models",
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
    """统一入口:把一次工具调用包装成 ToolCallEnvelope 走 dispatcher。"""
    dispatcher = ToolDispatcher(
        registry=get_registry(),
        state_provider=state_provider or (lambda env: None),
    )
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
    return dispatcher.dispatch_sync(env)
