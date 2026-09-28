"""command_tools_script_write §canon 实体族(拆包 2026-07-14,纯机械搬家零行为变化)。

canon 实体:list(读级闸)+ 按 logical_key upsert。
aliases = jsonb 字符串数组、attrs = jsonb 开放对象;编辑写入标 source='editor'(重建保留)。
类型白名单 / 新建时 logical_key 的生成与校验规则 / 同名同类型查重 / 改文本置空向量 /
别名缓存作废,与 REST(/canon-entities)共用 kb.canon_repo、kb.alias 的同一套。
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
            return f"(剧本 #{sid} 暂无 canon 实体。要新建用 upsert_canon_entity 给 name+type,logical_key 可省略自动生成。)"
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_upsert_canon_entity(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    logical_key = (args.get("logical_key") or "")
    logical_key = str(logical_key).strip()
    from kb.alias import invalidate_alias_cache
    from kb.canon_repo import (
        allocate_canon_logical_key,
        canon_embedding_reset_sql,
        canon_logical_key_problem,
        canon_type_choices_text,
        find_same_name_canon,
        normalize_canon_type,
    )
    key_problem = canon_logical_key_problem(logical_key)
    if key_problem:
        return f"失败: {key_problem}"
    # type:给了就必须认得出(与 REST 同一白名单,中文/近义写法归一);认不出直接失败,不落脏类型。
    entity_type = None
    if args.get("type") is not None and str(args.get("type")).strip():
        entity_type = normalize_canon_type(args.get("type"))
        if not entity_type:
            return f"失败: type「{args.get('type')}」不认识,可选 {canon_type_choices_text()}"
    if "name" in args and args["name"] is not None and not str(args["name"]).strip():
        return "失败: name 不能为空"
    if not logical_key and not (str(args.get("name") or "").strip() and entity_type):
        return ("失败: 改已有实体要给 logical_key(先 list_canon_entities 查);"
                "新建可以不给 logical_key,但必须给 name 和 type")
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
            existing = None
            if logical_key:
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
                        if col == "type" and entity_type is None:
                            continue  # 空串 type = 没打算改类型
                        sets.append(f"{col}=%s")
                        params.append(entity_type if col == "type" else str(args[col]))
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
                # 名字 / 别名 / 摘要真改了 → 向量置空等重嵌(与 REST PUT 同口径)。
                text_values = {}
                if args.get("name") is not None:
                    text_values["name"] = str(args["name"])
                if args.get("summary") is not None:
                    text_values["summary"] = str(args["summary"])
                if "aliases" in args and isinstance(args["aliases"], list):
                    text_values["aliases"] = _strlist(args["aliases"])
                reset = canon_embedding_reset_sql(text_values)
                if reset:
                    sets.append(reset[0])
                    params.extend(reset[1])
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
                invalidate_alias_cache(sid)
                return f"已更新 canon 实体「{logical_key}」(剧本 #{sid})"
            else:
                # ── 创建 ── name/type 是 NOT NULL,创建时必须给。
                name = str(args.get("name") or "").strip()
                if not name or not entity_type:
                    return "失败: 创建 canon 实体必须提供 name 和 type"
                dup = find_same_name_canon(db, sid, name, entity_type)
                if dup:
                    return (f"失败: 剧本 #{sid} 已有同名同类型实体「{dup['name']}」"
                            f"(logical_key={dup['logical_key']});要改它请带这个 logical_key 更新,不要重复新建")
                aliases = args.get("aliases")
                attrs = args.get("attrs")
                explicit_key = bool(logical_key)
                new_row = None
                # 没给 logical_key → 与 REST 同规则自动编号;并发撞号就重选(最多 5 次)。
                for _ in range(1 if explicit_key else 5):
                    if not explicit_key:
                        logical_key = allocate_canon_logical_key(db, sid, name, entity_type)
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
                    if new_row:
                        break
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
                invalidate_alias_cache(sid)
                return f"已创建 canon 实体「{logical_key}」(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


