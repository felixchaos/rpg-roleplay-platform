"""command_tools_script_write §canon 实体族(拆包 2026-07-14,纯机械搬家零行为变化)。

canon 实体:list(读级闸)+ 按 logical_key upsert。
aliases = jsonb 字符串数组、attrs = jsonb 开放对象;编辑写入标 source='editor'(重建保留)。
"""
from __future__ import annotations

import json
from typing import Any

from ._helpers import _resolve_sid, _strlist, _user_can_read_script


def _t_list_canon_entities(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """紧凑列出剧本 canon 实体(供 rule 4 按 logical_key 定位去 upsert)。

    口径照搬 GET /api/scripts/{id}/canon-entities(kb_canon_entities,_CANON_LIST_COLS 的列子集)。
    结果上限 300 防爆。
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
                "select logical_key, name, full_name, type, entity_subtype, importance "
                "from kb_canon_entities where script_id = %s "
                "order by importance desc, id desc limit 300",
                (sid,),
            ).fetchall() or []
        if not rows:
            return f"(剧本 #{sid} 暂无 canon 实体。要新建用 upsert_canon_entity 给 logical_key+name+type。)"
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_upsert_canon_entity(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    logical_key = (args.get("logical_key") or "")
    logical_key = str(logical_key).strip()
    if not logical_key:
        return "失败: logical_key 必填"
    try:
        from psycopg.types.json import Jsonb

        from platform_app.api.script_edit import _write_commit
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            # ② 严格 owner 闸
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            existing = db.execute(
                "select id from kb_canon_entities where script_id = %s and logical_key = %s",
                (sid, logical_key),
            ).fetchone()

            if existing:
                # ── 更新 ──
                sets, params = [], []
                for col in ("name", "full_name", "type", "summary", "identity",
                            "background", "entity_subtype", "parent_logical_key"):
                    if col in args and args[col] is not None:
                        sets.append(f"{col}=%s")
                        params.append(str(args[col]))
                if args.get("importance") is not None:
                    sets.append("importance=%s")
                    params.append(int(args["importance"]))
                if args.get("first_revealed_chapter") is not None:
                    sets.append("first_revealed_chapter=%s")
                    params.append(int(args["first_revealed_chapter"]))
                if args.get("public_knowledge") is not None:
                    sets.append("public_knowledge=%s")
                    params.append(bool(args["public_knowledge"]))
                # aliases = jsonb 字符串数组;attrs = jsonb 开放对象。
                if "aliases" in args and isinstance(args["aliases"], list):
                    sets.append("aliases=%s")
                    params.append(Jsonb(_strlist(args["aliases"])))
                if "attrs" in args and isinstance(args["attrs"], dict):
                    # 用户传了 attrs → jsonb 合并(保留既有键)+ 标 source='editor'。
                    sets.append("attrs = coalesce(attrs,'{}'::jsonb) || %s::jsonb")
                    params.append(Jsonb({**args["attrs"], "source": "editor"}))
                if not sets:
                    return "失败: 没有要更新的字段"
                # 有真实字段更新但没动 attrs → 仍标 source='editor',让重建保留这条用户编辑过的实体(harness 审计 P1)。
                if not any(s.startswith("attrs") for s in sets):
                    sets.append("attrs = coalesce(attrs,'{}'::jsonb) || '{\"source\":\"editor\"}'::jsonb")
                params.extend([sid, logical_key])
                db.execute(
                    f"update kb_canon_entities set {', '.join(sets)} "
                    f"where script_id=%s and logical_key=%s",
                    tuple(params),
                )
                try:
                    _write_commit(
                        db, script_id=sid, user_id=user_id, kind="canon_edit",
                        message=f"编辑 canon entity: {logical_key}",
                        payload={"table": "kb_canon_entities", "op": "edit",
                                 "ids": {"logical_key": logical_key}},
                    )
                except Exception:
                    pass
                db.commit()
                return f"已更新 canon 实体「{logical_key}」(剧本 #{sid})"
            else:
                # ── 创建 ── name/type 是 NOT NULL,创建时必须给。
                name = str(args.get("name") or "").strip()
                entity_type = str(args.get("type") or "").strip()
                if not name or not entity_type:
                    return "失败: 创建 canon 实体必须提供 name 和 type"
                aliases = args.get("aliases")
                attrs = args.get("attrs")
                new_row = db.execute(
                    """
                    insert into kb_canon_entities
                      (script_id, logical_key, name, full_name, type, summary, identity, background,
                       entity_subtype, parent_logical_key, importance,
                       aliases, attrs, first_revealed_chapter, public_knowledge)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (script_id, logical_key) do nothing
                    returning id
                    """,
                    (
                        sid, logical_key, name,
                        str(args.get("full_name") or ""),
                        entity_type,
                        str(args.get("summary") or ""),
                        str(args.get("identity") or ""),
                        str(args.get("background") or ""),
                        str(args.get("entity_subtype") or ""),
                        str(args.get("parent_logical_key") or ""),
                        int(args["importance"]) if args.get("importance") is not None else 0,
                        Jsonb(_strlist(aliases)) if isinstance(aliases, list) else Jsonb([]),
                        # 标 source='editor':重建保留不删(harness 审计 P1,attrs 是 canon 的开放 jsonb)
                        Jsonb({**(attrs if isinstance(attrs, dict) else {}), "source": "editor"}),
                        int(args["first_revealed_chapter"]) if args.get("first_revealed_chapter") is not None else 0,
                        bool(args["public_knowledge"]) if args.get("public_knowledge") is not None else False,
                    ),
                ).fetchone()
                if not new_row:
                    return f"失败: canon 实体「{logical_key}」已存在(并发创建?)"
                try:
                    _write_commit(
                        db, script_id=sid, user_id=user_id, kind="canon_add",
                        message=f"新增 canon entity: {logical_key}",
                        payload={"table": "kb_canon_entities", "op": "add",
                                 "ids": {"logical_key": logical_key}},
                    )
                except Exception:
                    pass
                db.commit()
                return f"已创建 canon 实体「{logical_key}」(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


