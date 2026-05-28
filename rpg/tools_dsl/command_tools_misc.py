"""
command_tools_misc.py — task 87 Phase 2 / 3 / 4 余下工具

集中实现:
  A 类补全 (save / user 级 mutate):
    set_permission_mode       save     (敏感,只 ui_button + api_direct)
    set_preference            user
    create_persona            user
    delete_persona            user destructive
    create_character_card     user
    delete_character_card     user destructive
    inject_pending_question   save     (debug,UI/API only)

  A 类管理员级 (MCP / 模型 / skills):
    mcp_server_enabled        user (admin)
    mcp_server_start          user
    mcp_server_stop           user
    mcp_server_validate       user
    mcp_server_delete         user destructive
    select_model              user

  C 类异步包装 (Phase 4):
    start_script_import       user     (返回 job_id,事件流仍走 /api/scripts/import-jobs/{id}/stream 副通道)
    get_import_status         user
    list_my_import_jobs       user
    cancel_import_job         user
    resplit_script            user destructive
    delete_script             user destructive
    probe_models              user

  B 类补全查询:
    get_save_detail           user
    get_chapter_facts         script
    get_worldbook             script
    get_my_stats              user
    list_my_credentials_meta  user (只元数据,不返 key)
"""
from __future__ import annotations

import json
from typing import Any

from tools_dsl.command_dispatcher import ToolSpec, get_registry

# task 87 Phase 7 安全审查:
#   _USER_READ      : 任意 origin (含 LLM 与 console_assistant) — read-only
#   _USER_MUTATE    : UI/API + console_assistant — LLM 仍禁;console_assistant 是「带方向盘的 agent」,
#                     语义上等同 UI 按按钮(由用户驱动),允许 mutate
#   _USER_DEST      : UI/API + console_assistant — destructive (console_assistant 走二次确认)
#   _SAVE_OK        : UI/API + LLM + console_assistant — save 级安全 mutate
#   _SAVE_SENSITIVE : 仅 UI/API — set_permission_mode 等敏感开关(console_assistant 也禁,
#                     这些是 UI 显式审批工具,助手不该自调)
#   _ADMIN          : UI/API + console_assistant — MCP server 管理 (用户用助手帮自己开/关)
# task 48 新增 console_assistant origin。
_USER_READ = frozenset({"ui_button", "api_direct", "llm_set", "llm_chat", "console_assistant"})
_USER_MUTATE = frozenset({"ui_button", "api_direct", "console_assistant"})
_USER_DEST = frozenset({"ui_button", "api_direct", "console_assistant"})
_SAVE_OK = frozenset({"ui_button", "api_direct", "llm_set", "llm_chat", "console_assistant"})
_SAVE_SENSITIVE = frozenset({"ui_button", "api_direct"})
_ADMIN = frozenset({"ui_button", "api_direct", "console_assistant"})
# 旧别名,保持向后兼容(misc 文件 user_specs 表里用)
_USER_OK = _USER_READ


# ────────────────────────────────────────────────────────────
# A 类补全 (save 级 mutate)
# ────────────────────────────────────────────────────────────


def _t_set_permission_mode(state: Any, args: dict) -> str:
    mode = (args.get("mode") or "").strip()
    # 通过 state 层的 _normalize_permission_mode 判断是否合法。
    # normalize 对无效值返回 "full_access" 默认值，而有效值按原名返回。
    # 我们先 normalize，如果输入本身就是无意义字符串（godmode 等）则 normalize → full_access
    # 但区分不了 "full_access 因为有效" vs "full_access 因为 fallback"。
    # 因此：凡是 normalize mapping 里存在的 key，视为合法；不在 mapping key 且 normalize 结果是 fallback 则非法。
    try:
        _VALID_INPUTS = {
            "只读", "只读模式", "suggest", "read", "read_only", "plan",
            "默认权限", "default",
            "auto", "自动审查", "auto_review", "review",
            "完全访问权限", "full", "full_access",
            "strict",  # 映射到 full_access (向后兼容旧 API)
        }
        if mode.lower() not in _VALID_INPUTS:
            return f"失败: mode 非法 {mode!r} (允许: default/auto_review/full_access/read_only)"
    except Exception:
        if mode not in {"default", "auto_review", "full_access", "read_only"}:
            return f"失败: mode 非法 {mode!r} (允许: default/auto_review/full_access/read_only)"
    try:
        state.set_permission_mode(mode)
        return f"permissions.mode → {mode}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_inject_pending_question(state: Any, args: dict) -> str:
    """debug 用: 注入一个 pending_question (前端可见可点)"""
    question = (args.get("question") or "").strip()
    if not question:
        return "失败: question 为空"
    options = args.get("options") or []
    if not isinstance(options, list):
        options = []
    source = (args.get("source") or "gm:json").strip()
    import secrets as _s
    qid = f"qmanual_{_s.token_urlsafe(6)}"
    permissions = state.data.setdefault("permissions", {})
    permissions.setdefault("pending_questions", []).append({
        "id": qid,
        "question": question,
        "options": list(options),
        "source": source,
        "turn": state.data.get("turn", 0),
    })
    return f"pending_question 注入: {qid}"


