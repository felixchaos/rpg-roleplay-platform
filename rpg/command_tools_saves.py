"""
command_tools_saves.py — task 87 Phase 2.2: saves / branches user 级工具表。

把 /api/saves/* 和 /api/branches/* 系列改造成 LLM 可调工具:

  user 级 (scope="user"):
    list_my_saves          列出当前用户所有存档
    activate_save          激活某个存档 (切档,会 drain 当前队列)
    rename_save            重命名存档
    delete_save            **destructive** 删档,仅 ui_button
    list_branches          列出某存档的所有分支
    activate_branch        激活分支
    delete_branch          **destructive** 删分支,仅 ui_button
    continue_branch        从某 turn 创建新分支

注意:
  · 所有工具 executor 签名 (user_id, args) — dispatcher 通过 scope="user"
    自动注入 user_id,不需要 GameState。
  · DB 操作走 platform_app.db / platform_app.branches。
  · destructive 操作只允许 ui_button + api_direct,不允许 llm_chat / llm_set。
"""
from __future__ import annotations

from typing import Any

from command_dispatcher import ToolSpec, get_registry


# task 87 Phase 7 安全审查:跨"世界泡"隔离
# task 48 新增 console_assistant:控制台助手是「用户带方向盘的 agent」,
# 它的工具调用语义上等同于「用户在 UI 上点了相应按钮」(read 自由,mutate 直接执行,
# destructive 由 endpoint 层做二次确认)。
# user 级 read 工具:列存档/列分支/查存档详情等 → 任意 origin (含 LLM 与 console_assistant)
_USER_ORIGINS_READ = frozenset({
    "ui_button", "api_direct", "llm_set", "llm_chat", "console_assistant",
})
# user 级 mutate 工具:激活/改名/切分支等会**影响后续 chat 路由的另一个 save** →
# LLM 任何 origin 都不允许 (即使玩家 /set 也不允许跨 save 操作)。
# console_assistant 允许 (它就是用来帮用户管 save 的)。
_USER_ORIGINS_MUTATE = frozenset({"ui_button", "api_direct", "console_assistant"})
# Destructive 同上,即使删自己当前 save 也是破坏性。console_assistant 允许,
# 但 /api/console_assistant/chat 在调度前会先 yield confirmation_required 等用户确认。
_USER_ORIGINS_DESTRUCTIVE = frozenset({"ui_button", "api_direct", "console_assistant"})


