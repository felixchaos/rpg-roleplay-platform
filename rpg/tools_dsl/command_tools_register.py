"""
command_tools_register.py — task 87: 把所有 save 级命令工具注册到全局 registry。

不直接合并到 command_tools.py,因为 command_tools.py 的 schema 是 LLM-facing 的纯数据,
不应混入 dispatcher 框架的 ToolSpec 包装(关注点分离)。

注册时机: app.py 启动时 import 一次。
"""
from __future__ import annotations

from typing import Any

from core.logging import get_logger
from tools_dsl.command_dispatcher import ToolSpec, get_registry
from tools_dsl.command_tools import COMMAND_TOOLS
from tools_dsl.command_tools import execute_tool as _execute_legacy

log = get_logger(__name__)

# 这些 origin 默认允许从 LLM (llm_set / llm_chat) 和 UI 调用 (save 级游戏指令).
# task 62: 移除 console_assistant — 它是"跨 save 资源管理"助手,
# 不该调当前 save 的剧情内编辑工具 (set_player_*/set_world_time 等).
# 否则 LLM 看到 "创建角色 晓卡" 容易误判为 set_player_name 而不是 create_character_card.
_DEFAULT_SAVE_ORIGINS = frozenset({
    "llm_set", "llm_chat", "llm_chat_json_op", "ui_button", "api_direct",
})
# Destructive 工具不允许 llm_chat (LLM 自由叙事流式输出时不该调它们);
# 但 llm_chat_json_op (GM 通过结构化 JSON op 协议写状态) 是 GM 正常工作流,允许。
# 仍允许 llm_set (用户通过 /set 明确意图) 和 ui_button (UI 显式按按钮).
# task 62: 同上,移除 console_assistant.
# task 91 (Bug fix): 加入 llm_chat_json_op — 之前 GM 叙事里说"我叫晓星"输出
# {"op":"set","path":"player.name","value":"晓星"} 被 dispatcher 拦,玩家不知道
# 静默失败,叙事和状态脱节。GM JSON op 是合法的有意状态变更,应允许。
_DESTRUCTIVE_SAVE_ORIGINS = frozenset({
    "llm_set", "llm_chat_json_op", "ui_button", "api_direct",
})


def _make_save_executor(name: str):
    """把 command_tools.execute_tool(state, name, args) 包成 dispatcher 的
    executor(state, args) -> str。"""
    def _exec(state: Any, args: dict) -> str:
        return _execute_legacy(state, name, args)
    return _exec


def _register_legacy_command_tools() -> None:
    """把 command_tools.COMMAND_TOOLS 全部注册为 save 级工具。
    各工具的 destructive 与 origin 按业务语义微调。"""
    # destructive 标记: 不允许 llm_chat 直接调,但允许 llm_set (用户明确意图)
    # v28+sidebar: delete_relationship 是删除条目,标 destructive 防 llm_chat 随手清掉
    # advance_story_progress 不标 destructive(非删除),但要挡 llm_chat:进度推进是史官(三贤者)
    # 的活,GM 自由叙事流不该自行跳章;只允许玩家显式 /set(llm_set)+ UI + api_direct。
    destructive_names = {"set_player_name", "set_player_role", "set_player_background",
                         "set_player_appearance", "set_player_personality",
                         "set_player_speech_style",
                         "delete_relationship"}
    no_llm_chat_names = {"advance_story_progress"}
    registry = get_registry()
    for tool in COMMAND_TOOLS:
        name = tool["name"]
        if registry.has(name):
            continue
        is_destructive = name in destructive_names
        # destructive 或显式挡 llm_chat 的工具都用不含 llm_chat 的 origin 集。
        exclude_llm_chat = is_destructive or (name in no_llm_chat_names)
        registry.register(ToolSpec(
            name=name,
            description=tool["description"],
            input_schema=tool["input_schema"],
            executor=_make_save_executor(name),
            scope="save",
            origins=_DESTRUCTIVE_SAVE_ORIGINS if exclude_llm_chat else _DEFAULT_SAVE_ORIGINS,
            destructive=is_destructive,
        ))


# ────────────────────────────────────────────────────────────
# Phase 2 首批新工具 (8 个核心 A 类)
# ────────────────────────────────────────────────────────────


def _tool_remove_memory_item(state: Any, args: dict) -> str:
    bucket = (args.get("bucket") or "").strip()
    index = args.get("index")
    if bucket not in {"resources", "abilities", "facts", "pinned", "notes"}:
        return f"失败: bucket 非法 {bucket!r}"
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return "失败: index 必须是整数"
    items = state.data.get("memory", {}).get(bucket, []) or []
    if not (0 <= idx < len(items)):
        return f"失败: index={idx} 越界 (bucket={bucket} 长度={len(items)})"
    removed = items[idx]
    state.remove_memory(bucket, idx)
    return f"memory.{bucket}[{idx}] 已移除: {removed!r}"


