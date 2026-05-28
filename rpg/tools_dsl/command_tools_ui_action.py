"""
command_tools_ui_action.py — task 109b-2

3 个 "UI action" 工具, 由 console_assistant 给 LLM 用:

  ui_describe_page()       — 返回当前页面 ui_atlas snapshot (从 pageContext 拿)
  ui_set_field(form_id, field_key, value)  — 填字段 (前端 React state 同步)
  ui_click(form_id, action_label)          — 点按钮

设计要点:
  · 这 3 个工具的 executor 不直接做事 — 它返回一个特殊 dict
    {"__ui_action__": "set_field|click", ...args}
  · console_assistant 主循环检测到 __ui_action__ 后, yield 一个 SSE
    event: ui_action, 前端 console-assistant-panel 监听后调
    window.__UI_ATLAS.setField / click — DOM 直接生效, React state 同步
  · 工具 result 立刻给 LLM 一个 ack 字符串, LLM 不阻塞等前端
  · 因为是"通过用户浏览器代为执行", 权限完全跟随用户在 UI 上能做的事 (用户
    自己能填能点的, agent 才能填能点); 安全靠用户的 permission_mode 控制
    (read_only 时 agent 不能调这些工具, 由前端在 onSet 时检查)。
"""
from __future__ import annotations

from typing import Any

from tools_dsl.command_dispatcher import ToolSpec, get_registry

# 允许所有 origin 调用 — 因为这些工具本质是"代用户点鼠标",
# 安全边界由前端 permission_mode 把关 (agent 写到 form 字段, 用户能看到)。
_UI_ORIGINS = frozenset({
    "ui_button", "api_direct", "llm_set", "llm_chat", "llm_chat_json_op",
    "console_assistant",
})
# ui_click 是 destructive=True 工具 — 安全不变量: destructive 工具不允许 llm_chat。
# llm 可通过 console_assistant / llm_set / ui_button 触发点击,但不能通过 llm_chat 直接调。
_UI_CLICK_ORIGINS = frozenset({
    "ui_button", "api_direct", "llm_set", "llm_chat_json_op",
    "console_assistant",
})


def _t_ui_describe_page(user_id: int, args: dict) -> str:
    """返回当前页面 UI atlas snapshot.

    实际 atlas 由前端 ui-atlas.js 维护, 通过 pageContext.ui_atlas 推到 console_assistant,
    所以 atlas 已经在 LLM 看到的 system prompt 里。这个工具主要是给 LLM 一个
    "我要看一眼当前页面" 的语义入口, 实际返回是 ack 字符串提示。
    """
    return ("当前页面的 UI Atlas 已通过 pageContext.ui_atlas 注入到上下文。"
            "请直接阅读 system prompt 中的 ui_atlas 段,无需重复请求。"
            "atlas 包含: page (路由), open_modals (已打开弹窗), "
            "forms (字段及当前值), top_actions (可点按钮)。")


def _t_ui_set_field(user_id: int, args: dict) -> dict[str, Any]:
    """填某 form 的某字段。返回特殊 __ui_action__ payload 让 console_assistant
    转成 SSE event 推到前端。"""
    form_id = str(args.get("form_id") or "").strip()
    field_key = str(args.get("field_key") or "").strip()
    value = args.get("value")
    if not form_id:
        return "失败: form_id 必填 (从 ui_atlas.forms[].id 拿)"
    if not field_key:
        return "失败: field_key 必填 (从 ui_atlas.forms[].fields[].key 拿)"
    if value is None:
        return "失败: value 必填 (可以是字符串/数字/布尔)"
    # 返回的 dict 会被 console_assistant 主循环识别为 UI action
    return {
        "__ui_action__": "set_field",
        "form_id": form_id,
        "field_key": field_key,
        "value": value,
        "ack": f"已请求前端在 '{form_id}' 上填字段 '{field_key}' = {value!r}",
    }


