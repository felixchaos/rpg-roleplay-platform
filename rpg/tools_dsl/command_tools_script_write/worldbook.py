"""command_tools_script_write §世界书族(拆包 2026-07-14,纯机械搬家零行为变化)。

世界书条目:list(读级闸)+ 单条/批量 upsert + delete + 缓存失效。
_wb_upsert_one 是单条 create/update 核心(单条工具与批量工具共用同一代码路径)。
jsonb 绑定:keys/regex_keys/character_filter/scene_filter = Jsonb([...])。
"""
from __future__ import annotations

import json
from typing import Any

from ._helpers import _resolve_sid, _strlist, _user_can_read_script


def _t_list_worldbook_entries(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """紧凑列出剧本世界书条目(供 rule 4 定位现有 entry_id 去更新)。

    口径照搬 GET /api/scripts/{id}/worldbook(_db_select_worldbook_entries 的列子集);
    probability 是 numeric → ::float8 防 Decimal 不可 JSON 序列化。结果上限 300 防爆。
    """
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    try:
        from platform_app.db import connect, init_db
        init_db()
        with connect() as db:
            if not _user_can_read_script(db, sid, user_id):
                return f"失败 (权限): 剧本 #{sid} 不属于当前用户或未订阅"
            rows = db.execute(
                "select id as entry_id, title, keys, enabled, "
                "       insertion_position, priority, probability::float8 as probability "
                "from worldbook_entries where script_id = %s "
                "order by priority desc, id desc limit 300",
                (sid,),
            ).fetchall() or []
        if not rows:
            return f"(剧本 #{sid} 暂无世界书条目。要新建用 upsert_worldbook_entry 不传 entry_id。)"
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _wb_upsert_one(db: Any, sid: int, user_id: int, args: dict) -> dict:
    """单条世界书 create/update 核心:在已开连接 + 已过 owner 闸内执行,**不 commit**(由调用方提交)。
    返回 {ok, id, action:'created'|'updated', title, error}。供单条工具与批量工具共用同一代码路径。"""
    from psycopg.types.json import Jsonb

    from platform_app.api.script_edit import _write_commit
    entry_id = args.get("entry_id")
    title = args.get("title")
    if not entry_id and not (title and str(title).strip()):
        return {"ok": False, "id": None, "action": None, "title": "", "error": "创建世界书条目必须提供 title"}

    if entry_id:
        # ── 更新现有条目 ──
        try:
            eid = int(entry_id)
        except (TypeError, ValueError):
            return {"ok": False, "id": None, "action": None, "title": "", "error": "entry_id 必须是整数"}
        before = db.execute(
            "select id, title, content, priority, token_budget, sticky_turns, cooldown_turns, "
            "probability::float8 as probability, enabled, keys, regex_keys, character_filter, "
            "scene_filter, insertion_position from worldbook_entries where id = %s and script_id = %s",
            (eid, sid),
        ).fetchone()
        if not before:
            return {"ok": False, "id": eid, "action": None, "title": "", "error": f"条目 #{eid} 不存在或不属于剧本 #{sid}"}
        # 撤销快照:存改前全字段,供作者一键撤回 AI 对世界书的改动(与章节撤销同款安全网)。
        _wb_before = {k: before[k] for k in (
            "title", "content", "priority", "token_budget", "sticky_turns", "cooldown_turns",
            "probability", "enabled", "keys", "regex_keys", "character_filter", "scene_filter",
            "insertion_position")}
        sets, params = [], []
        for col in ("title", "content", "insertion_position"):
            if col in args and args[col] is not None:
                sets.append(f"{col}=%s")
                params.append(str(args[col]))
        for col in ("priority", "token_budget", "sticky_turns", "cooldown_turns"):
            if col in args and args[col] is not None:
                sets.append(f"{col}=%s")
                params.append(int(args[col]))
        if args.get("probability") is not None:
            sets.append("probability=%s")
            params.append(float(args["probability"]))
        if args.get("enabled") is not None:
            sets.append("enabled=%s")
            params.append(bool(args["enabled"]))
        for col in ("keys", "regex_keys", "character_filter", "scene_filter"):
            if col in args and isinstance(args[col], list):
                sets.append(f"{col}=%s")
                params.append(Jsonb(_strlist(args[col])))
        if not sets:
            return {"ok": False, "id": eid, "action": None, "title": before["title"], "error": "没有要更新的字段"}
        sets.append("updated_at=now()")
        params.extend([eid, sid])
        db.execute(
            f"update worldbook_entries set {', '.join(sets)} where id=%s and script_id=%s",
            tuple(params),
        )
        try:
            _write_commit(db, script_id=sid, user_id=user_id, kind="worldbook_edit",
                          message=f"编辑 worldbook #{eid}",
                          payload={"table": "worldbook_entries", "op": "edit", "ids": {"entry_id": eid},
                                   "before": _wb_before, "undoable": True})
        except Exception:
            pass
        return {"ok": True, "id": eid, "action": "updated", "title": (args.get("title") or before["title"]), "error": None}

    # ── 创建新条目 ──
    t = str(title).strip()
    # book_id 是遗留可空列(migration 85);归属看 script_id,没有 books 行就 NULL。
    book = db.execute("select id from books where script_id = %s", (sid,)).fetchone()
    book_id = int(book["id"]) if book else None
    new_row = db.execute(
        """
        insert into worldbook_entries
          (book_id, script_id, title, content, priority, enabled, metadata,
           keys, regex_keys, character_filter, scene_filter,
           token_budget, sticky_turns, cooldown_turns, probability, insertion_position)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (script_id, title) do nothing
        returning id
        """,
        (
            book_id, sid, t,
            str(args.get("content") or ""),
            int(args["priority"]) if args.get("priority") is not None else 50,
            bool(args["enabled"]) if args.get("enabled") is not None else True,
            Jsonb({"source": "editor"}),  # 标记编辑器写入,重建保留不删(harness 审计 P1)
            Jsonb(_strlist(args.get("keys"))),
            Jsonb(_strlist(args.get("regex_keys"))),
            Jsonb(_strlist(args.get("character_filter"))),
            Jsonb(_strlist(args.get("scene_filter"))),
            int(args["token_budget"]) if args.get("token_budget") is not None else 600,
            int(args["sticky_turns"]) if args.get("sticky_turns") is not None else 0,
            int(args["cooldown_turns"]) if args.get("cooldown_turns") is not None else 0,
            float(args["probability"]) if args.get("probability") is not None else 100.0,
            str(args.get("insertion_position") or "worldbook"),
        ),
    ).fetchone()
    if not new_row:
        # title 已存在(unique(script_id,title) 冲突 → do nothing)→ 幂等不重复建。
        return {"ok": False, "id": None, "action": None, "title": t,
                "error": f"剧本 #{sid} 已有同名条目「{t}」(要改它请带 entry_id)"}
    new_id = int(new_row["id"])
    try:
        _write_commit(db, script_id=sid, user_id=user_id, kind="worldbook_add",
                      message=f"新增 worldbook: {t}",
                      payload={"table": "worldbook_entries", "op": "add", "ids": {"entry_id": new_id}})
    except Exception:
        pass
    return {"ok": True, "id": new_id, "action": "created", "title": t, "error": None}


def _t_upsert_worldbook_entry(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    try:
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            if not script_owned(db, sid, user_id):  # ② 严格 owner 闸
                return "失败(权限): 剧本不属于当前用户"
            r = _wb_upsert_one(db, sid, user_id, args)
            db.commit()
        _invalidate_worldbook_cache(sid)
        if not r["ok"]:
            extra = "。要改它请带 entry_id(先用 list_worldbook_entries 拿 entry_id),不要重复新建。" if "同名" in (r.get("error") or "") else ""
            return f"失败: {r['error']}{extra}"
        return f"已{'更新' if r['action'] == 'updated' else '创建'}世界书条目 #{r['id']}(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_upsert_worldbook_entries(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """批量创建/更新世界书条目 —— **一次工具调用、一次确认、一并落库**。
    根因:审查模式下 LLM 逐条调 upsert_worldbook_entry 时,只有第一条会被确认执行(确认流在首条 break),
    其余被静默丢弃但 LLM 误报已成功。改由本工具一次传 entries 数组,每条独立 savepoint(一条失败不连累其余),
    返回逐条真实结果,杜绝「只成功第一条却说全成功」。"""
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    entries = args.get("entries")
    if not isinstance(entries, list) or not entries:
        return ("失败: 没收到 entries(可能单次条数过多,整个工具调用超输出长度被截断了)。"
                "请每次只传 ≤6 条,把更多条目分成多次调用。每项是一条世界书条目对象(新建带 title、改带 entry_id)。")
    if len(entries) > 12:
        return "失败: 单次条数过多易被截断,请每次 ≤6 条、分多次调用"
    try:
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        results: list[dict] = []
        with connect() as db:
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            for i, e in enumerate(entries):
                if not isinstance(e, dict):
                    results.append({"ok": False, "title": f"#{i}", "error": "条目不是对象"})
                    continue
                try:
                    with db.transaction():   # 每条一个 savepoint:一条失败回滚自身,不连累其他
                        results.append(_wb_upsert_one(db, sid, user_id, e))
                except Exception as ex:
                    results.append({"ok": False, "title": str(e.get("title") or f"#{i}"), "error": f"{type(ex).__name__}: {ex}"})
            db.commit()
        _invalidate_worldbook_cache(sid)
        ok = [r for r in results if r.get("ok")]
        bad = [r for r in results if not r.get("ok")]
        # 全军覆没(0 成功)必须以失败惯例开头,否则 dispatcher 记 ok=True=报成功
        if results and not ok:
            lines = [f"批量世界书失败:成功 0/{len(results)} 条(剧本 #{sid})"]
        else:
            lines = [f"批量世界书:成功 {len(ok)}/{len(results)} 条(剧本 #{sid})"]
        for r in ok:
            lines.append(f"- {'更新' if r.get('action') == 'updated' else '创建'} #{r.get('id')} {r.get('title', '')}")
        for r in bad:
            lines.append(f"- 失败「{r.get('title', '')}」:{r.get('error')}")
        return "\n".join(lines)
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _invalidate_worldbook_cache(script_id: int) -> None:
    """worldbook 改动后清 constant 层缓存(照 script_edit 的做法)。"""
    try:
        from gm_serving.context_inject import invalidate_constant_cache
        invalidate_constant_cache(script_id)
    except Exception:
        pass


def _t_delete_worldbook_entry(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """删除一条世界书条目(entry_id 必填,先 list_worldbook_entries 拿 id)。不可逆,删前向用户确认。"""
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    try:
        eid = int(args.get("entry_id"))
    except (TypeError, ValueError):
        return "失败: entry_id 必填且为整数(先 list_worldbook_entries 拿 id)"
    try:
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            row = db.execute(
                "select title from worldbook_entries where id=%s and script_id=%s", (eid, sid),
            ).fetchone()
            if not row:
                return f"失败: 世界书条目 #{eid} 不存在或不属于剧本 #{sid}"
            db.execute("delete from worldbook_entries where id=%s and script_id=%s", (eid, sid))
            try:
                from platform_app.api.script_edit import _write_commit
                _write_commit(db, script_id=sid, user_id=user_id, kind="worldbook_delete",
                              message=f"删除世界书条目 #{eid}「{row['title']}」",
                              payload={"table": "worldbook_entries", "op": "delete",
                                       "ids": {"entry_id": eid}})
            except Exception:
                pass
            db.commit()
        return f"已删除世界书条目 #{eid}「{row['title']}」(剧本 #{sid})"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