def _tool_approve_pending_write(state: Any, args: dict) -> str:
    pid = (args.get("id") or "").strip()
    if not pid:
        return "失败: id 为空"
    try:
        result = state.approve_pending_write(pid)
        return result or f"pending_write {pid} 已批准"
    except AttributeError:
        return "失败: state.approve_pending_write 未实现"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _tool_reject_pending_write(state: Any, args: dict) -> str:
    pid = (args.get("id") or "").strip()
    if not pid:
        return "失败: id 为空"
    try:
        result = state.reject_pending_write(pid)
        return result or f"pending_write {pid} 已拒绝"
    except AttributeError:
        return "失败: state.reject_pending_write 未实现"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _tool_dismiss_pending_question(state: Any, args: dict) -> str:
    qid = (args.get("id") or "").strip()
    if not qid:
        return "失败: id 为空"
    permissions = state.data.setdefault("permissions", {})
    questions = permissions.setdefault("pending_questions", [])
    before = len(questions)
    permissions["pending_questions"] = [q for q in questions if q.get("id") != qid]
    after = len(permissions["pending_questions"])
    if after == before:
        return f"失败: 未找到 question id={qid}"
    return f"pending_question {qid} 已关闭"


def _tool_remove_user_variable(state: Any, args: dict) -> str:
    key = (args.get("key") or "").strip()
    if not key:
        return "失败: key 为空"
    variables = state.data.setdefault("worldline", {}).setdefault("user_variables", {})
    if key not in variables:
        return f"失败: user_variables 不含 {key}"
    state.remove_user_variable(key)
    return f"user_variables.{key} 已删除"


def _tool_save_runtime(state: Any, args: dict) -> str:
    """触发一次显式存档持久化 (write to disk / DB)."""
    try:
        path = state.save()
        return f"已存档: {path or '(server 模式,由 runtime 持久化)'}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _tool_set_known_event(state: Any, args: dict) -> str:
    """与 set_world_known_event 重名,新工具改名 add_world_event 兼顾语义。"""
    text = (args.get("text") or args.get("event") or "").strip()
    if not text:
        return "失败: text 为空"
    events = state.data.setdefault("world", {}).setdefault("known_events", [])
    if text in events:
        return f"已存在: {text}"
    events.append(text)
    return f"world.known_events += {text}"


def _tool_stop_current_chat(state: Any, args: dict) -> str:
    """工具调用层抛出 stop 标志,具体停止逻辑由 chat handler 监听 state.permissions.stop_signal。"""
    permissions = state.data.setdefault("permissions", {})
    permissions["stop_signal"] = {"requested": True, "ts": __import__("datetime").datetime.now().isoformat(timespec="seconds")}
    return "已请求停止当前 chat (由 chat handler 在下次循环检测)"


def _register_phase2_tools() -> None:
    registry = get_registry()
    new_tools: list[ToolSpec] = [
        ToolSpec(
            name="remove_memory_item",
            description=(
                "从指定 bucket 移除一条记忆项。bucket ∈ "
                "{resources, abilities, facts, pinned, notes},index 为该 bucket 内 0-based 下标。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "bucket": {"type": "string",
                               "enum": ["resources", "abilities", "facts", "pinned", "notes"]},
                    "index": {"type": "integer", "minimum": 0},
                },
                "required": ["bucket", "index"],
            },
            executor=_tool_remove_memory_item,
            scope="save",
            origins=_DESTRUCTIVE_SAVE_ORIGINS,
            destructive=True,  # 移除是破坏性,llm_chat 不允许
        ),
        ToolSpec(
            name="approve_pending_write",
            description="审批通过一个待写入的 state op。",
            input_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            executor=_tool_approve_pending_write,
            scope="save",
            # 只允许 UI 显式审批,不允许 LLM 自批
            origins=frozenset({"ui_button", "api_direct"}),
        ),
        ToolSpec(
            name="reject_pending_write",
            description="拒绝一个待写入的 state op。",
            input_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            executor=_tool_reject_pending_write,
            scope="save",
            origins=frozenset({"ui_button", "api_direct"}),
        ),
        ToolSpec(
            name="dismiss_pending_question",
            description="关闭一个 GM 待确认问题 (玩家明确放弃回答)。",
            input_schema={
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
            executor=_tool_dismiss_pending_question,
            scope="save",
            origins=_DEFAULT_SAVE_ORIGINS,
        ),
        ToolSpec(
            name="remove_user_variable",
            description="删除一个玩家世界线硬约束变量 (worldline.user_variables.{key})。",
            input_schema={
                "type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"],
            },
            executor=_tool_remove_user_variable,
            scope="save",
            origins=_DESTRUCTIVE_SAVE_ORIGINS,
            destructive=True,
        ),
        ToolSpec(
            name="save_runtime",
            description="把当前 state 显式持久化到磁盘/DB。",
            input_schema={"type": "object", "properties": {}, "required": []},
            executor=_tool_save_runtime,
            scope="save",
            origins=_DEFAULT_SAVE_ORIGINS,
        ),
        # add_world_event 已删除:跟 legacy set_world_known_event 是纯别名,executor 共用
        # _tool_set_known_event,写同一个 state.world.known_events 字段。两个都暴露给 LLM
        # 会让 GM 50% 概率随机选,同事件追加两次。set_world_known_event 保留,描述里加上
        # 5 选 1 决策树,_tool_set_known_event 仍保留供老代码 / 测试 import 复用。
        ToolSpec(
            name="stop_current_chat",
            description="请求停止当前正在执行的 chat turn (由 chat handler 在下次检查点中断)。",
            input_schema={"type": "object", "properties": {}, "required": []},
            executor=_tool_stop_current_chat,
            scope="save",
            origins=frozenset({"ui_button", "api_direct"}),  # 不允许 LLM 自停
        ),
    ]
    for spec in new_tools:
        if not registry.has(spec.name):
            registry.register(spec)


