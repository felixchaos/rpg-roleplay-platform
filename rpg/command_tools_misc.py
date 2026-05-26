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

from command_dispatcher import ToolSpec, get_registry


# task 87 Phase 7 安全审查:
#   _USER_READ      : 任意 origin (含 LLM) — read-only,不修改任何 user/save 资源
#   _USER_MUTATE    : 仅 UI/API — LLM 不能修改跨 save 的 user 级资源 (持久 persona/卡片/偏好等)
#   _USER_DEST      : 仅 UI/API — destructive
#   _SAVE_OK        : 含 LLM — save 级安全 mutate (当前 save 上下文,LLM 自然有权改)
#   _SAVE_SENSITIVE : 仅 UI/API — set_permission_mode 等敏感开关
#   _ADMIN          : 仅 UI/API — MCP server 管理
_USER_READ = frozenset({"ui_button", "api_direct", "llm_set", "llm_chat"})
_USER_MUTATE = frozenset({"ui_button", "api_direct"})
_USER_DEST = frozenset({"ui_button", "api_direct"})
_SAVE_OK = frozenset({"ui_button", "api_direct", "llm_set", "llm_chat"})
_SAVE_SENSITIVE = frozenset({"ui_button", "api_direct"})
_ADMIN = frozenset({"ui_button", "api_direct"})
# 旧别名,保持向后兼容(misc 文件 user_specs 表里用)
_USER_OK = _USER_READ


# ────────────────────────────────────────────────────────────
# A 类补全 (save 级 mutate)
# ────────────────────────────────────────────────────────────


def _t_set_permission_mode(state: Any, args: dict) -> str:
    mode = (args.get("mode") or "").strip()
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
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "insert into user_personas (user_id, name, summary) "
                "values (%s, %s, %s) returning id",
                (user_id, name, summary),
            ).fetchone()
        return f"persona 创建: id={row['id']} name={name}"
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
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "insert into user_character_cards (user_id, name, summary) "
                "values (%s, %s, %s) returning id",
                (user_id, name, summary),
            ).fetchone()
        return f"角色卡创建: id={row['id']} name={name}"
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
        import tool_registry as _tr
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
        from platform_app.db import connect, init_db
        from psycopg.types.json import Jsonb
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
            mode=(args.get("mode") or "regex").strip() or "regex",
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
        result = script_import.resplit_script(user_id=user_id, script_id=int(script_id), mode=mode)
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
                "(select count(*) from scripts where user_id = %s) as script_count, "
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
         "向当前 save 注入一个待回答问题 (debug 用,UI/API 显式调)",
         {"type": "object",
          "properties": {
              "question": {"type": "string"},
              "options": {"type": "array", "items": {"type": "string"}},
              "source": {"type": "string", "default": "gm:json"},
          }, "required": ["question"]},
         _t_inject_pending_question, "save", _ADMIN, False),
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
        ("create_persona", "新建一个用户 persona",
         {"type": "object",
          "properties": {"name": {"type": "string"}, "summary": {"type": "string"}},
          "required": ["name"]},
         _t_create_persona, _USER_MUTATE, False),  # 跨 save 持久资源,LLM 禁
        ("delete_persona", "永久删除 persona",
         {"type": "object", "properties": {"persona_id": {"type": "integer"}}, "required": ["persona_id"]},
         _t_delete_persona, _USER_DEST, True),
        ("create_character_card", "新建一张角色卡",
         {"type": "object",
          "properties": {"name": {"type": "string"}, "summary": {"type": "string"}},
          "required": ["name"]},
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
         {"type": "object", "properties": {"server_id": {"type": "string"}}, "required": ["server_id"]},
         _t_mcp_server_start, _ADMIN, False),
        ("mcp_server_stop", "停止指定 MCP server",
         {"type": "object", "properties": {"server_id": {"type": "string"}}, "required": ["server_id"]},
         _t_mcp_server_stop, _ADMIN, False),
        ("select_model", "切换当前 GM 使用的模型 (api_id + model_real_name)",
         {"type": "object",
          "properties": {"api_id": {"type": "string"}, "model": {"type": "string"}},
          "required": ["api_id", "model"]},
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
          }, "required": ["upload_id", "title"]},
         _t_start_script_import, _USER_MUTATE, False),
        ("get_import_status", "查询剧本导入进度",
         {"type": "object", "properties": {"script_id": {"type": "integer"}}, "required": ["script_id"]},
         _t_get_import_status, _USER_READ, False),  # read OK
        ("list_my_import_jobs", "列出当前用户的导入任务",
         {"type": "object", "properties": {}}, _t_list_my_import_jobs, _USER_READ, False),
        ("cancel_import_job", "取消进行中的导入任务",
         {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
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
    for name, desc, schema, exec_, origins, destructive in user_specs:
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
    for name, desc, schema, exec_ in script_specs:
        if not registry.has(name):
            registry.register(ToolSpec(
                name=name, description=desc, input_schema=schema,
                executor=exec_, scope="script", origins=_USER_OK, destructive=False,
            ))


__all__ = ["register_misc_tools"]
