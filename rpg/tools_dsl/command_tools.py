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
        "name": "set_world_field",
        "description": (
            '【推荐入口·合并版】设置 world.* 的任一字段。key 决定写法:\n'
            '· "time": 走 update_time 触发 timeline 推断 (等价旧 set_world_time)\n'
            '· "location": 走 update_location 触发场景刷新 (等价旧 set_player_location 但写 world.location)\n'
            '· 其他任意 key: 写 world[key] (等价旧 set_world_attribute,但允许包括 time)\n'
            '取代 set_world_time / set_player_location / set_world_attribute 3 个分散工具。'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "world 下要写的字段名"},
                "value": {"type": "string", "description": "新值(字符串)"},
            },
            "required": ["key", "value"],
        },
    },
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
        "description": (
            '【事件类 5 选 1 决策树】写一件已发生事件,看下面用对工具:\n'
            '· set_world_known_event(本工具): 一句话快记 world.known_events 流水账,**不进 KB / 不进历史档 / 不影响检索**。适合临时小事件如"巡逻兵换班"。\n'
            '· vs record_history_anchor: 玩家做了 importance≥60 的事(改 NPC 关系/势力立场/改写原著锚点)用它,会写**存档独立历史时间线**,retrieve_context 下次注入【过去时态】给 GM。\n'
            '· vs kb_record_event: 进 KB 世界树(canon_entities/graph_edges 永久层),被 search_canon/graph_neighbors 检索到。**本周目 NPC 之间关系/势力变化**用这个。\n'
            '· vs add_memory_fact: 个人/角色级"我知道 X"用 memory.facts,例如"我记得把钥匙藏在炉子下",**不是世界级公开事件**。\n'
            '· vs mark_anchor_satisfied: 原著剧本里有这个锚点已触发,用 mark_anchor_satisfied 配 drift_score,**不要重复**写到 known_events。\n'
            '\n要旨:本工具是最弱的"流水账级",优先选其他 4 个。'
        ),
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
        "description": (
            '设置玩家与某 NPC 的关系状态。用户写"NPC关系=信任"/"X对我警惕"等用这个工具。\n'
            "示例: set_relationship(character=\"迷迭香\", status=\"亲近\")"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "character": {"type": "string", "description": "NPC 名字(字段名是 character,不是 npc_name)"},
                "status": {"type": "string",
                           "description": "关系状态描述(字段名是 status,不是 status_label),如 '信任/警惕/敌意/亲近/紧张/疏离'"},
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
        "name": "advance_story_progress",
        "description": (
            "把玩家进度推进到指定原著章号(progress_chapter,单调只增、绝不回退)。"
            "用于玩家【已经玩过】某段剧情、但系统进度没跟上 —— 典型:自插入/发散流(如无限流副本)"
            "剧情不命中原著锚点,进度卡在早章。由玩家显式追平。"
            "影响:防剧透天花板下限、检索窗口、软引导「下一拍」的起点章。"
            "玩家说「推进到第X章 / 我已经过了第X章了 / 进度跳到第X章 / 已经玩到十几章了还卡在七八章」时调。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "to_chapter": {
                    "type": "integer",
                    "description": "目标原著章号(≥1)。低于当前进度会被忽略(单调只增不减)。",
                },
            },
            "required": ["to_chapter"],
        },
    },
    {
        "name": "add_memory",
        "description": (
            '【推荐入口·合并版】往 memory 任意一个桶追加一条。bucket enum 决定桶:\n'
            '· "facts": 本局已知事实(玩家"我记下 X")\n'
            '· "resources": 物品/资源(玩家"我有 X"/"获得 X")\n'
            '· "abilities": 能力/技能\n'
            '· "notes": 低优先级笔记\n'
            '· "pinned": 高优先级固定(也可在 facts/resources 调用时加 pin=true)\n'
            '取代 add_memory_fact/resource/ability/note/pin_memory 5 个分散工具,'
            '降低 GM 决策面;旧工具仍保留向后兼容,但 GM 应优先用本工具。'
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "bucket": {"type": "string", "enum": ["facts", "resources", "abilities", "notes", "pinned"]},
                "text": {"type": "string"},
                "pin": {"type": "boolean", "description": "true=同时也写入 pinned 桶(高优先级冗余)"},
            },
            "required": ["bucket", "text"],
        },
    },
    {
        "name": "add_memory_fact",
        "description": '[DEPRECATED] 等价于 add_memory(bucket="facts", text=X)。新代码用 add_memory。',
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add_memory_resource",
        "description": '[DEPRECATED] 等价于 add_memory(bucket="resources", text=X)。新代码用 add_memory。',
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add_memory_ability",
        "description": '[DEPRECATED] 等价于 add_memory(bucket="abilities", text=X)。新代码用 add_memory。',
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "pin_memory",
        "description": '[DEPRECATED] 等价于 add_memory(bucket="pinned", text=X)。新代码用 add_memory。',
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "add_memory_note",
        "description": '[DEPRECATED] 等价于 add_memory(bucket="notes", text=X)。新代码用 add_memory。',
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
        # iter#6 合并版入口 — dispatch 到对应旧 executor 路径
        if name == "set_world_field":
            key = (args.get("key") or "").strip()
            value = (args.get("value") or "").strip()
            if not key or not value:
                return "set_world_field 失败: key 或 value 为空"
            if key == "time":
                return execute_tool(state, "set_world_time", {"target": value})
            if key == "location":
                return execute_tool(state, "set_player_location", {"location": value})
            return execute_tool(state, "set_world_attribute", {"key": key, "value": value})
        if name == "add_memory":
            bucket = (args.get("bucket") or "").strip()
            text = (args.get("text") or "").strip()
            pin = bool(args.get("pin"))
            if not bucket or not text:
                return "add_memory 失败: bucket 或 text 为空"
            valid_buckets = {"facts", "resources", "abilities", "notes", "pinned"}
            if bucket not in valid_buckets:
                return f"add_memory 失败: bucket 非法 ({bucket} 不在 {valid_buckets})"
            # dispatch 到对应旧 executor
            old_map = {
                "facts": "add_memory_fact",
                "resources": "add_memory_resource",
                "abilities": "add_memory_ability",
                "notes": "add_memory_note",
                "pinned": "pin_memory",
            }
            main = execute_tool(state, old_map[bucket], {"text": text})
            if pin and bucket != "pinned":
                pin_r = execute_tool(state, "pin_memory", {"text": text})
                return f"{main} + 同时固定: {pin_r}"
            return main
        if name == "set_world_time":
            target = (args.get("target") or "").strip()
            if not target:
                return "set_world_time 失败: target 为空"
            old = state.data.get("world", {}).get("time", "")
            state.update_time(target, source="user_set")
            # 时间连续性护栏 v0:天数倒退检测(检测→surface,不拦截;解析不出天数即休眠)。
            try:
                from state.time_ops import detect_day_regression
                _regress = detect_day_regression(old, target)
            except Exception:
                _regress = None
            if _regress:
                try:
                    from datetime import datetime as _dt
                    _audit = state.data.setdefault("permissions", {}).setdefault("audit_log", [])
                    _audit.append({
                        "ts": _dt.now().isoformat(timespec="seconds"),
                        "kind": "time_day_regression",
                        "source": "time_continuity_guard",
                        "turn": int(state.data.get("turn") or 0),
                        "from": old, "to": target,
                        "hint": _regress,
                    })
                    state.data["permissions"]["audit_log"] = _audit[-200:]
                except Exception:
                    pass
                return f"时间线 → {target} (原: {old}) ⚠ {_regress}"
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
                # 硬上限:known_events 原本无 cap,GM 每轮记流水账长局后会无界堆积撑大
                # state_snapshot(持久化进 DB)。注入只看最近 15 条,故保留最近 100 条足矣。
                if len(events) > 100:
                    del events[:-100]
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
            return f"已撤销时间跳跃: {reason}"
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
        if name == "advance_story_progress":
            raw = args.get("to_chapter")
            try:
                to_ch = int(raw)
            except (TypeError, ValueError):
                return "advance_story_progress 失败: to_chapter 必须是整数章号"
            if to_ch < 1:
                return "advance_story_progress 失败: to_chapter 必须 ≥ 1"
            # save_id 由 dispatcher 无条件注入 args(服务端绑定、防跨档),不信任 state。
            sid = args.get("save_id")
            if not sid:
                return "advance_story_progress 失败: 无 active save(存档未就绪)"
            try:
                from platform_app.db import connect
                from gm_serving.settings import advance_progress, read_settings
                with connect() as db:
                    before = (read_settings(db, int(sid)) or {}).get("progress_chapter")
                    advance_progress(db, int(sid), to_ch)  # max-only,单调只增
                    after = (read_settings(db, int(sid)) or {}).get("progress_chapter")
            except Exception as exc:
                return f"advance_story_progress 失败: {type(exc).__name__}: {exc}"
            if before is not None and after is not None and int(after) == int(before):
                return f"进度未变:已在第 {after} 章(目标第 {to_ch} 章 ≤ 当前,单调不回退)"
            return f"进度推进:第 {before} 章 → 第 {after} 章"
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
