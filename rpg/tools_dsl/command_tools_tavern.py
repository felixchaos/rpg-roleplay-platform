"""command_tools_tavern.py — 酒馆 v2: agent 驱动角色扮演的 dispatcher 工具。

让 GM(角色扮演引擎)在对话中途用工具搭/换环境:
  · set_tavern_character    —— 换/新建 AI 角色卡 → 写 state.data['tavern'].character
  · edit_tavern_character   —— 改角色单字段
  · set_tavern_persona      —— 改用户 persona → 写 state.data['player']
  · tavern_list_scripts     —— 列用户拥有/订阅的剧本(只读)
  · tavern_bind_script      —— 把剧本绑到本对话 → 写 state.data['tavern'].bound_script_id

铁律(单写者):写状态的工具一律 **mutate in-memory state.data[...]**,由 chat
管线在 per-save advisory 锁内持久化。**绝不**在工具里裸 UPDATE game_saves.state_snapshot。
镜像的写法见 tools_dsl/command_tools.py 的 set_player_name(:484) / set_world_attribute
(:457) 与 tools_dsl/command_tools_worldbook.py 的 _t_worldbook_add(:54)。

授权:tavern_bind_script 标 destructive=True,复用现有权限系统 —— 当
permissions.mode != full_access 时由 dispatcher 拦 llm_chat origin,经
routes/permissions.py 的 pending_writes 审批,**不另造同意 UX**。
其余工具 origin 含 llm_chat,scope='save'(list-scripts 是 user 级只读)。
"""
from __future__ import annotations

import json
from typing import Any

from tools_dsl.command_dispatcher import ToolSpec, get_registry

# ────────────────────────────────────────────────────────────
# Origin 集合
# ────────────────────────────────────────────────────────────

# 非破坏性写:允许 agent(llm_chat)与 GM JSON op / UI / console
_WRITE_ORIGINS = frozenset({
    "ui_button", "api_direct", "llm_set", "llm_chat", "llm_chat_json_op", "console_assistant",
})

# 只读列表:全部 origin 均可读
_READ_ORIGINS = frozenset({
    "ui_button", "api_direct", "llm_set", "llm_chat", "llm_chat_json_op", "console_assistant",
})

# 绑剧本:destructive — dispatcher 会拦 llm_chat 裸调(走 pending_writes 审批),
# 但允许 llm_chat_json_op(GM 结构化协议)/ UI / console / llm_set(用户明确意图)。
_BIND_ORIGINS = frozenset({
    "ui_button", "api_direct", "llm_set", "llm_chat_json_op", "console_assistant",
})

# 角色卡 snapshot 写进 state 的字段(与 STATE CONTRACT / create_tavern_save 对齐)
_CHARACTER_FIELDS = (
    "name", "identity", "background", "appearance",
    "personality", "speech_style", "current_status", "sample_dialogue",
)
_EDITABLE_CHARACTER_FIELDS = frozenset(_CHARACTER_FIELDS)


# ────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────


def _resolve_user_id(state: Any, args: dict) -> int | None:
    """save 级工具的 executor 只拿到 (state, args),不含 user_id。
    从 save_id 反查 game_saves.user_id(只读查询,合规)。

    安全(关键):args["save_id"] 由 dispatcher 在 save 级分支**无条件覆盖**为已鉴权会话
    绑定的 env.save_id(command_dispatcher._execute),故此处反查到的 user_id 恒为当前
    已鉴权用户 —— LLM 即便在 tool args 里塞入异档 save_id 也会被覆盖,无法借此解析到他人
    user_id 去读写他人卡片。**切勿**改成信任调用方传入的 save_id 而绕开 dispatcher。"""
    save_id = args.get("save_id") or getattr(state, "_save_id", None) \
        or (getattr(state, "data", {}) or {}).get("_active_save_id") \
        or (getattr(state, "data", {}) or {}).get("save_id")
    if not save_id:
        return None
    try:
        save_id = int(save_id)
    except (TypeError, ValueError):
        return None
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select user_id from game_saves where id = %s",
                (save_id,),
            ).fetchone()
        if row:
            return int(row["user_id"])
    except Exception:
        return None
    return None


def _card_to_character_snapshot(card: dict) -> dict:
    """把 user_cards.get_user_card 返回的卡 DTO 投影成 state.data['tavern'].character。"""
    return {
        "name": card.get("name") or "角色",
        "identity": card.get("identity") or "",
        "background": card.get("background") or "",
        "appearance": card.get("appearance") or "",
        "personality": card.get("personality") or "",
        "speech_style": card.get("speech_style") or "",
        "current_status": card.get("current_status") or "",
        "sample_dialogue": card.get("sample_dialogue") or [],
    }