def _t_ui_click(user_id: int, args: dict) -> dict[str, Any]:
    """点击某 form 的某 action 按钮 (或 global 区按钮)。

    form_id="global" 时 action_label 是 page-level 按钮 (如 "新游戏"、"导入剧本");
    其他 form_id 是 modal 内的按钮 (如 "创建并进入"、"取消")。
    """
    form_id = str(args.get("form_id") or "").strip()
    action_label = str(args.get("action_label") or "").strip()
    if not form_id:
        return "失败: form_id 必填 (modal 内按钮用 modal id, 页面级按钮用 'global')"
    if not action_label:
        return "失败: action_label 必填 (从 ui_atlas.forms[].top_actions[].label 拿)"
    return {
        "__ui_action__": "click",
        "form_id": form_id,
        "action_label": action_label,
        "ack": f"已请求前端在 '{form_id}' 上点击 '{action_label}'",
    }


def register_ui_action_tools() -> None:
    """task 109b: 注册到全局 dispatcher。在 ensure_registered() 里调一次。"""
    reg = get_registry()

    reg.register(ToolSpec(
        name="ui_describe_page",
        description=(
            "查看当前页面的 UI 结构 (forms / 字段 / 按钮)。"
            "实际 atlas 已通过 pageContext 注入 system prompt, "
            "你直接读取 ui_atlas 段即可,不必每次调本工具。"
        ),
        input_schema={"type": "object", "properties": {}, "required": []},
        executor=_t_ui_describe_page,
        scope="user",
        origins=_UI_ORIGINS,
        destructive=False,
        intent_keywords=("ui", "page", "describe", "页面", "atlas"),
    ))

    reg.register(ToolSpec(
        name="ui_set_field",
        description=(
            "在当前页面的某 form 里填一个字段 (代用户在 input/select/textarea 里输入)。"
            "form_id 和 field_key 必须从 ui_atlas.forms[*].id / .fields[*].key 里取, "
            "不要瞎编。优先用 label 文本作为 field_key (e.g. '存档名称')。"
            "value 是字符串/数字/布尔。select 字段可以传 option label 或 value。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "form_id":   {"type": "string", "description": "form 的 id, 从 ui_atlas 拿"},
                "field_key": {"type": "string", "description": "字段的 key (一般是 label 文本)"},
                "value":     {"description": "要填的值 (string/number/bool)"},
            },
            "required": ["form_id", "field_key", "value"],
        },
        executor=_t_ui_set_field,
        scope="user",
        origins=_UI_ORIGINS,
        destructive=False,
        intent_keywords=("fill", "set", "input", "填", "填写", "输入"),
        input_examples=(
            {"form_id": "newgame", "field_key": "存档名称", "value": "雾港调查"},
            {"form_id": "newgame", "field_key": "剧本", "value": "我蕾穆丽娜不爱你"},
            {"form_id": "card-edit", "field_key": "姓名", "value": "晓星"},
        ),
    ))

    reg.register(ToolSpec(
        name="ui_click",
        description=(
            "点击当前页面的某个按钮 (代用户鼠标点击)。"
            "form_id='global' 用于 page-level 按钮 (如 '新游戏'、'刷新');"
            "其他 form_id 用于 modal 内按钮 (如 '创建并进入'、'取消')。"
            "action_label 必须从 ui_atlas.forms[*].top_actions[*].label 取。"
            "destructive=true 因为可能触发提交/创建/删除 等不可逆动作 — "
            "在 default 权限模式下会先 yield confirmation_required 让用户确认。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "form_id":      {"type": "string"},
                "action_label": {"type": "string"},
            },
            "required": ["form_id", "action_label"],
        },
        executor=_t_ui_click,
        scope="user",
        origins=_UI_CLICK_ORIGINS,  # destructive=True 不允许 llm_chat (安全不变量)
        destructive=True,  # 点提交按钮可能产生不可逆动作 (创建存档/删存档等)
        intent_keywords=("click", "submit", "点", "提交", "创建", "确定"),
        input_examples=(
            {"form_id": "newgame", "action_label": "创建并进入"},
            {"form_id": "global", "action_label": "新游戏"},
            {"form_id": "card-edit", "action_label": "保存"},
        ),
    ))