# ────────────────────────────────────────────────────────────
# N (MD 编辑器) §5: script 级「列出」读工具
# (executor 在 command_tools_script_write,读级闸 owner|subscriber)
# 照抄 command_tools_queries 的 script 读工具注册行(scope="script" + _READ_ANY_ORIGIN)。
# rule 4 同步前先用这三个定位现有 entry_id / anchor_id / logical_key。
# ────────────────────────────────────────────────────────────


def _register_script_read_tools() -> None:
    from tools_dsl.command_tools_script_write import (
        _SCRIPT_READ_ORIGINS,
        _t_extract_from_selection,
        _t_get_chapter_context,
        _t_list_anchors,
        _t_list_canon_entities,
        _t_list_worldbook_entries,
    )
    registry = get_registry()
    read_specs = [
        ("list_worldbook_entries",
         "列出剧本世界书条目精简清单(entry_id/title/keys/enabled/...)。"
         "用 upsert_worldbook_entry 更新前先用它拿 entry_id。",
         _t_list_worldbook_entries),
        ("list_anchors",
         "列出剧本时间线锚点精简清单(anchor_id/label/story_phase/章节区间)。"
         "用 update_anchor 更新前先用它拿 anchor_id。",
         _t_list_anchors),
        ("list_canon_entities",
         "列出剧本 canon 实体精简清单(logical_key/name/type/...)。"
         "用 upsert_canon_entity 更新前先用它拿 logical_key。",
         _t_list_canon_entities),
    ]
    for name, desc, exec_ in read_specs:
        if registry.has(name):
            continue
        registry.register(ToolSpec(
            name=name,
            description=desc,
            input_schema={"type": "object", "properties": {"script_id": {"type": "integer"}}},
            executor=exec_,
            scope="script",
            origins=_SCRIPT_READ_ORIGINS,
            destructive=False,
        ))
    # get_chapter_context:聚合读工具(需 chapter_index),单独注册(schema 与上面 list_* 不同)。
    if not registry.has("get_chapter_context"):
        registry.register(ToolSpec(
            name="get_chapter_context",
            description=(
                "一次性取「该章相关编辑环境」(相关世界书/人物/词条/此刻时点/前情提要,按 chapter_index 防剧透)。"
                "编辑或续写某章前先调它建立设定认知,免去逐个 list_*/get_* 多轮往返、也避免凭空写。"
            ),
            input_schema={"type": "object", "properties": {
                "script_id": {"type": "integer"},
                "chapter_index": {"type": "integer", "description": "正在编辑的章号(1-based)"},
            }},
            executor=_t_get_chapter_context,
            scope="script",
            origins=_SCRIPT_READ_ORIGINS,
            destructive=False,
        ))
    # extract_from_selection:作者优先「把提取器拆成选区工具」—— 只产提议、不写库(调提取 LLM)。
    if not registry.has("extract_from_selection"):
        registry.register(ToolSpec(
            name="extract_from_selection",
            description=(
                "对用户选中的一段正文跑结构化提取(复用提取器:反史实/反编造/中文别名归并铁律),"
                "返回提议的人物/势力/地点/概念/事件/摘要(只产提议、不写库)。作者把设定写进正文后,"
                "用它从选中段抽出知识资产,再按用户意愿用 upsert_*/create_anchor 落库(经写入权限闸)。"
            ),
            input_schema={"type": "object", "properties": {
                "script_id": {"type": "integer"},
                "text": {"type": "string", "description": "要提取信息的选中正文"},
            }, "required": ["text"]},
            executor=_t_extract_from_selection,
            scope="script",
            origins=_SCRIPT_READ_ORIGINS,
            destructive=False,
        ))