# ────────────────────────────────────────────────────────────
# Executors (save 级:签名 (state, args),mutate state.data 内存,不裸写 DB)
# ────────────────────────────────────────────────────────────


def _t_set_tavern_character(state: Any, args: dict) -> str:
    """换/新建本对话的 AI 角色卡。

    分支:
      · 传 character_card_id → user_cards.get_user_card 载入既有卡并绑定。
      · 传新卡字段(name 等)→ user_cards.upsert_user_card 建 pc 卡,再绑其返回 id。
    两种分支都 **只 mutate state.data['tavern']**(character / character_card_id /
    system_prompt / scenario 等),由管线在锁内落库。
    """
    tavern = state.data.setdefault("tavern", {})

    card_id = args.get("character_card_id")
    if card_id is not None:
        try:
            card_id = int(card_id)
        except (TypeError, ValueError):
            return "失败: character_card_id 必须是整数"
        user_id = _resolve_user_id(state, args)
        if user_id is None:
            return "失败: 无法解析当前用户(save_id 缺失)"
        try:
            from platform_app import user_cards as _ucards
            card = _ucards.get_user_card(user_id, card_id)
        except Exception as exc:
            return f"失败: {type(exc).__name__}: {exc}"
        if not card:
            return f"失败: 找不到角色卡 #{card_id}(需 card_type='pc' 且属于当前用户)"
        meta = card.get("metadata") or {}
        tavern["character"] = _card_to_character_snapshot(card)
        tavern["character_card_id"] = card_id
        tavern["system_prompt"] = str(meta.get("system_prompt") or "")
        tavern["post_history_instructions"] = str(meta.get("post_history_instructions") or "")
        tavern["scenario"] = str(meta.get("scenario") or "")
        tavern["alternate_greetings"] = meta.get("alternate_greetings") or []
        # 开局发言(first_mes):确定性贴出用,绝不由 LLM 现编。无则留空。
        tavern["first_mes"] = str(meta.get("first_mes") or "")
        return f"已切换扮演角色 → {tavern['character'].get('name') or '角色'}(卡 #{card_id})"

    # —— 新卡字段分支 ——
    name = (args.get("name") or "").strip()
    if not name:
        return "失败: 需提供 character_card_id,或新建卡的字段(至少 name)"
    user_id = _resolve_user_id(state, args)
    if user_id is None:
        return "失败: 无法解析当前用户(save_id 缺失)"
    payload = {
        "name": name,
        "identity": (args.get("identity") or "").strip(),
        "personality": (args.get("personality") or "").strip(),
        "appearance": (args.get("appearance") or "").strip(),
        "speech_style": (args.get("speech_style") or "").strip(),
        "background": (args.get("background") or "").strip(),
    }
    try:
        from platform_app import user_cards as _ucards
        new_card = _ucards.upsert_user_card(user_id, payload)
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"
    new_id = int(new_card["id"]) if new_card.get("id") is not None else None
    tavern["character"] = _card_to_character_snapshot(new_card)
    if new_id is not None:
        tavern["character_card_id"] = new_id
    # 新卡无内嵌 system_prompt/scenario,清空旧角色残留(换角色语义)
    tavern["system_prompt"] = ""
    tavern["post_history_instructions"] = ""
    tavern["scenario"] = ""
    tavern["alternate_greetings"] = []
    tavern["first_mes"] = ""  # 新建角色无开局发言 → 留空
    return f"已新建并扮演角色 → {name}(卡 #{new_id})"


def _t_edit_tavern_character(state: Any, args: dict) -> str:
    """改本对话角色的单个字段,并同步**写回关联的角色卡**(用户语义:修改角色卡)。

    写回用 merge:get_user_card 取全卡 → 只改该字段 → upsert。绝不覆盖 metadata
    (first_mes / scenario / character_book 等)与其它字段。无关联卡(character_card_id
    缺失)时仅改会话内快照。
    """
    field = (args.get("field") or "").strip()
    if field not in _EDITABLE_CHARACTER_FIELDS:
        return f"失败: field 非法 {field!r}(允许: {sorted(_EDITABLE_CHARACTER_FIELDS)})"
    if "value" not in args:
        return "失败: value 缺失"
    value = args.get("value")
    if field == "sample_dialogue":
        if isinstance(value, str):
            value = [value] if value.strip() else []
        elif not isinstance(value, list):
            value = []
    else:
        value = str(value or "").strip()
    tavern = state.data.setdefault("tavern", {})
    character = tavern.setdefault("character", {})
    character[field] = value
    # 同步写回关联角色卡(merge,保 metadata/其它字段)
    persisted = ""
    card_id = tavern.get("character_card_id")
    if card_id:
        uid = _resolve_user_id(state, args)
        if uid is not None:
            try:
                from platform_app import user_cards as _ucards
                full = _ucards.get_user_card(uid, int(card_id))
                if full:
                    full = dict(full)
                    full[field] = value
                    full["id"] = int(card_id)
                    _ucards.upsert_user_card(uid, full)
                    persisted = f"(已写回角色卡 #{int(card_id)})"
            except Exception as exc:
                persisted = f"(角色卡写回失败: {type(exc).__name__}: {exc})"
    return f"角色.{field} 已更新{persisted}"