# ────────────────────────────────────────────────────────────
# user 级: preference / persona / character_card
# ────────────────────────────────────────────────────────────


def _t_set_preference(user_id: int, args: dict) -> str:
    key = (args.get("key") or "").strip()
    value = args.get("value")
    if not key:
        return "失败: key 为空"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            from psycopg.types.json import Jsonb
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (user_id,),
            ).fetchone()
            prefs = (row and row.get("preferences")) or {}
            if not isinstance(prefs, dict):
                prefs = {}
            prefs[key] = value
            db.execute(
                "insert into user_preferences (user_id, preferences) values (%s, %s) "
                "on conflict (user_id) do update set preferences = excluded.preferences, "
                "updated_at = now()",
                (user_id, Jsonb(prefs)),
            )
        return f"preference[{key}] = {json.dumps(value, ensure_ascii=False)[:80]}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_create_persona(user_id: int, args: dict) -> str:
    name = (args.get("name") or "").strip()
    summary = (args.get("summary") or "").strip()
    if not name:
        return "失败: name 为空"
    payload = {
        "name": name,
        "personality": summary,
        "role": (args.get("role") or "").strip(),
        "background": (args.get("background") or "").strip(),
        "appearance": (args.get("appearance") or "").strip(),
        "tags": args.get("tags") or [],
    }
    try:
        from platform_app.user_cards import upsert_persona
        row = upsert_persona(user_id, payload)
        return f"persona 创建: id={row.get('id')} name={name} slug={row.get('slug')}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_delete_persona(user_id: int, args: dict) -> str:
    pid = args.get("persona_id")
    if not isinstance(pid, (int, float, str)) or not str(pid).lstrip("-").isdigit():
        return "失败: persona_id 必须整数"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "delete from user_personas where id = %s and user_id = %s returning id",
                (int(pid), user_id),
            ).fetchone()
            if not row:
                return f"失败: persona {pid} 不属于当前用户或不存在"
        return f"persona {pid} 已删除"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_create_character_card(user_id: int, args: dict) -> str:
    name = (args.get("name") or "").strip()
    summary = (args.get("summary") or "").strip()
    if not name:
        return "失败: name 为空"
    payload = {
        "name": name,
        "personality": summary,
        "identity": (args.get("identity") or "").strip(),
        "appearance": (args.get("appearance") or "").strip(),
        "speech_style": (args.get("speech_style") or "").strip(),
        "current_status": (args.get("current_status") or "").strip(),
        "secrets": (args.get("secrets") or "").strip(),
        "aliases": args.get("aliases") or [],
        "sample_dialogue": args.get("sample_dialogue") or [],
        "tags": args.get("tags") or [],
    }
    try:
        from platform_app.user_cards import upsert_user_card
        row = upsert_user_card(user_id, payload)
        return f"角色卡创建: id={row.get('id')} name={name} slug={row.get('slug')}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_delete_character_card(user_id: int, args: dict) -> str:
    cid = args.get("card_id")
    if not isinstance(cid, (int, float, str)) or not str(cid).lstrip("-").isdigit():
        return "失败: card_id 必须整数"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "delete from user_character_cards where id = %s and user_id = %s returning id",
                (int(cid), user_id),
            ).fetchone()
            if not row:
                return f"失败: card {cid} 不属于当前用户或不存在"
        return f"角色卡 {cid} 已删除"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────
# MCP 管理 (admin 工具,只 ui_button)
# ────────────────────────────────────────────────────────────