# ────────────────────────────────────────────────────────────
# Public: 一次性初始化所有工具
# ────────────────────────────────────────────────────────────


_REGISTERED = False


def ensure_registered() -> None:
    """幂等,多次调不会重注册。app.py 启动时调一次即可。"""
    global _REGISTERED
    if _REGISTERED:
        return
    _register_legacy_command_tools()
    _register_phase2_tools()
    # task 87 Phase 2.2 / 2.3 / Phase 3: 三个独立子模块的工具
    try:
        from tools_dsl.command_tools_saves import register_saves_tools
        register_saves_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] saves 工具注册失败: {exc}")
    try:
        from tools_dsl.command_tools_rules import register_rules_tools
        register_rules_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] rules 工具注册失败: {exc}")
    try:
        from tools_dsl.command_tools_queries import register_query_tools
        register_query_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] query 工具注册失败: {exc}")
    # task 87 Phase 4 + 余下补全
    try:
        from tools_dsl.command_tools_misc import register_misc_tools
        register_misc_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] misc 工具注册失败: {exc}")
    # persona / character_card 工具 (拆自 misc)
    try:
        from tools_dsl.command_tools_persona import register_persona_tools
        register_persona_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] persona 工具注册失败: {exc}")
    # script import / probe 工具 (拆自 misc)
    try:
        from tools_dsl.command_tools_imports import register_imports_tools
        register_imports_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] imports 工具注册失败: {exc}")
    # task 107C: phase management tools
    try:
        from tools_dsl.command_tools_phase import register_phase_tools
        register_phase_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] phase 工具注册失败: {exc}")
    # task 107H: worldbook overlay tools
    try:
        from tools_dsl.command_tools_worldbook import register_worldbook_tools
        register_worldbook_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] worldbook 工具注册失败: {exc}")
    # 后果账本 v1 (docs/design/consequence_ledger_v1.md): schedule_consequence
    try:
        from tools_dsl.command_tools_consequence import register_consequence_tools
        register_consequence_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] consequence 工具注册失败: {exc}")
    # task 109b: ui action tools (set_field/click via SSE to frontend)
    try:
        from tools_dsl.command_tools_ui_action import register_ui_action_tools
        register_ui_action_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] ui_action 工具注册失败: {exc}")
    # creative tools: recommend_player_identity (新建存档时推荐初始身份)
    try:
        from tools_dsl.command_tools_creative import register_creative_tools
        register_creative_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] creative 工具注册失败: {exc}")
    # task 136: 世界线收束机制 — list_pending_anchors / mark_satisfied / mark_superseded
    try:
        from tools_dsl.command_tools_anchors import register_anchor_tools
        register_anchor_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] anchors 工具注册失败: {exc}")
    # Phase D: GM 知识库查询/写工具(读 kb_canon∪live / 写 kb_* 世界树 delta)
    try:
        from tools_dsl.command_tools_kb import register_kb_tools
        register_kb_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] kb 工具注册失败: {exc}")
    # 酒馆 v2: agent 中途建/换角色 + persona + 列/绑剧本(权限门控)
    try:
        from tools_dsl.command_tools_tavern import register_tavern_tools
        register_tavern_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] tavern 工具注册失败: {exc}")
    # Phase 1: generate_image 工具（save 级生图 + 确定性门控）
    try:
        from tools_dsl.command_tools_image import register_image_tools
        register_image_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] image 工具注册失败: {exc}")
    # N (MD 编辑器) §5: script 级「直写库」写工具(章节/世界书/NPC卡/锚点/canon,严格 owner 闸)
    try:
        from tools_dsl.command_tools_script_write import register_script_write_tools
        register_script_write_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] script_write 工具注册失败: {exc}")
    # N (MD 编辑器) §5: script 级「列出」读工具(世界书/锚点/canon,读级闸 owner|subscriber)
    try:
        _register_script_read_tools()
    except Exception as exc:
        log.warning(f"[command_tools_register] script_read 工具注册失败: {exc}")
    # task 68/72 — 给已注册工具打 intent_keywords + side_effect_topics 标签,
    # 供 ui_describe 模糊匹配 + dispatcher 状态变更广播。
    try:
        from ui_manifest import apply_tags
        apply_tags()
    except Exception as exc:
        log.warning(f"[command_tools_register] ui_manifest.apply_tags 失败: {exc}")
    _REGISTERED = True


def force_reset_for_tests() -> None:
    """仅供测试: 清空全局 registry 并重新注册,用于测试场景隔离。"""
    global _REGISTERED
    get_registry().clear()
    _REGISTERED = False
    ensure_registered()


__all__ = ["ensure_registered", "force_reset_for_tests"]