def _t_set_tavern_persona(state: Any, args: dict) -> str:
    """改用户 persona → 写 state.data['player'](可选 upsert 一张 persona 卡)。"""
    player = state.data.setdefault("player", {})
    touched: list[str] = []
    for fld in ("name", "role", "background", "appearance"):
        if fld in args and args.get(fld) is not None:
            player[fld] = str(args.get(fld) or "").strip()
            touched.append(fld)
    if not touched:
        return "失败: 至少提供 name/role/background/appearance 之一"

    # 可选:持久化为一张 persona 卡,并把 id 记到 tavern.persona_card_id(便于下次复用)
    if bool(args.get("save_card")):
        user_id = _resolve_user_id(state, args)
        if user_id is not None and (player.get("name") or "").strip():
            try:
                from platform_app import user_cards as _ucards
                row = _ucards.upsert_persona(user_id, {
                    "name": player.get("name"),
                    "role": player.get("role") or "",
                    "background": player.get("background") or "",
                    "appearance": player.get("appearance") or "",
                })
                pid = int(row["id"]) if row.get("id") is not None else None
                if pid is not None:
                    tavern = state.data.setdefault("tavern", {})
                    tavern["persona_card_id"] = pid
            except Exception:
                pass  # persona 卡持久化失败不影响内存 player 写入
    return f"persona 已更新(字段: {', '.join(touched)})"


def _t_tavern_list_scripts(user_id: int, args: dict) -> str:
    """列当前用户拥有 + 订阅的剧本(只读)。返回 [{id, title}] 的 JSON。"""
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                """
                select s.id, s.title from scripts s
                where s.owner_id = %s
                   or s.id in (select script_id from user_script_subscriptions where user_id = %s)
                order by s.updated_at desc nulls last, s.id desc
                limit 200
                """,
                (user_id, user_id),
            ).fetchall() or []
        items = [{"id": int(r["id"]), "title": (r.get("title") or "(未命名剧本)")} for r in rows]
        return json.dumps({"scripts": items, "total": len(items)}, ensure_ascii=False)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_tavern_bind_script(state: Any, args: dict) -> str:
    """把一个剧本绑到本对话 → 写 state.data['tavern'].bound_script_id(内存)。

    校验当前用户拥有/订阅该剧本(镜像 workspace.create_save 的 owner/subscriber 检查)。
    destructive=True → dispatcher 在 mode != full_access 时拦 llm_chat,经 pending_writes 审批。
    """
    script_id = args.get("script_id")
    if script_id is None:
        return "失败: script_id 必填"
    try:
        script_id = int(script_id)
    except (TypeError, ValueError):
        return "失败: script_id 必须是整数"

    user_id = _resolve_user_id(state, args)
    if user_id is None:
        return "失败: 无法解析当前用户(save_id 缺失)"

    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                """
                select s.id, s.title from scripts s
                where s.id = %s and (
                  s.owner_id = %s
                  or s.id in (select script_id from user_script_subscriptions where user_id = %s)
                )
                """,
                (script_id, user_id, user_id),
            ).fetchone()
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"
    if not row:
        return f"失败 (权限): 剧本 #{script_id} 不属于当前用户或未订阅"

    tavern = state.data.setdefault("tavern", {})
    tavern["bound_script_id"] = script_id
    return f"已绑定剧本 #{script_id}（{row.get('title') or '剧本'}）— 现在可在对话中翻阅其设定/原著。"


# ────────────────────────────────────────────────────────────
# 注册
# ────────────────────────────────────────────────────────────


