"""
command_tools_queries.py — task 87 Phase 3: B 类只读查询工具。

让 LLM 主动检索上下文,而不是被动接受静态 prompt 摘要。

scope 划分:
  global  : 无作用域要求 (list_models, list_tools)
  user    : 当前用户的资源 (list_my_personas, list_my_character_cards)
  script  : 当前剧本的资源 (get_chapter_facts, list_script_npcs)
  save    : 当前 save 状态 (query_memory, get_current_scene, get_user_variables)

所有工具不写 state,只读;允许所有 origin 调用。
"""
from __future__ import annotations

import json
from typing import Any

from tools_dsl.command_dispatcher import ToolSpec, get_registry


# task 48: console_assistant 是 user-driven agent,所有 read 工具都对它开放
_READ_ANY_ORIGIN = frozenset({
    "ui_button", "api_direct", "llm_set", "llm_chat", "mcp_call", "console_assistant",
})


# ── save 级 (读 GameState) ──────────────────────────────


def _t_get_game_state(state: Any, args: dict) -> str:
    fields = args.get("fields") or []
    if not isinstance(fields, list):
        fields = []
    d = state.data
    snapshot = {
        "turn": d.get("turn"),
        "player": d.get("player", {}),
        "world": {
            "time": (d.get("world") or {}).get("time"),
            "timeline": {
                "current_label": ((d.get("world") or {}).get("timeline") or {}).get("current_label"),
                "current_phase": ((d.get("world") or {}).get("timeline") or {}).get("current_phase"),
                "user_set_jump_turn": ((d.get("world") or {}).get("timeline") or {}).get("user_set_jump_turn"),
            },
            "known_events": (d.get("world") or {}).get("known_events", [])[-10:],
        },
        "relationships": d.get("relationships", {}),
        "memory": {
            "main_quest": (d.get("memory") or {}).get("main_quest"),
            "current_objective": (d.get("memory") or {}).get("current_objective"),
            "mode": (d.get("memory") or {}).get("mode"),
        },
    }
    if fields:
        snapshot = {k: v for k, v in snapshot.items() if k in set(fields)}
    return json.dumps(snapshot, ensure_ascii=False, indent=2)


def _t_query_memory(state: Any, args: dict) -> str:
    kind = (args.get("kind") or "").strip() or None
    characters = args.get("characters") or []
    time_label = (args.get("time_label") or "").strip() or None
    limit = int(args.get("limit") or 20)
    items = (state.data.get("memory") or {}).get("items") or []
    out = []
    for it in items[-200:]:
        if kind and it.get("kind") != kind:
            continue
        if characters:
            it_chars = it.get("characters") or []
            if not any(c in it_chars for c in characters):
                continue
        if time_label and it.get("time_label") != time_label:
            continue
        out.append({
            "id": it.get("id"),
            "kind": it.get("kind"),
            "text": it.get("text"),
            "turn": it.get("turn"),
            "time_label": it.get("time_label"),
            "characters": it.get("characters"),
        })
        if len(out) >= limit:
            break
    if not out:
        return "(无匹配)"
    return json.dumps(out, ensure_ascii=False, indent=2)


def _t_get_user_variables(state: Any, args: dict) -> str:
    variables = (state.data.get("worldline") or {}).get("user_variables") or {}
    if not variables:
        return "(无 user_variables)"
    out = {k: v.get("value") for k, v in variables.items()}
    return json.dumps(out, ensure_ascii=False, indent=2)


def _t_get_current_scene(state: Any, args: dict) -> str:
    scene = state.data.get("scene") or {}
    enc = state.data.get("encounter") or {}
    return json.dumps({
        "module_id": scene.get("module_id"),
        "location_id": scene.get("location_id"),
        "visible_clues": scene.get("visible_clues"),
        "exits": scene.get("exits"),
        "encounter_active": enc.get("active"),
        "active_entities": state.data.get("active_entities", []),
    }, ensure_ascii=False, indent=2)


def _t_get_known_events(state: Any, args: dict) -> str:
    events = (state.data.get("world") or {}).get("known_events") or []
    limit = int(args.get("limit") or 20)
    return json.dumps(events[-limit:], ensure_ascii=False, indent=2)


def _t_list_relationships(state: Any, args: dict) -> str:
    rels = state.data.get("relationships") or {}
    return json.dumps(rels, ensure_ascii=False, indent=2)


