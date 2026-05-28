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

from tools_dsl.command_dispatcher import ToolSpec, get_registry


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


def _t_list_my_usage(user_id: int, args: dict) -> str:
    """task 119: 查当前用户的 token 用量/成本/请求数(按天/周/月窗口)。

    page=Usage 时用户说"统计/给我看/汇总一下用量"应直接调本工具,而不是 ui_set_field。
    """
    days = int(args.get("days") or 30)
    if days <= 0 or days > 365:
        days = 30
    try:
        from platform_app import usage as _usage
        data = _usage.aggregate_usage(int(user_id), days=days)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"
    if not data:
        return f"过去 {days} 天没有任何用量记录。"
    requests = int(data.get("requests", 0) or 0)
    in_tk = int(data.get("input_tokens", 0) or 0)
    out_tk = int(data.get("output_tokens", 0) or 0)
    cost = float(data.get("cost_usd", 0) or 0)
    avg_lat = data.get("avg_latency_ms")
    err_rate = data.get("error_rate")
    by_api = data.get("by_api") or []
    by_model = data.get("by_model") or []
    daily_avg = (requests / max(days, 1))
    lines = [
        f"过去 {days} 天用量汇总:",
        f"- 请求数: {requests} (日均 {daily_avg:.1f})",
        f"- Token: 输入 {in_tk:,} / 输出 {out_tk:,} (比 {in_tk / max(out_tk, 1):.1f}:1)",
        f"- 成本: ${cost:.4f}",
    ]
    if avg_lat is not None:
        lines.append(f"- 平均延迟: {avg_lat} ms")
    if err_rate is not None:
        lines.append(f"- 错误率: {err_rate}%")
    if by_api:
        lines.append("按 API:")
        for r in by_api[:5]:
            lines.append(f"  · {r.get('api','-')}: {r.get('requests',0)} 请求 · ${float(r.get('cost_usd', 0) or 0):.4f}")
    if by_model:
        lines.append("按模型 (TOP 5):")
        for r in by_model[:5]:
            lines.append(f"  · {r.get('model','-')} (via {r.get('api','-')}): {r.get('requests',0)} 请求 · ${float(r.get('cost_usd', 0) or 0):.4f}")
    return "\n".join(lines)


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
            # task 120: 先查 save 详情, 包括 turn (有进度的不该让 LLM 轻易删)
            owned = db.execute(
                "select id, title, "
                "  (state_snapshot->>'turn')::int as turn, "
                "  coalesce(jsonb_array_length(state_snapshot->'history'), 0) as msg_count "
                "from game_saves where id = %s and user_id = %s",
                (int(save_id), user_id),
            ).fetchone()
            if not owned:
                return f"失败 (权限/不存在): save_id={save_id} 不属于当前用户或已不存在。**禁止编造 '已删除'**: 这个 ID 根本没操作过。"
            turn = int(owned.get("turn") or 0)
            title = str(owned.get("title") or "")
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
        # task 120: 结果带 turn 信息, LLM 续叙时也只能引用这一次返回的具体 save_id, 不能 generalize
        warn = ""
        if turn >= 5:
            warn = f" ⚠️ 该存档已玩 {turn} 回合, 有重要进度被永久删除!"
        return f"已删除 save_id={save_id} ({title!r}, turn={turn}) ✓{warn}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_delete_saves(user_id: int, args: dict) -> str:
    """task 120: 批量删除 - 解决"删多个存档"场景下 LLM 只调 1 次然后编造其他被删的幻觉。
    LLM 必须给出完整 save_ids 列表(先 list_my_saves 拿真实 ID),
    backend 逐个验证 + 删除, 返回 JSON 列出 deleted/not_found/protected 三个桶,
    LLM 续叙时只能基于这个真实结果, 无法编造。
    """
    import json as _json
    save_ids = args.get("save_ids")
    if not isinstance(save_ids, list) or not save_ids:
        return "失败: save_ids 必须是非空整数数组"
    try:
        ids = [int(x) for x in save_ids]
    except (TypeError, ValueError):
        return "失败: save_ids 必须是整数列表"
    if len(ids) > 50:
        return "失败: 单次最多删 50 个 save (避免误操作)"

    deleted: list[dict] = []
    not_found: list[int] = []
    protected: list[dict] = []  # turn>=10 的高价值存档,需要单独 delete_save 确认
    errors: list[dict] = []

    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            for sid in ids:
                try:
                    row = db.execute(
                        "select id, title, "
                        "  (state_snapshot->>'turn')::int as turn, "
                        "  coalesce(jsonb_array_length(state_snapshot->'history'), 0) as msg_count "
                        "from game_saves where id = %s and user_id = %s",
                        (sid, user_id),
                    ).fetchone()
                    if not row:
                        not_found.append(sid)
                        continue
                    turn = int(row.get("turn") or 0)
                    title = str(row.get("title") or "")
                    # 保护高价值存档:turn >= 10 必须单独 delete_save 走 destructive 确认
                    if turn >= 10:
                        protected.append({"id": sid, "title": title, "turn": turn})
                        continue
                    db.execute(
                        "delete from game_saves where id = %s and user_id = %s",
                        (sid, user_id),
                    )
                    deleted.append({"id": sid, "title": title, "turn": turn})
                except Exception as exc:
                    errors.append({"id": sid, "error": f"{type(exc).__name__}: {exc}"})
        try:
            import app as _ui
            _ui._invalidate_user_cache({"id": int(user_id)})
        except Exception:
            pass
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"

    # 详细 JSON 结果, LLM 必须基于这个 narrate, 不能编
    result = {
        "requested": len(ids),
        "deleted_count": len(deleted),
        "deleted": deleted,
        "not_found": not_found,
        "protected": protected,
        "errors": errors,
    }
    summary_lines = [f"批量删除结果 (请求 {len(ids)} 个):"]
    if deleted:
        summary_lines.append(f"  ✓ 成功删除 {len(deleted)} 个: " + ", ".join(
            f"{d['id']}({d['title']!r},turn={d['turn']})" for d in deleted
        ))
    if not_found:
        summary_lines.append(f"  ✗ 不存在/无权 {len(not_found)} 个: {not_found}")
    if protected:
        prot_strs = [f"{p['id']}({p['title']!r},turn={p['turn']})" for p in protected]
        summary_lines.append(
            f"  ⚠️ 高价值存档需单独删除 (turn>=10, 自动跳过) {len(protected)} 个: " +
            ", ".join(prot_strs) + " — 用 delete_save 走二次确认逐个删"
        )
    if errors:
        summary_lines.append(f"  ❌ 错误 {len(errors)} 个: {errors}")
    summary_lines.append("--- raw JSON ---")
    summary_lines.append(_json.dumps(result, ensure_ascii=False))
    return "\n".join(summary_lines)


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
        # task 119: 助手在用量页 (Usage) 时,看到用户问"统计/汇总/给我看一下用量",
        # 必须直接调这个工具,而不是 ui_set_field 把字符串塞回自己的输入框。
        ToolSpec(
            name="list_my_usage",
            description=(
                "查询当前用户在过去 N 天的 token 用量/成本/请求数/错误率,"
                "按 API 与模型拆分。用户在 #usage 页问\"统计/汇总/看看用量/算算花了多少\" "
                "时直接用本工具,不要 ui_set_field。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer",
                             "description": "回溯天数 (默认 30, 范围 1-365)",
                             "default": 30},
                },
                "required": [],
            },
            executor=_t_list_my_usage,
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
            description="**永久删除**单个存档及其所有分支/上下文链。不可恢复。一次只删一个; 用户说删多个时改用 delete_saves。",
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
        # task 120: 批量删除 - 防止 LLM 只调 1 次 delete_save 然后编造其他被删的幻觉。
        # 用法:用户说"删除多个/批量/除了 X 以外的"时,先 list_my_saves 拿全部 ID,
        # 然后**一次性**调 delete_saves(save_ids=[...]),走 1 次 destructive 确认,
        # 工具返回真实成败列表;turn>=10 的高价值档自动跳过,需要单独 delete_save。
        ToolSpec(
            name="delete_saves",
            description=(
                "**批量永久删除**多个存档。一次调用搞定,走一次 destructive 确认。"
                "用户说'删除全部/除了 X 以外/这几个'时用本工具,不要循环调 delete_save。"
                "turn>=10 的存档会被自动跳过(防止误删长期进度),返回的 protected 列表需要后续单独 delete_save 确认。"
                "你必须先 list_my_saves 拿真实 ID 列表填进来,不要凭印象填。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "save_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "要删的 save_id 数组 (必须从 list_my_saves 真实拿到, 不能编)",
                        "minItems": 1,
                        "maxItems": 50,
                    },
                },
                "required": ["save_ids"],
            },
            input_examples=(
                {"save_ids": [10144, 13787, 13788, 13789]},
            ),
            executor=_t_delete_saves,
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
