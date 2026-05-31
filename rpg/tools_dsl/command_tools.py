"""
command_tools.py — task 86: LLM 命令工具表。

设计理念 (用户反馈):
> 规则判断永远会有 bug。用 LLM 接管命令,用大模型理解用户自然语言,
> 然后对照实际支持的命令表用"工具调用"完成指令。
> 不要再让规则判断束缚大模型的手脚。

每个工具:
  · 名字 + JSON Schema 描述参数
  · 一个执行函数,直接对接 GameState 的 public API
  · 不暴露权限/历史/元数据写入(这些没有对应工具,LLM 不能调出来)

工具列表 (12 个,覆盖 /set 几乎所有合理用法):

  时间/位置/世界:
    set_world_time          世界时间 = X(走 update_time(source='user_set'))
    set_player_location     玩家当前位置 = X
    set_world_known_event   往 world.known_events 追加一条

  玩家档案:
    set_player_name         玩家姓名
    set_player_role         玩家身份/职业
    set_player_background   玩家背景

  关系:
    set_relationship        某 NPC 的关系状态

  记忆:
    set_main_quest          主线任务
    set_current_objective   当前目标
    add_memory_fact         事实
    add_memory_resource     资源
    add_memory_ability      能力
    pin_memory              固定记忆(高优先级)
    add_memory_note         笔记
    set_memory_mode         记忆模式 (concise/normal/deep)

  推测/世界线:
    add_hypothesis          推测/计划/草稿
    set_user_variable       玩家世界线硬约束变量

如果工具表里没有用户想做的事(比如改 permissions),LLM 无法找到对应工具
→ command_agent 应返回 "没有对应工具" 让 chat handler 提示用户。
这是设计上的安全特性:没有工具就不能写。
"""
from __future__ import annotations

from typing import Any

# ────────────────────────────────────────────────────────────
# Tool schema (Anthropic tool_use input_schema 兼容)
# ────────────────────────────────────────────────────────────


COMMAND_TOOLS: list[dict[str, Any]] = [
    {
        "name": "set_world_time",
        "description": (
            "把世界当前时间/时间线锚点设置为新值。用户写"
            '"设置时间为火星·扬陆城内"/"时间线=月球时期"/"切换时间到柏林暗流"等都用这个工具。'
            "**重要**:这是用户硬覆盖,会触发 narrative_guard 检测,GM 下一轮不能写穿越/醒来等过渡叙事。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "新的时间/时间线标签,任意用户描述,如 '火星·扬陆城内'、'剧情月球时期'、'柏林暗流篇'",
                }
            },
            "required": ["target"],
        },
    },
    {
        "name": "set_player_location",
        "description": '设置玩家当前位置。用户写"位置改为X"/"在X"/"当前位置=X"等都用这个工具。',
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "新的当前位置标签"},
            },
            "required": ["location"],
        },
    },
    {
        "name": "set_world_known_event",
        "description": '往 world.known_events 追加一条已发生的世界事件。用户写"已知事件:X"/"刚刚发生X"等用这个工具。',
        "input_schema": {
            "type": "object",
            "properties": {
                "event": {"type": "string", "description": "事件描述"},
            },
            "required": ["event"],
        },
    },
    {
        "name": "set_world_attribute",
        "description": (
            '设置 world.<key> 的标量属性,如 weather/atmosphere/season/region 等。'
            '不能用来改 time/timeline (那有专门工具 set_world_time)。'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string",
                        "description": "world 下的属性名,如 weather/atmosphere"},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "confirm_time_jump",
        "description": (
            '确认一个待确认的时间跳跃。当玩家用自然语言提出跳跃后,'
            'GM 询问玩家"是否跳到 X",玩家回"确认"时调此工具。target 可选,'
            '默认用 pending_jump.to。'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "可选,跳到的时间标签"},
            },
            "required": [],
        },
    },
    {
        "name": "reject_time_jump",
        "description": (
            '拒绝一个待确认的时间跳跃,清掉 pending_jump,保持原时间线。'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "拒绝理由,可选"},
            },
            "required": [],
        },
    },
    {
        "name": "set_player_name",
        "description": "设置玩家角色姓名。",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "set_player_role",
        "description": '设置玩家身份/职业/定位(如"穿越者·魔女"、"流亡贵族")。',
        "input_schema": {
            "type": "object",
            "properties": {"role": {"type": "string"}},
            "required": ["role"],
        },
    },
    {
        "name": "set_player_background",
        "description": "设置玩家背景故事(自由文本)。",
        "input_schema": {
            "type": "object",
            "properties": {"background": {"type": "string"}},
            "required": ["background"],
        },
    },
    {
        "name": "set_relationship",
        "description": '设置玩家与某 NPC 的关系状态。用户写"NPC关系=信任"/"X对我警惕"等用这个工具。',
        "input_schema": {
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "NPC 名字"},
                "status": {"type": "string",
                           "description": "关系状态描述,如 '信任/警惕/敌意/亲近/紧张/疏离'"},
            },
            "required": ["character", "status"],
        },
    },
    {
        "name": "delete_relationship",
        "description": '删除玩家与某 NPC 的关系条目(整条移除,不是清空)。侧栏点 × 删关系卡时调。',
        "input_schema": {
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "要删除的 NPC 名字"},
            },
            "required": ["character"],
        },
    },
    {
        "name": "set_timeline_phase",
        "description": (
            '设置世界线当前 phase 标签(world.timeline.current_phase)。'
            '会被记入 user_locked_fields,后续 update_time 不再用 _phase_for_time 推断覆盖。'
            '用户在侧栏直接选/输 phase 时调。'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "phase": {"type": "string", "description": "phase 标签,如 '柏林暗流篇/月球时期'"},
            },
            "required": ["phase"],
        },
    },
    {
        "name": "set_main_quest",
        "description": "设置主线任务文本。",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "set_current_objective",
        "description": "设置当前回合目标(短期).",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add_memory_fact",
        "description": '追加一条本局事实(memory.facts).用户写"事实:X"/"我记下X"等用这个工具。',
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add_memory_resource",
        "description": '追加一项资源(memory.resources).用户写"我有X"/"获得X"等用这个工具。',
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add_memory_ability",
        "description": '追加一项能力(memory.abilities).',
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "pin_memory",
        "description": '固定记忆(高优先级,memory.pinned).用户标记重要事项时用。',
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add_memory_note",
        "description": '追加一条笔记(memory.notes,低优先级).',
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "set_memory_mode",
        "description": '切换记忆模式: concise(精简)/normal(普通)/deep(深度).',
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["concise", "normal", "deep"]},
            },
            "required": ["mode"],
        },
    },
    {
        "name": "add_hypothesis",
        "description": '登记一条推测/计划/草稿。和 add_memory_fact 不同,推测**不是**事实。',
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "characters": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "涉及到的 NPC/角色名,可选",
                },
                "time_label": {
                    "type": "string",
                    "description": "推测发生的时间标签,可选",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "set_user_variable",
        "description": (
            '设置玩家硬约束变量(worldline.user_variables).用于强制设定剧情走向,'
            '如 "trust_slaine=信任下降"。和 add_memory_fact 不同,user_variable 是显式约束,世界线推演必须先满足。'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "变量名(英文或拼音,如 trust_slaine)"},
                "value": {"type": "string"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "clarify",
        "description": (
            "当 LLM 无法明确把用户的话映射到上面任何工具,或者用户句子歧义时,"
            "用这个工具向用户提问。**只有在真的无法决定时**才用。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "向用户的问题"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选选项,2-4 项",
                },
            },
            "required": ["question"],
        },
    },
]