def _t_get_pending_questions(state: Any, args: dict) -> str:
    qs = (state.data.get("permissions") or {}).get("pending_questions") or []
    return json.dumps(qs, ensure_ascii=False, indent=2)


def _t_get_pending_writes(state: Any, args: dict) -> str:
    pws = (state.data.get("permissions") or {}).get("pending_writes") or []
    return json.dumps(pws, ensure_ascii=False, indent=2)


def _t_recent_audit_log(state: Any, args: dict) -> str:
    limit = int(args.get("limit") or 10)
    kind_filter = (args.get("kind") or "").strip() or None
    audit = (state.data.get("permissions") or {}).get("audit_log") or []
    if kind_filter:
        audit = [a for a in audit if a.get("kind") == kind_filter]
    return json.dumps(audit[-limit:], ensure_ascii=False, indent=2)


# ── user 级 (读 DB) ─────────────────────────────────────


def _t_list_my_personas(user_id: int, args: dict) -> str:
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                "select id, name, role, personality, is_default "
                "from user_personas where user_id = %s "
                "order by updated_at desc limit 30",
                (user_id,),
            ).fetchall() or []
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2) if rows else "(无 persona)"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_list_my_character_cards(user_id: int, args: dict) -> str:
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                "select id, name, identity, personality, enabled "
                "from user_character_cards where user_id = %s "
                "order by updated_at desc limit 50",
                (user_id,),
            ).fetchall() or []
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2) if rows else "(无 card)"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_get_my_usage(user_id: int, args: dict) -> str:
    days = int(args.get("days") or 30)
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                "select date_trunc('day', created_at) as day, "
                "sum(input_tokens) as in_t, sum(output_tokens) as out_t, count(*) as n "
                "from llm_usage_log where user_id = %s and created_at > now() - %s::interval "
                "group by day order by day desc limit 30",
                (user_id, f"{days} days"),
            ).fetchall() or []
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ── script 级 (读剧本数据) ──────────────────────────────


