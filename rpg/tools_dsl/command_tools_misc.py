"""
command_tools_misc.py — task 87 Phase 2 / 3 / 4 余下工具

集中实现:
  A 类补全 (save / user 级 mutate):
    set_permission_mode       save     (敏感,只 ui_button + api_direct)
    set_preference            user
    inject_pending_question   save     (debug,UI/API only)

  A 类管理员级 (MCP / 模型 / skills):
    mcp_server_enabled        user (admin)
    mcp_server_start          user
    mcp_server_stop           user
    mcp_server_validate       user
    mcp_server_delete         user destructive
    select_model              user

  B 类补全查询:
    get_save_detail           user
    get_chapter_facts         script
    get_worldbook             script
    get_my_stats              user
    list_my_credentials_meta  user (只元数据,不返 key)

  已拆出:
    persona / character_card   → command_tools_persona.py
    script import / probe      → command_tools_imports.py
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
# SEC(M-1): MCP server 管理(全局共享 + 启动=任意代码执行)严格限 UI/API,**不含 console_assistant**
# —— 这两个 origin 的 MCP HTTP 端点已被 get_current_admin_strict 强制 admin(Batch 1)。
_MCP_ADMIN = frozenset({"ui_button", "api_direct"})
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
    # SEC(M-12): value 限长,防 console_assistant LLM 放大存储。
    if value is not None and len(json.dumps(value, ensure_ascii=False)) > 4096:
        return "失败: value 过大(上限 4096 字符)"
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
            # SEC(M-12): 总 blob 上限,防累积放大。
            if len(json.dumps(prefs, ensure_ascii=False).encode("utf-8")) > 32 * 1024:
                return "失败: 偏好总量超限(32KB)"
            db.execute(
                "insert into user_preferences (user_id, preferences) values (%s, %s) "
                "on conflict (user_id) do update set preferences = excluded.preferences, "
                "updated_at = now()",
                (user_id, Jsonb(prefs)),
            )
        return f"preference[{key}] = {json.dumps(value, ensure_ascii=False)[:80]}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────
# MCP 管理 (admin 工具)
# SEC(M-1): MCP server 配置全局共享、启动 = 以服务进程身份执行任意代码。这 3 个工具只允许
# ui_button / api_direct origin —— 这两个 origin 的 MCP 端点在 HTTP 层已被 get_current_admin_strict
# 强制 admin(Batch 1)。**不含 console_assistant** —— 否则任意非 admin 用户的侧栏助手 LLM 即可
# 操作全局 MCP catalog(原 _ADMIN 含 console_assistant 是越权面)。见 _MCP_ADMIN。
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


def _user_can_read_script(db, sid: int, user_id: int) -> bool:
    """剧本读权限:owner 或订阅者。镜像 tavern_bind_script 的校验 —— 防 LLM 用任意
    script_id 跨用户读取他人私有剧本的章节/世界书/NPC 内容(script 级工具的 sid 可由
    args 注入,env.script_id 在酒馆/无剧本会话里为 None)。"""
    return db.execute(
        "select 1 from scripts s where s.id = %s and ("
        "  s.owner_id = %s or s.id in (select script_id from user_script_subscriptions where user_id = %s))",
        (int(sid), user_id, user_id),
    ).fetchone() is not None


def _t_get_chapter_facts(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = script_id or args.get("script_id")
    chapter_index = args.get("chapter_index")
    if not sid:
        return "失败: script_id 必填"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _user_can_read_script(db, int(sid), user_id):
                return f"失败 (权限): 剧本 #{int(sid)} 不属于当前用户或未订阅"
            if chapter_index is None:
                rows = db.execute(
                    "select chapter, title, summary from chapter_facts "
                    "where script_id = %s order by chapter limit 200",
                    (int(sid),),
                ).fetchall() or []
            else:
                rows = db.execute(
                    "select chapter, title, summary from chapter_facts "
                    "where script_id = %s and chapter = %s",
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
            if not _user_can_read_script(db, int(sid), user_id):
                return f"失败 (权限): 剧本 #{int(sid)} 不属于当前用户或未订阅"
            # 真实表名 worldbook_entries(不是 script_worldbook,旧 SQL 用了过期表名)
            # 字段:title / content(不是 key)
            if query:
                rows = db.execute(
                    "select id, title as key, content from worldbook_entries "
                    "where script_id = %s and enabled = true "
                    "and (title ilike %s or content ilike %s) "
                    "order by priority desc, title limit 30",
                    (int(sid), f"%{query}%", f"%{query}%"),
                ).fetchall() or []
            else:
                rows = db.execute(
                    "select id, title as key, content from worldbook_entries "
                    "where script_id = %s and enabled = true "
                    "order by priority desc, title limit 30",
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
                "(select count(*) from character_cards where user_id = %s and card_type = 'persona') as persona_count, "
                "(select count(*) from character_cards where user_id = %s and card_type = 'pc') as card_count",
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
        ("mcp_server_enable", "切换 MCP server 启用状态 (admin)",
         {"type": "object",
          "properties": {"server_id": {"type": "string"}, "enabled": {"type": "boolean"}},
          "required": ["server_id", "enabled"]},
         _t_mcp_server_enable, _MCP_ADMIN, False),  # SEC(M-1): 不含 console_assistant
        ("mcp_server_start", "启动指定 MCP server",
         {"type": "object", "properties": {"server_id": {"type": "string"}}, "required": []},
         _t_mcp_server_start, _MCP_ADMIN, False),   # SEC(M-1): 不含 console_assistant
        ("mcp_server_stop", "停止指定 MCP server",
         {"type": "object", "properties": {"server_id": {"type": "string"}}, "required": ["server_id"]},
         _t_mcp_server_stop, _MCP_ADMIN, False),    # SEC(M-1): 不含 console_assistant
        ("select_model", "切换当前 GM 使用的模型 (api_id + model_real_name)",
         {"type": "object",
          "properties": {"api_id": {"type": "string"}, "model": {"type": "string"}},
          "required": []},  # handler 自行校验并返回"不能为空"友好消息
         _t_select_model, _USER_MUTATE, False),  # LLM 改自己的模型?坚决禁
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

    # cowork(写作搭档):向作者「展示」结构化信息的两把无副作用工具 —— 数据在 args 里,前端按
    # tool 名特殊渲染成右栏面板(计划清单 / 问题清单),executor 只回执让 agent 继续。informational,
    # 不写库、不改状态;agent 不调也无害(只是不显示面板),不违反确定性铁律。
    def _t_set_writing_plan(user_id: int, args: dict) -> str:
        raw = args.get("steps")
        steps = [str(s).strip() for s in raw if str(s).strip()] if isinstance(raw, list) else []
        if not steps:
            return "失败: steps 为空(给一个分步计划字符串数组)"
        return f"已在右栏向作者展示写作计划({len(steps)} 步);按计划逐步执行即可。"

    plan_spec = ToolSpec(
        name="set_writing_plan",
        description=(
            "把你对当前写作/改稿任务的分步计划展示给作者(右栏渲染成清单)。遇到多步任务"
            "(重写一章并同步设定 / 梳理一条线的伏笔等)开工前调一次,让作者看清你打算怎么做。"
            "steps 是有序分步字符串数组,每步一句话。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "可选,计划标题"},
                "steps": {"type": "array", "items": {"type": "string"},
                          "description": "有序分步,每步一句话", "minItems": 1},
            },
            "required": ["steps"],
        },
        executor=_t_set_writing_plan,
        scope="user",
        origins=_CHOICE_ORIGINS,
        destructive=False,
    )
    if not registry.has(plan_spec.name):
        registry.register(plan_spec)

    def _t_report_writing_issues(user_id: int, args: dict) -> str:
        issues = args.get("issues")
        if not isinstance(issues, list) or not issues:
            return "失败: issues 为空(给一个问题对象数组)"
        return f"已在右栏向作者列出 {len(issues)} 条问题供逐条处理。"

    issues_spec = ToolSpec(
        name="report_writing_issues",
        description=(
            "把你审稿/通读发现的问题结构化列给作者(右栏渲染成可逐条处理的清单,带章号可跳转)。"
            "审稿、查矛盾/重复/连贯/伏笔回收后调它汇总,而不是只在回复里散文罗列。每条 issue:"
            "chapter(可空,章号)/ severity(可空 高/中/低)/ type(类别)/ detail(具体描述与建议)。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "可选,一句话总评"},
                "issues": {
                    "type": "array", "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "chapter": {"type": "integer", "description": "相关章号(可空)"},
                            "severity": {"type": "string", "description": "高/中/低(可空)"},
                            "type": {"type": "string", "description": "问题类别:矛盾/重复/连贯/伏笔/节奏/POV 等"},
                            "detail": {"type": "string", "description": "具体描述 + 修改建议"},
                        },
                        "required": ["detail"],
                    },
                },
            },
            "required": ["issues"],
        },
        executor=_t_report_writing_issues,
        scope="user",
        origins=_CHOICE_ORIGINS,
        destructive=False,
    )
    if not registry.has(issues_spec.name):
        registry.register(issues_spec)

    # task 96: ui_invoke 已删除。LLM 直接调具体工具 (create_character_card etc.)
    # 通过 native tool_use,缺 required 字段时 dispatcher 返普通错误,
    # LLM 读错误自己调 ask_user_choice。与 Anthropic Tool Search Tool 模式一致。


__all__ = ["register_misc_tools"]