# ────────────────────────────────────────────────────────────
# Tool execution: 直接调 GameState public API
# ────────────────────────────────────────────────────────────


def execute_tool(state: Any, name: str, args: dict) -> str:
    """执行单个工具调用,返回人类可读的执行结果文本。

    失败 (state 没有对应方法 / 参数缺) 不抛异常,返回错误描述,
    让 command_agent 写到 audit_log 让 LLM 自纠或前端展示。
    """
    try:
        if name == "set_world_time":
            target = (args.get("target") or "").strip()
            if not target:
                return "set_world_time 失败: target 为空"
            old = state.data.get("world", {}).get("time", "")
            state.update_time(target, source="user_set")
            return f"时间线 → {target} (原: {old})"
        if name == "set_player_location":
            loc = (args.get("location") or "").strip()
            if not loc:
                return "set_player_location 失败: location 为空"
            state.update_location(loc)
            return f"位置 → {loc}"
        if name == "set_world_known_event":
            event = (args.get("event") or "").strip()
            if not event:
                return "set_world_known_event 失败: event 为空"
            events = state.data.setdefault("world", {}).setdefault("known_events", [])
            if event not in events:
                events.append(event)
                return f"已知事件 += {event}"
            return f"已知事件已存在: {event}"
        if name == "set_world_attribute":
            key = (args.get("key") or "").strip()
            value = (args.get("value") or "").strip()
            if not key or not value:
                return "set_world_attribute 失败: key 或 value 为空"
            # 禁用 time/timeline (有专门工具)
            if key in {"time", "timeline"}:
                return f"set_world_attribute 失败: 请用 set_world_time 改 {key}"
            world = state.data.setdefault("world", {})
            world[key] = value
            return f"world.{key} = {value}"
        if name == "confirm_time_jump":
            target = (args.get("target") or "").strip() or None
            timeline = (state.data.get("world") or {}).get("timeline") or {}
            pending = timeline.get("pending_jump") or {}
            actual_target = target or pending.get("to") or state.data.get("world", {}).get("time")
            if not pending:
                return "confirm_time_jump 失败: 没有待确认的 pending_jump"
            state.confirm_time_jump(actual_target)
            return f"时间跳跃确认 → {actual_target}"
        if name == "reject_time_jump":
            reason = (args.get("reason") or "玩家拒绝").strip()
            timeline = (state.data.get("world") or {}).get("timeline") or {}
            if not (timeline.get("pending_jump")):
                return "reject_time_jump 失败: 没有待确认的 pending_jump"
            state.reject_time_jump(reason)
            return f"时间跳跃已拒绝 ({reason})"
        if name == "set_player_name":
            v = (args.get("name") or "").strip()
            if not v:
                return "set_player_name 失败: name 为空"
            state.data.setdefault("player", {})["name"] = v
            return f"玩家姓名 → {v}"
        if name == "set_player_role":
            v = (args.get("role") or "").strip()
            if not v:
                return "set_player_role 失败: role 为空"
            state.data.setdefault("player", {})["role"] = v
            return f"玩家身份 → {v}"
        if name == "set_player_background":
            v = (args.get("background") or "").strip()
            if not v:
                return "set_player_background 失败: background 为空"
            state.data.setdefault("player", {})["background"] = v
            return "玩家背景已更新"
        if name == "set_relationship":
            ch = (args.get("character") or "").strip()
            st = (args.get("status") or "").strip()
            if not ch or not st:
                return "set_relationship 失败: character 或 status 为空"
            state.update_relationship(ch, st)
            return f"关系 {ch} → {st}"
        if name == "delete_relationship":
            ch = (args.get("character") or "").strip()
            if not ch:
                return "delete_relationship 失败: character 为空"
            rels = state.data.setdefault("relationships", {})
            if ch not in rels:
                return f"delete_relationship: {ch} 不在 relationships(无需删)"
            del rels[ch]
            return f"关系已删除: {ch}"
        if name == "set_timeline_phase":
            phase = (args.get("phase") or "").strip()
            if not phase:
                return "set_timeline_phase 失败: phase 为空"
            timeline = state.data.setdefault("world", {}).setdefault("timeline", {})
            old = timeline.get("current_phase", "")
            timeline["current_phase"] = phase
            # 走现成 mark_user_locked,后续 update_time / _phase_for_time 不再覆盖
            try:
                state.mark_user_locked("world.timeline.current_phase")
            except Exception:
                pass
            return f"timeline.current_phase: {old or '∅'} → {phase}"
        if name == "set_main_quest":
            v = (args.get("text") or "").strip()
            if not v:
                return "set_main_quest 失败: text 为空"
            state.data.setdefault("memory", {})["main_quest"] = v
            return f"主线 → {v}"
        if name == "set_current_objective":
            v = (args.get("text") or "").strip()
            if not v:
                return "set_current_objective 失败: text 为空"
            state.data.setdefault("memory", {})["current_objective"] = v
            return f"当前目标 → {v}"
        if name in ("add_memory_fact", "add_memory_resource", "add_memory_ability",
                     "pin_memory", "add_memory_note"):
            bucket = {
                "add_memory_fact": "facts",
                "add_memory_resource": "resources",
                "add_memory_ability": "abilities",
                "pin_memory": "pinned",
                "add_memory_note": "notes",
            }[name]
            v = (args.get("text") or "").strip()
            if not v:
                return f"{name} 失败: text 为空"
            ok = state.add_memory(bucket, v)
            return f"memory.{bucket} += {v}" if ok else f"memory.{bucket} 已含此条 (去重)"
        if name == "set_memory_mode":
            mode = (args.get("mode") or "").strip()
            if mode not in {"concise", "normal", "deep"}:
                return f"set_memory_mode 失败: mode 非法 {mode!r}"
            state.set_memory_mode(mode)
            return f"记忆模式 → {mode}"
        if name == "add_hypothesis":
            v = (args.get("text") or "").strip()
            if not v:
                return "add_hypothesis 失败: text 为空"
            hid = state.add_hypothesis(
                text=v,
                source="user:/set:tool",
                time_label=args.get("time_label"),
                characters=args.get("characters"),
            )
            return f"推测登记: {hid}"
        if name == "set_user_variable":
            k = (args.get("key") or "").strip()
            v = (args.get("value") or "").strip()
            if not k or not v:
                return "set_user_variable 失败: key 或 value 为空"
            state.set_user_variable(k, v, source="user:/set:tool")
            return f"user_variables.{k} = {v}"
        if name == "clarify":
            q = (args.get("question") or "").strip()
            opts = args.get("options") or []
            return f"clarify: {q} (选项: {opts})"
        return f"unknown tool: {name}"
    except Exception as exc:
        return f"{name} 执行异常: {type(exc).__name__}: {exc}"


# Public re-exports
__all__ = ["COMMAND_TOOLS", "execute_tool"]