def _t_create_save(user_id: int, args: dict) -> str:
    """task 48: 基于 script_id 创建一个新存档。

    复用 platform_app.workspace.create_save (与 POST /api/saves 同源)。
    args:
      script_id    : 必填,基于哪个剧本建档
      title        : 可选,存档标题(空字符串则 workspace 自动给 "新存档")
      script_card_id : 可选,选用该剧本里的某张角色卡 (映射 character_kind="script_card")
      persona_id   : 可选,选用该用户某个 persona (映射 character_kind="persona")
    返回字符串 "save 创建: id=X title='...' script=Y"。
    """
    script_id = args.get("script_id")
    if not isinstance(script_id, (int, float, str)) or not str(script_id).lstrip("-").isdigit():
        return "失败: script_id 必填且必须是整数"
    title = (args.get("title") or "").strip()
    character: dict[str, Any] | None = None
    if args.get("script_card_id") is not None:
        character = {"kind": "script_card", "id": args.get("script_card_id")}
    elif args.get("persona_id") is not None:
        character = {"kind": "persona", "id": args.get("persona_id")}
    elif args.get("user_card_id") is not None:
        character = {"kind": "user_card", "id": args.get("user_card_id")}
    try:
        from platform_app import workspace as _ws
        save = _ws.create_save(
            user_id=int(user_id),
            script_id=int(script_id),
            title=title,
            new_card=None,
            character=character,
        )
        # 失效缓存,UI 切档时能拿到新 save
        try:
            import app as _ui
            _ui._invalidate_user_cache({"id": int(user_id)})
        except Exception:
            pass
        sid = (save or {}).get("id") or "?"
        stitle = (save or {}).get("title") or title or "新存档"
        # task 112: 工具结果带强 hint, 让 LLM 不要在用户说"最新/刚才创建的"时
        # 还问选择 — 答案就是这个 sid。
        return (
            f"save 创建: id={sid} title={stitle!r} script={script_id}. "
            f"提示: 这是用户当前会话里最新创建的存档, 用户说"
            f"'最新的'/'刚才创建的'/'上面这个'都指 id={sid}, 不要再让用户选。"
        )
    except ValueError as exc:
        return f"失败 (权限): {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_list_my_saves(user_id: int, args: dict) -> str:
    script_id = args.get("script_id")
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if script_id:
                rows = db.execute(
                    "select id, title, script_id, updated_at, created_at "
                    "from game_saves where user_id = %s and script_id = %s "
                    "order by updated_at desc limit 50",
                    (user_id, int(script_id)),
                ).fetchall()
            else:
                rows = db.execute(
                    "select id, title, script_id, updated_at, created_at "
                    "from game_saves where user_id = %s "
                    "order by updated_at desc limit 50",
                    (user_id,),
                ).fetchall()
        if not rows:
            return "(无存档)"
        # task 112: 排序明示 + 时间 + "最新"标记, 让 LLM 不需再问"哪个最新"
        lines = [
            f"共 {len(rows)} 个存档 (按 updated_at desc 倒序排, **第 1 个就是最新的**):"
        ]
        for i, r in enumerate(rows[:20]):
            ts = r.get("updated_at") or r.get("created_at")
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else (str(ts) if ts else "")
            tag = " **[最新]**" if i == 0 else ""
            lines.append(
                f"  · id={r['id']} title={r.get('title') or '(无标题)'} "
                f"script={r.get('script_id')} updated_at={ts_str}{tag}"
            )
        if len(rows) > 20:
            lines.append(f"  ...(还有 {len(rows) - 20} 个)")
        lines.append(
            "提示: 用户说'最新的'/'刚才创建的'/'上面那个' → 用第 1 行的 id, 不要让用户选。"
        )
        return "\n".join(lines)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_activate_save(user_id: int, args: dict) -> str:
    save_id = args.get("save_id")
    if not isinstance(save_id, (int, float, str)) or not str(save_id).lstrip("-").isdigit():
        return "失败: save_id 必须是整数"
    try:
        from platform_app import branches as _branches
        result = _branches.activate_save(int(user_id), int(save_id))
        # 同步清 app.py 的 user state cache,跨模块耦合
        try:
            import app as _ui
            _ui._invalidate_user_cache({"id": int(user_id)})
        except Exception:
            pass
        # task 110: 激活成功后, 在工具返回里强提示 LLM 必须接着 navigate_to_setting
        # 跳到 game_console (否则用户停在 Platform 看不到剧本)。
        return (
            f"激活存档 {save_id} ✓ (active_commit={result.get('active_commit_id', '?')}). "
            f"下一步: 如果用户想'进入游戏/开始玩', 必须调 "
            f"navigate_to_setting(target='game_console', reason='进入游戏') 跳转。"
        )
    except ValueError as exc:
        return f"失败 (权限): {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_rename_save(user_id: int, args: dict) -> str:
    save_id = args.get("save_id")
    title = (args.get("title") or "").strip()
    if not isinstance(save_id, (int, float, str)) or not str(save_id).lstrip("-").isdigit():
        return "失败: save_id 必须是整数"
    if not title:
        return "失败: title 不能为空"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            owned = db.execute(
                "select 1 from game_saves where id = %s and user_id = %s",
                (int(save_id), user_id),
            ).fetchone()
            if not owned:
                return "失败 (权限): 该存档不属于当前用户"
            db.execute(
                "update game_saves set title = %s, updated_at = now() where id = %s",
                (title, int(save_id)),
            )
        return f"重命名存档 {save_id} → {title!r}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_delete_save(user_id: int, args: dict) -> str:
    save_id = args.get("save_id")
    if not isinstance(save_id, (int, float, str)) or not str(save_id).lstrip("-").isdigit():
        return "失败: save_id 必须是整数"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            owned = db.execute(
                "select 1 from game_saves where id = %s and user_id = %s",
                (int(save_id), user_id),
            ).fetchone()
            if not owned:
                return "失败 (权限): 该存档不属于当前用户"
            db.execute(
                "delete from game_saves where id = %s and user_id = %s",
                (int(save_id), user_id),
            )
        # 失效 user state cache
        try:
            import app as _ui
            _ui._invalidate_user_cache({"id": int(user_id)})
        except Exception:
            pass
        return f"删除存档 {save_id} ✓"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_list_branches(user_id: int, args: dict) -> str:
    save_id = args.get("save_id")
    if not isinstance(save_id, (int, float, str)) or not str(save_id).lstrip("-").isdigit():
        return "失败: save_id 必须是整数"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            owned = db.execute(
                "select 1 from game_saves where id = %s and user_id = %s",
                (int(save_id), user_id),
            ).fetchone()
            if not owned:
                return "失败 (权限): 该存档不属于当前用户"
            rows = db.execute(
                "select id, label, turn, created_at from game_branches "
                "where save_id = %s order by created_at desc limit 50",
                (int(save_id),),
            ).fetchall() or []
        if not rows:
            return f"存档 {save_id} 暂无分支"
        lines = [f"存档 {save_id} 的 {len(rows)} 个分支:"]
        for r in rows[:20]:
            lines.append(
                f"  · branch_id={r['id']} label={r.get('label') or '(无标签)'} "
                f"turn={r.get('turn')}"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_activate_branch(user_id: int, args: dict) -> str:
    branch_id = args.get("branch_id")
    if not isinstance(branch_id, (int, float, str)) or not str(branch_id).lstrip("-").isdigit():
        return "失败: branch_id 必须是整数"
    try:
        from platform_app import branches as _branches
        # branches.activate_branch 期望 (user_id, branch_id) 但有的版本要 dict
        # 这里通过 DB 自校验所有权
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select b.save_id from game_branches b "
                "join game_saves s on b.save_id = s.id "
                "where b.id = %s and s.user_id = %s",
                (int(branch_id), user_id),
            ).fetchone()
            if not row:
                return "失败 (权限): 该分支不属于当前用户"
        if hasattr(_branches, "activate_branch"):
            result = _branches.activate_branch(user_id, int(branch_id))
            return f"激活分支 {branch_id} ✓ (返回 {result})"
        return f"激活分支 {branch_id} (核心 API 未提供细节)"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_delete_branch(user_id: int, args: dict) -> str:
    branch_id = args.get("branch_id")
    if not isinstance(branch_id, (int, float, str)) or not str(branch_id).lstrip("-").isdigit():
        return "失败: branch_id 必须是整数"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            row = db.execute(
                "select b.id from game_branches b "
                "join game_saves s on b.save_id = s.id "
                "where b.id = %s and s.user_id = %s",
                (int(branch_id), user_id),
            ).fetchone()
            if not row:
                return "失败 (权限): 该分支不属于当前用户"
            db.execute("delete from game_branches where id = %s", (int(branch_id),))
        return f"删除分支 {branch_id} ✓"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_continue_branch(user_id: int, args: dict) -> str:
    save_id = args.get("save_id")
    from_turn = args.get("from_turn")
    label = (args.get("label") or "").strip() or None
    if not isinstance(save_id, (int, float, str)) or not str(save_id).lstrip("-").isdigit():
        return "失败: save_id 必须是整数"
    if not isinstance(from_turn, (int, float, str)) or not str(from_turn).lstrip("-").isdigit():
        return "失败: from_turn 必须是整数"
    try:
        from platform_app import branches as _branches
        if hasattr(_branches, "continue_branch"):
            result = _branches.continue_branch(
                user_id, int(save_id), int(from_turn), label=label,
            )
            new_id = result.get("branch_id") if isinstance(result, dict) else result
            return f"创建分支 from save={save_id} turn={from_turn} → branch_id={new_id}"
        return "失败: branches.continue_branch 未实现"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def register_saves_tools() -> None:
    registry = get_registry()
    specs: list[ToolSpec] = [
        ToolSpec(
            name="create_save",
            description=(
                "基于 script_id 创建一个新存档。等价于 UI 的「新建存档」。"
                "\n\n**角色卡 id 三选一 (重要,不要混淆):**"
                "\n  · `user_card_id`: 用户自创跨剧本通用角色卡 (来自 list_my_character_cards),"
                "比如玩家自己捏的「杭雁菱」「晓卡」。**推荐优先用这个**, 因为它跨 script 共享。"
                "\n  · `persona_id`: 用户的玩家 persona (来自 list_my_personas)。"
                "\n  · `script_card_id`: 剧本内 NPC 卡 (剧本作者预设的, e.g. 伊奈帆/娅赛兰)。"
                "玩家把某 NPC 当主角时才用。**不是**用户自创的角色卡。"
                "\n\n如果不确定, **先调 list_my_character_cards 看 user_card_id 列表**, 然后用 user_card_id。"
                "如果 3 个都不传, 系统会自动用用户的默认 persona 兜底。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "script_id": {"type": "integer"},
                    "title": {"type": "string"},
                    "user_card_id": {
                        "type": "integer",
                        "description": "用户自创角色卡 id (推荐, 来自 list_my_character_cards)",
                    },
                    "persona_id": {
                        "type": "integer",
                        "description": "用户的玩家 persona id (来自 list_my_personas)",
                    },
                    "script_card_id": {
                        "type": "integer",
                        "description": "剧本内 NPC 卡 id (剧本作者预设的, 不是用户自创的)",
                    },
                },
                "required": ["script_id"],
            },
            executor=_t_create_save,
            scope="user",
            # task 48: console_assistant 可调,UI 与 api_direct 也可调。
            # LLM chat / llm_set 不可调:即使玩家在 chat 里 /set,也不该跨 save 操作。
            origins=frozenset({"ui_button", "api_direct", "console_assistant"}),
            destructive=False,
        ),
        ToolSpec(
            name="list_my_saves",
            description="列出当前用户的存档 (可选按 script_id 过滤)。",
            input_schema={
                "type": "object",
                "properties": {
                    "script_id": {"type": "integer",
                                  "description": "可选,只列某剧本的存档"},
                },
                "required": [],
            },
            executor=_t_list_my_saves,
            scope="user",
            origins=_USER_ORIGINS_READ,
        ),
        ToolSpec(
            name="activate_save",
            description=(
                "把指定存档设为当前激活档。所有后续 chat 都基于此 save。"
                "切档前会等待当前 save 的工具队列 drain。"
            ),
            input_schema={
                "type": "object",
                "properties": {"save_id": {"type": "integer"}},
                "required": ["save_id"],
            },
            executor=_t_activate_save,
            scope="user",
            origins=_USER_ORIGINS_MUTATE,  # task 87 Phase 7: LLM 禁
        ),
        ToolSpec(
            name="rename_save",
            description="给存档改标题。",
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer"},
                    "title": {"type": "string"},
                },
                "required": ["save_id", "title"],
            },
            executor=_t_rename_save,
            scope="user",
            origins=_USER_ORIGINS_MUTATE,  # task 87 Phase 7
        ),
        ToolSpec(
            name="delete_save",
            description="**永久删除**存档及其所有分支/上下文链。不可恢复。",
            input_schema={
                "type": "object",
                "properties": {"save_id": {"type": "integer"}},
                "required": ["save_id"],
            },
            executor=_t_delete_save,
            scope="user",
            origins=_USER_ORIGINS_DESTRUCTIVE,
            destructive=True,
        ),
        ToolSpec(
            name="list_branches",
            description="列出某存档的所有分支。",
            input_schema={
                "type": "object",
                "properties": {"save_id": {"type": "integer"}},
                "required": ["save_id"],
            },
            executor=_t_list_branches,
            scope="user",
            origins=_USER_ORIGINS_READ,
        ),
        ToolSpec(
            name="activate_branch",
            description="把指定分支切为当前活动分支。",
            input_schema={
                "type": "object",
                "properties": {"branch_id": {"type": "integer"}},
                "required": ["branch_id"],
            },
            executor=_t_activate_branch,
            scope="user",
            origins=_USER_ORIGINS_MUTATE,  # task 87 Phase 7
        ),
        ToolSpec(
            name="delete_branch",
            description="**永久删除**指定分支。不可恢复。",
            input_schema={
                "type": "object",
                "properties": {"branch_id": {"type": "integer"}},
                "required": ["branch_id"],
            },
            executor=_t_delete_branch,
            scope="user",
            origins=_USER_ORIGINS_DESTRUCTIVE,
            destructive=True,
        ),
        ToolSpec(
            name="continue_branch",
            description="从某个存档的指定 turn 创建新分支,沿用前文 history 直到该 turn。",
            input_schema={
                "type": "object",
                "properties": {
                    "save_id": {"type": "integer"},
                    "from_turn": {"type": "integer", "minimum": 0},
                    "label": {"type": "string"},
                },
                "required": ["save_id", "from_turn"],
            },
            executor=_t_continue_branch,
            scope="user",
            origins=_USER_ORIGINS_MUTATE,  # task 87 Phase 7
        ),
    ]
    for spec in specs:
        if not registry.has(spec.name):
            registry.register(spec)


__all__ = ["register_saves_tools"]