def _t_list_scripts(user_id: int, args: dict) -> str:
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                "select id, title, chapter_count, word_count from scripts "
                "where owner_id = %s "
                "order by updated_at desc limit 50",
                (user_id,),
            ).fetchall() or []
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2) if rows else "(无 script)"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_get_script_chapters(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = script_id or args.get("script_id")
    if not sid:
        return "失败: script_id 必填"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                "select chapter_index, title, summary from script_chapters "
                "where script_id = %s order by chapter_index limit 200",
                (int(sid),),
            ).fetchall() or []
        return json.dumps([dict(r) for r in rows[:50]], ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_list_script_npcs(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = script_id or args.get("script_id")
    if not sid:
        return "失败: script_id 必填"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                "select id, name, summary from script_character_cards "
                "where script_id = %s order by name limit 80",
                (int(sid),),
            ).fetchall() or []
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ── global 级 ───────────────────────────────────────────


def _t_list_available_tools(args: dict) -> str:
    """元工具: LLM 自查当前工具表。"""
    origin = (args.get("origin") or "").strip()
    reg = get_registry()
    if origin:
        tools = reg.list_for_origin(origin)
    else:
        tools = reg.list_all()
    out = [
        {
            "name": t.name,
            "description": t.description,
            "scope": t.scope,
            "origins": sorted(t.origins),
            "destructive": t.destructive,
        }
        for t in tools
    ]
    return json.dumps(out, ensure_ascii=False, indent=2)


def _t_list_modules(args: dict) -> str:
    try:
        import modules as _modules
        cat = _modules.list_modules() if hasattr(_modules, "list_modules") else []
        return json.dumps(cat, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_list_available_models(args: dict) -> str:
    try:
        from model_registry import load_model_catalog
        cat = load_model_catalog() or {}
        # 只暴露简略信息,避免 prompt 爆炸
        apis = cat.get("apis") or []
        return json.dumps(
            [{"id": a.get("id"), "kind": a.get("kind"),
              "models": [m.get("id") for m in a.get("models") or []][:8]}
             for a in apis[:12]],
            ensure_ascii=False, indent=2,
        )
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ── 注册 ─────────────────────────────────────────────────


def register_query_tools() -> None:
    registry = get_registry()
    save_specs = [
        ("get_game_state",
         "返回当前 save 状态的精简快照: turn/player/world/relationships/memory 概要。"
         "fields 可选,只返指定一级 key。",
         {"type": "object", "properties": {
             "fields": {"type": "array", "items": {"type": "string"}}}},
         _t_get_game_state),
        ("query_memory",
         "按 kind / characters / time_label 过滤检索 memory.items。",
         {"type": "object", "properties": {
             "kind": {"type": "string",
                      "enum": ["canon_fact", "runtime_fact", "hypothesis", "user_constraint"]},
             "characters": {"type": "array", "items": {"type": "string"}},
             "time_label": {"type": "string"},
             "limit": {"type": "integer", "default": 20, "minimum": 1, "maximum": 100},
         }},
         _t_query_memory),
        ("get_user_variables",
         "返回 worldline.user_variables 当前键值对。",
         {"type": "object", "properties": {}},
         _t_get_user_variables),
        ("get_current_scene",
         "返回当前 scene/encounter/active_entities 快照。",
         {"type": "object", "properties": {}},
         _t_get_current_scene),
        ("get_known_events",
         "返回最近的 world.known_events。",
         {"type": "object", "properties": {"limit": {"type": "integer"}}},
         _t_get_known_events),
        ("list_relationships",
         "返回 relationships dict。",
         {"type": "object", "properties": {}},
         _t_list_relationships),
        ("get_pending_questions",
         "返回当前 pending_questions。",
         {"type": "object", "properties": {}},
         _t_get_pending_questions),
        ("get_pending_writes",
         "返回当前 pending_writes (待审批的状态写入)。",
         {"type": "object", "properties": {}},
         _t_get_pending_writes),
        ("recent_audit_log",
         "返回最近的 audit_log,可按 kind 过滤。",
         {"type": "object", "properties": {
             "kind": {"type": "string"},
             "limit": {"type": "integer", "default": 10},
         }},
         _t_recent_audit_log),
    ]
    for name, desc, schema, exec_ in save_specs:
        if not registry.has(name):
            registry.register(ToolSpec(
                name=name, description=desc, input_schema=schema,
                executor=exec_, scope="save", origins=_READ_ANY_ORIGIN,
            ))

    user_specs = [
        ("list_my_personas", "列出当前用户的 persona。",
         {"type": "object", "properties": {}}, _t_list_my_personas),
        ("list_my_character_cards", "列出当前用户的角色卡。",
         {"type": "object", "properties": {}}, _t_list_my_character_cards),
        ("get_my_usage", "返回最近 N 天的 LLM token 消耗。",
         {"type": "object", "properties": {"days": {"type": "integer", "default": 30}}},
         _t_get_my_usage),
        ("list_scripts", "列出当前用户可见的剧本。",
         {"type": "object", "properties": {}}, _t_list_scripts),
    ]
    for name, desc, schema, exec_ in user_specs:
        if not registry.has(name):
            registry.register(ToolSpec(
                name=name, description=desc, input_schema=schema,
                executor=exec_, scope="user", origins=_READ_ANY_ORIGIN,
            ))

    script_specs = [
        ("get_script_chapters",
         "列出剧本的章节目录。可不带 script_id 时从 save 派生。",
         {"type": "object", "properties": {"script_id": {"type": "integer"}}},
         _t_get_script_chapters),
        ("list_script_npcs",
         "列出剧本附带的 NPC 角色卡。",
         {"type": "object", "properties": {"script_id": {"type": "integer"}}},
         _t_list_script_npcs),
    ]
    for name, desc, schema, exec_ in script_specs:
        if not registry.has(name):
            registry.register(ToolSpec(
                name=name, description=desc, input_schema=schema,
                executor=exec_, scope="script", origins=_READ_ANY_ORIGIN,
            ))

    global_specs = [
        ("list_available_tools",
         "元工具: 列出当前所有已注册工具。可按 origin 过滤(返回 LLM 可见的子集)。",
         {"type": "object", "properties": {"origin": {"type": "string"}}},
         _t_list_available_tools),
        ("list_modules",
         "列出所有 5E 模组目录。",
         {"type": "object", "properties": {}},
         _t_list_modules),
        ("list_available_models",
         "列出当前可用的 LLM API + model 配置。",
         {"type": "object", "properties": {}},
         _t_list_available_models),
    ]
    for name, desc, schema, exec_ in global_specs:
        if not registry.has(name):
            registry.register(ToolSpec(
                name=name, description=desc, input_schema=schema,
                executor=exec_, scope="global", origins=_READ_ANY_ORIGIN,
            ))


__all__ = ["register_query_tools"]