def _t_mcp_server_enable(user_id: int, args: dict) -> str:
    sid = (args.get("server_id") or "").strip()
    enabled = bool(args.get("enabled"))
    if not sid:
        return "失败: server_id 为空"
    try:
        import tools_dsl.tool_registry as _tr
        catalog = _tr.load_mcp_catalog()
        servers = catalog.get("servers", [])
        for s in servers:
            if s.get("id") == sid:
                s["enabled"] = enabled
                break
        else:
            return f"失败: 未找到 server_id={sid}"
        _tr.save_mcp_catalog(catalog) if hasattr(_tr, "save_mcp_catalog") else None
        return f"MCP server {sid} enabled → {enabled}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_mcp_server_start(user_id: int, args: dict) -> str:
    sid = (args.get("server_id") or "").strip()
    if not sid:
        return "失败: server_id 为空"
    try:
        import mcp_broker
        result = mcp_broker.start_server(sid) if hasattr(mcp_broker, "start_server") else {"ok": False, "error": "start_server 未实现"}
        if not result.get("ok"):
            return f"失败: {result.get('error')}"
        return f"MCP server {sid} 已启动 (pid={result.get('pid','?')})"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_mcp_server_stop(user_id: int, args: dict) -> str:
    sid = (args.get("server_id") or "").strip()
    if not sid:
        return "失败: server_id 为空"
    try:
        import mcp_broker
        result = mcp_broker.stop_server(sid) if hasattr(mcp_broker, "stop_server") else {"ok": False, "error": "stop_server 未实现"}
        if not result.get("ok"):
            return f"失败: {result.get('error')}"
        return f"MCP server {sid} 已停止"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_select_model(user_id: int, args: dict) -> str:
    api_id = (args.get("api_id") or "").strip()
    model_real_name = (args.get("model") or "").strip()
    if not api_id or not model_real_name:
        return "失败: api_id 与 model 都不能为空"
    try:
        from psycopg.types.json import Jsonb

        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select preferences from user_preferences where user_id = %s",
                (user_id,),
            ).fetchone()
            prefs = (row and row.get("preferences")) or {}
            if not isinstance(prefs, dict):
                prefs = {}
            prefs["gm.api_id"] = api_id
            prefs["gm.model_real_name"] = model_real_name
            db.execute(
                "insert into user_preferences (user_id, preferences) values (%s, %s) "
                "on conflict (user_id) do update set preferences = excluded.preferences, "
                "updated_at = now()",
                (user_id, Jsonb(prefs)),
            )
        return f"GM 模型切换: {api_id} / {model_real_name}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────
# Phase 4 异步: script import / probe
# ────────────────────────────────────────────────────────────


def _t_start_script_import(user_id: int, args: dict) -> str:
    """从已上传的 upload_id 启动剧本导入。upload 走 /api/uploads (二进制,保留 HTTP)。"""
    upload_id = (args.get("upload_id") or "").strip()
    title = (args.get("title") or "").strip()
    if not upload_id or not title:
        return "失败: upload_id 与 title 都必填"
    try:
        from platform_app import script_import
        result = script_import.import_script(
            user_id=user_id,
            upload_id=upload_id,
            title=title,
            split_rule=(args.get("mode") or "regex").strip() or "regex",
        )
        sid = result.get("script_id")
        return f"导入剧本启动: script_id={sid} (事件流: /api/scripts/import-jobs/{result.get('job_id','?')}/stream)"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_get_import_status(user_id: int, args: dict) -> str:
    script_id = args.get("script_id")
    if not isinstance(script_id, (int, float, str)) or not str(script_id).lstrip("-").isdigit():
        return "失败: script_id 必须整数"
    try:
        from platform_app import script_import
        status = script_import.get_sync_status(user_id, int(script_id))
        return json.dumps(status, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_list_my_import_jobs(user_id: int, args: dict) -> str:
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                "select id, script_id, status, progress, created_at, updated_at "
                "from script_import_jobs where user_id = %s "
                "order by created_at desc limit 30",
                (user_id,),
            ).fetchall() or []
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_cancel_import_job(user_id: int, args: dict) -> str:
    job_id = (args.get("job_id") or "").strip()
    if not job_id:
        return "失败: job_id 为空"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "update script_import_jobs set status = 'cancelled', "
                "updated_at = now() where id = %s and user_id = %s "
                "and status in ('pending','running') returning id",
                (job_id, user_id),
            ).fetchone()
            if not row:
                return f"失败: job {job_id} 不属于当前用户、不存在,或已终止"
        return f"取消导入 job {job_id} ✓"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_resplit_script(user_id: int, args: dict) -> str:
    script_id = args.get("script_id")
    mode = (args.get("mode") or "regex").strip() or "regex"
    if not isinstance(script_id, (int, float, str)) or not str(script_id).lstrip("-").isdigit():
        return "失败: script_id 必须整数"
    try:
        from platform_app import script_import
        result = script_import.resplit_script(user_id=user_id, script_id=int(script_id), split_rule=mode)
        return f"重新拆分: chapters={result.get('chapter_count','?')} (mode={mode})"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_delete_script(user_id: int, args: dict) -> str:
    script_id = args.get("script_id")
    force = bool(args.get("force"))
    if not isinstance(script_id, (int, float, str)) or not str(script_id).lstrip("-").isdigit():
        return "失败: script_id 必须整数"
    try:
        from platform_app import script_import
        result = script_import.delete_script(user_id=user_id, script_id=int(script_id), force=force)
        return f"剧本 {script_id} 已删除 (chapters_dropped={result.get('chapters_dropped',0)})"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_probe_models(user_id: int, args: dict) -> str:
    api_id = (args.get("api_id") or "").strip() or None
    try:
        import model_probe
        result = model_probe.probe(user_id=user_id, api_id_filter=api_id) if hasattr(model_probe, "probe") else None
        if result is None:
            return "失败: model_probe.probe 未提供"
        return json.dumps(result, ensure_ascii=False, indent=2)[:1500]
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────
# B 类补全查询
# ────────────────────────────────────────────────────────────