def register_tavern_tools() -> None:
    """注册酒馆 v2 工具到全局 registry。幂等(已注册则跳过)。"""
    registry = get_registry()

    if not registry.has("set_tavern_character"):
        registry.register(ToolSpec(
            name="set_tavern_character",
            description=(
                "为本酒馆对话设置/切换你要扮演的 AI 角色。\n"
                "用法之一:玩家说『你来扮演 X』时调用此工具搭好角色再开始扮演。\n"
                "两种方式二选一:\n"
                "  1) 已有角色卡 → 传 character_card_id;\n"
                "  2) 临时新建角色 → 传 name(必填)+ identity/personality/appearance/"
                "speech_style/background(可选),会创建一张新角色卡并绑定。\n"
                "成功后本对话的扮演对象立即切换为该角色。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "character_card_id": {"type": "integer", "description": "已有角色卡 id(与新建字段二选一)"},
                    "name": {"type": "string", "description": "新建角色名(新建分支必填)"},
                    "identity": {"type": "string", "description": "身份/职业(新建,可选)"},
                    "personality": {"type": "string", "description": "性格(新建,可选)"},
                    "appearance": {"type": "string", "description": "外貌(新建,可选)"},
                    "speech_style": {"type": "string", "description": "说话风格(新建,可选)"},
                    "background": {"type": "string", "description": "背景故事(新建,可选)"},
                },
                "required": [],
            },
            executor=_t_set_tavern_character,
            scope="save",
            origins=_WRITE_ORIGINS,
            destructive=False,
            input_examples=(
                {"character_card_id": 42},
                {"name": "薇拉", "identity": "流浪剑客", "personality": "冷峻寡言但护短"},
            ),
        ))

    if not registry.has("edit_tavern_character"):
        registry.register(ToolSpec(
            name="edit_tavern_character",
            description=(
                "修改当前扮演角色的单个字段(微调人设,不换角色)。\n"
                "field ∈ {name, identity, background, appearance, personality, "
                "speech_style, current_status, sample_dialogue}。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "field": {
                        "type": "string",
                        "enum": list(_CHARACTER_FIELDS),
                        "description": "要修改的角色字段",
                    },
                    "value": {"description": "新值(sample_dialogue 可传字符串数组)"},
                },
                "required": ["field", "value"],
            },
            executor=_t_edit_tavern_character,
            scope="save",
            origins=_WRITE_ORIGINS,
            destructive=False,
        ))

    if not registry.has("set_tavern_persona"):
        registry.register(ToolSpec(
            name="set_tavern_persona",
            description=(
                "设置/修改玩家自己的 persona(玩家在对话中扮演的人物)。\n"
                "可传 name/role/background/appearance 任意子集,会写入当前对话的玩家身份。\n"
                "可选 save_card=true 同时把它存为一张可复用的 persona 卡。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "玩家 persona 名"},
                    "role": {"type": "string", "description": "玩家身份/角色"},
                    "background": {"type": "string", "description": "玩家背景"},
                    "appearance": {"type": "string", "description": "玩家外貌"},
                    "save_card": {"type": "boolean", "description": "是否同时存为可复用 persona 卡", "default": False},
                },
                "required": [],
            },
            executor=_t_set_tavern_persona,
            scope="save",
            origins=_WRITE_ORIGINS,
            destructive=False,
        ))

    if not registry.has("tavern_list_scripts"):
        registry.register(ToolSpec(
            name="tavern_list_scripts",
            description=(
                "列出当前用户拥有或订阅的剧本(原著/故事库),返回 [{id, title}]。\n"
                "当玩家希望和某个已知故事/原著里的角色互动、或要参考某剧本设定时,先用它查可用剧本,\n"
                "再用 tavern_bind_script 绑定。"
            ),
            input_schema={"type": "object", "properties": {}, "required": []},
            executor=_t_tavern_list_scripts,
            scope="user",
            origins=_READ_ORIGINS,
            destructive=False,
        ))

    if not registry.has("tavern_bind_script"):
        registry.register(ToolSpec(
            name="tavern_bind_script",
            description=(
                "把一个剧本(原著/故事)绑定到本酒馆对话。绑定后你可以翻阅该剧本的设定、"
                "人物、世界书与原著正文,让角色扮演贴合该故事。\n"
                "需玩家拥有或已订阅该剧本。此操作需要玩家授权(权限系统会在必要时弹审批)。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "script_id": {"type": "integer", "description": "要绑定的剧本 id(来自 tavern_list_scripts)"},
                },
                "required": ["script_id"],
            },
            executor=_t_tavern_bind_script,
            scope="save",
            origins=_BIND_ORIGINS,
            destructive=True,
        ))


__all__ = ["register_tavern_tools"]