def _t_get_save_detail(user_id: int, args: dict) -> str:
    save_id = args.get("save_id")
    if not isinstance(save_id, (int, float, str)) or not str(save_id).lstrip("-").isdigit():
        return "失败: save_id 必须整数"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select id, title, script_id, active_commit_id, created_at, updated_at "
                "from game_saves where id = %s and user_id = %s",
                (int(save_id), user_id),
            ).fetchone()
            if not row:
                return f"失败 (权限): save {save_id} 不属于当前用户"
        return json.dumps(dict(row), ensure_ascii=False, default=str, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_get_chapter_facts(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = script_id or args.get("script_id")
    chapter_index = args.get("chapter_index")
    if not sid:
        return "失败: script_id 必填"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if chapter_index is None:
                rows = db.execute(
                    "select chapter_index, fact_text from chapter_facts "
                    "where script_id = %s order by chapter_index limit 200",
                    (int(sid),),
                ).fetchall() or []
            else:
                rows = db.execute(
                    "select chapter_index, fact_text from chapter_facts "
                    "where script_id = %s and chapter_index = %s",
                    (int(sid), int(chapter_index)),
                ).fetchall() or []
        return json.dumps([dict(r) for r in rows[:50]], ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_get_worldbook(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = script_id or args.get("script_id")
    query = (args.get("query") or "").strip()
    if not sid:
        return "失败: script_id 必填"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if query:
                # 简单 LIKE 检索
                rows = db.execute(
                    "select id, key, content from script_worldbook "
                    "where script_id = %s and (key ilike %s or content ilike %s) limit 30",
                    (int(sid), f"%{query}%", f"%{query}%"),
                ).fetchall() or []
            else:
                rows = db.execute(
                    "select id, key, content from script_worldbook "
                    "where script_id = %s order by key limit 30",
                    (int(sid),),
                ).fetchall() or []
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_get_my_stats(user_id: int, args: dict) -> str:
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select "
                "(select count(*) from game_saves where user_id = %s) as save_count, "
                "(select count(*) from scripts where owner_id = %s) as script_count, "
                "(select count(*) from user_personas where user_id = %s) as persona_count, "
                "(select count(*) from user_character_cards where user_id = %s) as card_count",
                (user_id, user_id, user_id, user_id),
            ).fetchone()
        return json.dumps(dict(row or {}), ensure_ascii=False, default=str, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_list_my_credentials_meta(user_id: int, args: dict) -> str:
    """只返凭证元数据(provider、最后更新时间),**永不返 key 本身**。"""
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            rows = db.execute(
                "select provider, length(key_encrypted) as key_len, updated_at "
                "from user_credentials where user_id = %s",
                (user_id,),
            ).fetchall() or []
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, default=str, indent=2)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────
# 注册
# ────────────────────────────────────────────────────────────


def register_misc_tools() -> None:
    registry = get_registry()
    save_specs = [
        ("set_permission_mode",
         "切换写入权限模式: full_access(LLM 自由写)/auto_review(自动审批)/default(默认)/read_only(LLM 不写)",
         {"type": "object",
          "properties": {"mode": {"type": "string",
                                  "enum": ["default", "auto_review", "full_access", "read_only"]}},
          "required": ["mode"]},
         _t_set_permission_mode, "save", _SAVE_SENSITIVE, False),
        ("inject_pending_question",
         "向当前 save 注入一个待回答问题 (debug 用,只允许 UI/API)",
         {"type": "object",
          "properties": {
              "question": {"type": "string"},
              "options": {"type": "array", "items": {"type": "string"}},
              "source": {"type": "string", "default": "gm:json"},
          }, "required": ["question"]},
         _t_inject_pending_question, "save",
         # task 48: inject_pending_question 是 debug 用工具,助手不应自调
         frozenset({"ui_button", "api_direct"}), False),
    ]
    for name, desc, schema, exec_, scope, origins, destructive in save_specs:
        if not registry.has(name):
            registry.register(ToolSpec(
                name=name, description=desc, input_schema=schema,
                executor=exec_, scope=scope, origins=origins, destructive=destructive,
            ))

    user_specs = [
        # task 87 Phase 7 安全审查 — user 级 mutate (跨 save 影响) 全部禁 LLM:
        ("set_preference", "设置当前用户偏好键值对 (写 user_preferences.preferences 的某一项)",
         {"type": "object",
          "properties": {"key": {"type": "string"}, "value": {}},
          "required": ["key", "value"]},
         _t_set_preference, _USER_MUTATE, False),  # 跨 save,LLM 禁
        ("create_persona", "新建一个用户 persona (玩家身份模板)。summary 写入 personality 字段。",
         {"type": "object",
          "properties": {
              "name": {"type": "string"},
              "summary": {"type": "string", "description": "性格简介,写入 personality 字段"},
              "role": {"type": "string"},
              "background": {"type": "string"},
              "appearance": {"type": "string"},
              "tags": {"type": "array", "items": {"type": "string"}},
          },
          "required": []},  # handler 自行校验并返回"name 为空"友好消息
         _t_create_persona, _USER_MUTATE, False),  # 跨 save 持久资源,LLM 禁
        ("delete_persona", "永久删除 persona",
         {"type": "object", "properties": {"persona_id": {"type": "integer"}}, "required": ["persona_id"]},
         _t_delete_persona, _USER_DEST, True),
        ("create_character_card",
         "新建一张可复用角色卡片 (跨 save 共享)。summary 写入 personality 字段。\n"
         "**不是** 改剧情内玩家名 (那是 set_player_name, 助手不管)。",
         {"type": "object",
          "properties": {
              "name": {"type": "string", "description": "角色名 (例: 晓星 / 阿狸)"},
              "summary": {"type": "string", "description": "性格简介 (例: 开朗元气 / 冷静腹黑)"},
              "identity": {"type": "string", "description": "身份背景 1 句话 (例: 女高中生穿越者)"},
              "appearance": {"type": "string", "description": "外貌特征"},
              "speech_style": {"type": "string", "description": "说话方式"},
              "current_status": {"type": "string", "description": "当前状态"},
              "secrets": {"type": "string", "description": "未公开秘密"},
              "aliases": {"type": "array", "items": {"type": "string"}},
              "sample_dialogue": {"type": "array", "items": {"type": "string"}},
              "tags": {"type": "array", "items": {"type": "string"}},
          },
          # handler 自行校验 name 为空,返回"name 为空"友好消息
          "required": []},
         _t_create_character_card, _USER_MUTATE, False),  # 跨 save,LLM 禁
        ("delete_character_card", "永久删除角色卡",
         {"type": "object", "properties": {"card_id": {"type": "integer"}}, "required": ["card_id"]},
         _t_delete_character_card, _USER_DEST, True),
        ("mcp_server_enable", "切换 MCP server 启用状态 (admin)",
         {"type": "object",
          "properties": {"server_id": {"type": "string"}, "enabled": {"type": "boolean"}},
          "required": ["server_id", "enabled"]},
         _t_mcp_server_enable, _ADMIN, False),
        ("mcp_server_start", "启动指定 MCP server",
         {"type": "object", "properties": {"server_id": {"type": "string"}}, "required": []},
         _t_mcp_server_start, _ADMIN, False),
        ("mcp_server_stop", "停止指定 MCP server",
         {"type": "object", "properties": {"server_id": {"type": "string"}}, "required": ["server_id"]},
         _t_mcp_server_stop, _ADMIN, False),
        ("select_model", "切换当前 GM 使用的模型 (api_id + model_real_name)",
         {"type": "object",
          "properties": {"api_id": {"type": "string"}, "model": {"type": "string"}},
          "required": []},  # handler 自行校验并返回"不能为空"友好消息
         _t_select_model, _USER_MUTATE, False),  # LLM 改自己的模型?坚决禁
        # Phase 4 异步 - 启动/取消任务都禁 LLM (会消耗资源/触发外部 LLM 调用)
        ("start_script_import",
         "从已上传 upload_id 启动剧本导入 (上传走 /api/uploads,这里只触发导入). "
         "返回 script_id 与 job_id,事件流走 /api/scripts/import-jobs/{job_id}/stream",
         {"type": "object",
          "properties": {
              "upload_id": {"type": "string"},
              "title": {"type": "string"},
              "mode": {"type": "string", "default": "regex"},
          }, "required": []},  # handler 自行校验并返回"必填"友好消息
         _t_start_script_import, _USER_MUTATE, False),
        ("get_import_status", "查询剧本导入进度",
         {"type": "object", "properties": {"script_id": {"type": "integer"}}, "required": ["script_id"]},
         _t_get_import_status, _USER_READ, False),  # read OK
        ("list_my_import_jobs", "列出当前用户的导入任务",
         {"type": "object", "properties": {}}, _t_list_my_import_jobs, _USER_READ, False),
        ("cancel_import_job", "取消进行中的导入任务",
         {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": []},
         _t_cancel_import_job, _USER_MUTATE, False),  # 跨任务 mutate,LLM 禁
        ("resplit_script", "对已导入剧本重新切章",
         {"type": "object",
          "properties": {"script_id": {"type": "integer"}, "mode": {"type": "string", "default": "regex"}},
          "required": ["script_id"]},
         _t_resplit_script, _USER_DEST, True),
        ("delete_script", "永久删除剧本及其所有派生数据",
         {"type": "object",
          "properties": {"script_id": {"type": "integer"}, "force": {"type": "boolean", "default": False}},
          "required": ["script_id"]},
         _t_delete_script, _USER_DEST, True),
        ("probe_models", "探测可用模型 (异步,可能耗时)",
         {"type": "object", "properties": {"api_id": {"type": "string"}}},
         _t_probe_models, _USER_MUTATE, False),  # 触发外部 LLM 调用,LLM 不能自启
        # B 类补全 (全部 read)
        ("get_save_detail", "返回指定 save 的元数据(标题/script_id/激活 commit 等)",
         {"type": "object", "properties": {"save_id": {"type": "integer"}}, "required": ["save_id"]},
         _t_get_save_detail, _USER_READ, False),
        ("get_my_stats", "返回当前用户的存档/剧本/persona/卡片计数",
         {"type": "object", "properties": {}}, _t_get_my_stats, _USER_READ, False),
        ("list_my_credentials_meta",
         "只返凭证元数据(provider/last_updated),**永不返 key 本身**",
         {"type": "object", "properties": {}}, _t_list_my_credentials_meta, _USER_READ, False),
    ]
    for name, desc, schema, exec_, origins, destructive in user_specs:  # type: ignore[assignment]
        if not registry.has(name):
            registry.register(ToolSpec(
                name=name, description=desc, input_schema=schema,
                executor=exec_, scope="user", origins=origins, destructive=destructive,
            ))

    script_specs = [
        ("get_chapter_facts",
         "按 script_id + chapter_index 检索章节事实表",
         {"type": "object",
          "properties": {"script_id": {"type": "integer"}, "chapter_index": {"type": "integer"}},
          "required": []},
         _t_get_chapter_facts),
        ("get_worldbook",
         "按 script_id + 可选 query 检索世界书条目",
         {"type": "object",
          "properties": {"script_id": {"type": "integer"}, "query": {"type": "string"}},
          "required": []},
         _t_get_worldbook),
    ]
    for name, desc, schema, exec_ in script_specs:  # type: ignore[assignment]
        if not registry.has(name):
            registry.register(ToolSpec(
                name=name, description=desc, input_schema=schema,
                executor=exec_, scope="script", origins=_USER_OK, destructive=False,
            ))

    # ────────────────────────────────────────────────────────────
    # task 49: 创意工具 — generate/refine_character_card_draft
    # 仅 console_assistant + api_direct 可调; LLM 自由叙事 (llm_chat) 不允许
    # 自创角色卡 (该走 gm_provisional active_entity 路径)。
    # ────────────────────────────────────────────────────────────
    _CREATIVE_ORIGINS = frozenset({"console_assistant", "api_direct"})

    def _t_generate_card_draft(user_id: int, args: dict) -> str:
        try:
            import character_card_generator as ccg
            result = ccg.generate_character_card_draft(
                brief=str(args.get("brief") or ""),
                user_id=user_id,
                script_id=args.get("script_id"),
                kind=str(args.get("kind") or "user"),
                phase=args.get("phase"),
                timeout_sec=int(args.get("timeout_sec") or 30),
            )
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as exc:
            return f"失败: {type(exc).__name__}: {exc}"

    def _t_refine_card_draft(user_id: int, args: dict) -> str:
        try:
            import character_card_generator as ccg
            prev = args.get("previous_draft")
            if not isinstance(prev, dict):
                return "失败: previous_draft 必须是对象"
            result = ccg.refine_character_card_draft(
                previous_draft=prev,
                feedback=str(args.get("feedback") or ""),
                user_id=user_id,
                script_id=args.get("script_id"),
                timeout_sec=int(args.get("timeout_sec") or 30),
            )
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as exc:
            return f"失败: {type(exc).__name__}: {exc}"

    creative_specs = [
        ("generate_character_card_draft",
         "把简短人设描述扩展为符合当前剧本规范的角色卡 candidate (不写 DB,只返回 draft+validations)",
         {"type": "object",
          "properties": {
              "brief": {"type": "string", "description": "用户简短描述,如 '20 岁女法师,流亡贵族'"},
              "script_id": {"type": "integer", "description": "目标剧本 id (用于查重/phase/风格)"},
              "kind": {"type": "string", "enum": ["user", "script"], "default": "user"},
              "phase": {"type": "string", "description": "目标 phase 标签,空则由 DB 推断"},
              "timeout_sec": {"type": "integer", "default": 30},
          },
          "required": ["brief"]},
         _t_generate_card_draft),
        ("refine_character_card_draft",
         "用 previous_draft + 用户反馈重新生成卡片 candidate (走同一 5 层 validator)",
         {"type": "object",
          "properties": {
              "previous_draft": {"type": "object", "description": "上一版 draft (generate 返回的 draft 字段)"},
              "feedback": {"type": "string", "description": "用户反馈,如 '把性格改得更内向'"},
              "script_id": {"type": "integer"},
              "timeout_sec": {"type": "integer", "default": 30},
          },
          "required": ["previous_draft", "feedback"]},
         _t_refine_card_draft),
    ]
    for name, desc, schema, exec_ in creative_specs:  # type: ignore[assignment]
        if not registry.has(name):
            registry.register(ToolSpec(
                name=name, description=desc, input_schema=schema,
                executor=exec_, scope="user", origins=_CREATIVE_ORIGINS, destructive=False,
            ))

    # ────────────────────────────────────────────────────────────
    # task 57: navigate_to_setting — 助手页面导航工具
    # 用户问"XX 功能在哪里设置",助手调此工具直接引导到具体页面 + 高亮元素。
    # 不修改任何 state,只返回 NAVIGATE:<target>|<reason> 哨兵字符串,
    # 由 console_assistant SSE 流识别并转成 navigation_required 事件 yield 出去。
    # 仅 console_assistant + api_direct 可用(纯 UI 导航,LLM 自由叙事不该调)。
    # ────────────────────────────────────────────────────────────
    _NAV_ORIGINS = frozenset({"console_assistant", "api_direct"})
    _NAV_TARGETS = [
        # task 110: 跨 SPA 跳到独立游戏 Console — 用户激活存档后想"进入游戏"用这个
        "game_console",
        # settings 子页
        "settings.preferences",
        "settings.models",
        "settings.models.gm",
        "settings.models.console_assistant",
        "settings.modelparams",
        "settings.memory",
        "settings.permissions",
        "settings.deploy",
        "settings.danger",
        "settings.profile",
        "settings.api",
        # scripts
        "scripts.list",
        "scripts.import",
        # saves
        "saves.list",
        "saves.branches",
        # cards
        "cards.user",
        "cards.npc",
        # 其它平台页
        "personas",
        "library",
        "usage",
        "modules",
        "me",
        "me.edit",
        "me.settings",
    ]

    def _t_navigate_to_setting(user_id: int, args: dict) -> str:
        target = (args.get("target") or "").strip()
        reason = (args.get("reason") or "").strip()
        if not target:
            return "失败: target 为空"
        # 不强校验枚举(允许未来扩展),只提示一下
        return f"NAVIGATE:{target}|{reason}"

    nav_targets_enum = ", ".join(_NAV_TARGETS)
    nav_spec = ToolSpec(
        name="navigate_to_setting",
        description=(
            "当用户问 'XX 功能在哪里' / '怎么去 XX 页' / 想跳转到某设置项时,"
            "调此工具引导用户到指定页面位置。会触发前端跳转 + 高亮目标元素。"
            f"target 推荐取下列枚举: {nav_targets_enum}。"
            "也支持未列出的 pageId.anchor 自由扩展。reason 简短说明为什么跳。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "页面锚点 id,推荐枚举或 pageId.anchor",
                },
                "reason": {
                    "type": "string",
                    "description": "简短说明跳转原因,前端可能展示给用户",
                },
            },
            "required": ["target"],
        },
        executor=_t_navigate_to_setting,
        scope="user",
        origins=_NAV_ORIGINS,
        destructive=False,
    )
    if not registry.has(nav_spec.name):
        registry.register(nav_spec)

    # ────────────────────────────────────────────────────────────
    # task 61: ask_user_choice — 助手结构化选择题工具
    # 当助手需要用户在有限选项里做选择(性格/路线/类型/分支),
    # 调此工具让 UI 渲染按钮组,而不是裸文本列 1/2/3 让用户打字。
    # 类似 navigate_to_setting,返回 USER_CHOICE:<json> 哨兵,
    # console_assistant SSE 流识别后转成 user_choice_required 事件 yield,
    # 并中断当前 LLM loop 等用户在 UI 上选完。
    # 仅 console_assistant 可用(LLM 自由叙事不应弹 UI 选择题)。
    # ────────────────────────────────────────────────────────────
    _CHOICE_ORIGINS = frozenset({"console_assistant"})

    def _t_ask_user_choice(user_id: int, args: dict) -> str:
        question = (args.get("question") or "").strip()
        if not question:
            return "失败: question 为空"
        options = args.get("options") or []
        if not isinstance(options, list):
            return "失败: options 必须是数组"
        # 清理为纯字符串列表
        clean_options = [str(o).strip() for o in options if str(o).strip()]
        if len(clean_options) < 2:
            return "失败: options 至少 2 项"
        if len(clean_options) > 6:
            return "失败: options 最多 6 项"
        allow_free_text = args.get("allow_free_text")
        if allow_free_text is None:
            allow_free_text = True
        allow_free_text = bool(allow_free_text)
        context = (args.get("context") or "").strip()
        payload = {
            "question": question,
            "options": clean_options,
            "allow_free_text": allow_free_text,
            "context": context,
        }
        return f"USER_CHOICE:{json.dumps(payload, ensure_ascii=False)}"

    choice_spec = ToolSpec(
        name="ask_user_choice",
        description=(
            "向用户提一个有限选项的问题, 用户在 UI 上点按钮选答案。"
            "比让用户打字快。当你需要确认偏好/路线/分支选择时调它, "
            "而不是裸文本列 1/2/3。options 数组每项是一个候选答案字符串, "
            "allow_free_text=true 时 UI 会额外显示一个自由输入按钮, "
            "让用户也能描述自己的想法。context 可选, 解释为什么问这个。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "问题文本",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选答案列表 (2-6 项), 纯字符串",
                    "minItems": 2,
                    "maxItems": 6,
                },
                "allow_free_text": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否允许用户自由输入(默认 true)",
                },
                "context": {
                    "type": "string",
                    "description": "可选, 解释为什么问这个",
                },
            },
            "required": ["question", "options"],
        },
        executor=_t_ask_user_choice,
        scope="user",
        origins=_CHOICE_ORIGINS,
        destructive=False,
    )
    if not registry.has(choice_spec.name):
        registry.register(choice_spec)

    # task 74 — ask_user_text: 自由文本输入 (用户需要打字而不是点选项时)。
    def _t_ask_user_text(user_id: int, args: dict) -> str:
        question = (args.get("question") or "").strip()
        if not question:
            return "失败: question 为空"
        placeholder = (args.get("placeholder") or "").strip()
        context = (args.get("context") or "").strip()
        payload = {
            "question": question,
            "placeholder": placeholder,
            "context": context,
        }
        return f"USER_TEXT:{json.dumps(payload, ensure_ascii=False)}"

    text_spec = ToolSpec(
        name="ask_user_text",
        description=(
            "向用户提一个自由文本输入问题。当字段是名字/描述/长文本而不适合做选项时调它。"
            "UI 会弹一个输入框,placeholder 是占位提示,context 解释为什么问。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "问题文本"},
                "placeholder": {"type": "string", "description": "输入框 placeholder"},
                "context": {"type": "string", "description": "可选, 解释为什么问这个"},
            },
            "required": ["question"],
        },
        executor=_t_ask_user_text,
        scope="user",
        origins=_CHOICE_ORIGINS,
        destructive=False,
    )
    if not registry.has(text_spec.name):
        registry.register(text_spec)

    # task 68/72 — ui_describe / ui_invoke: 助手统一 UI 机制的两把通用工具。
    # 它们替代了"把 52 个子工具全塞 LLM tool list"的旧做法,
    # 让 LLM 只见 5 把工具:ui_describe / ui_invoke / ask_user_choice
    # / ask_user_text / navigate_to_setting。
    from ui_manifest import ui_describe as _ui_describe

    describe_spec = ToolSpec(
        name="ui_describe",
        description=(
            "按意图关键词查找当前可用的 UI action (角色卡/存档/剧本/设置等)。"
            "返回一组 action 卡片, 含 id / 描述 / 参数表 / 是否 destructive。"
            "用户每说一个想做的事, 先调 ui_describe(intent='用户原话关键词') 看选项, "
            "再决定是 ui_invoke 还是 ask_user_choice 继续问。"
            "page 可选, 限定页面范围 (saves/scripts/cards/settings)。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "intent": {"type": "string",
                           "description": "用户原话或关键词, 用于模糊匹配。空则返通用 top-N"},
                "page": {"type": "string",
                         "enum": ["saves", "scripts", "cards", "settings"],
                         "description": "限定页面范围 (可选)"},
                "limit": {"type": "integer", "default": 8, "minimum": 1, "maximum": 20},
            },
            "required": [],
        },
        executor=_ui_describe,
        scope="user",
        origins=_CHOICE_ORIGINS,
        destructive=False,
    )
    if not registry.has(describe_spec.name):
        registry.register(describe_spec)

    # task 96: ui_invoke 已删除。LLM 直接调具体工具 (create_character_card etc.)
    # 通过 native tool_use,缺 required 字段时 dispatcher 返普通错误,
    # LLM 读错误自己调 ask_user_choice。与 Anthropic Tool Search Tool 模式一致。


__all__ = ["register_misc_tools"]
