"""command_tools_script_write §时间线锚点族(拆包 2026-07-14,纯机械搬家零行为变化)。

锚点:list(读级闸)+ update + create + delete。
keywords 是 PostgreSQL 原生 text[]:参数直接绑 Python list,绝不 Jsonb/json.dumps。
create_anchor 写 source='editor'(时间线重建不会删它)。
"""
from __future__ import annotations

import json
from typing import Any

from ._helpers import _resolve_sid, _user_can_read_script


def _t_list_anchors(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """紧凑列出剧本时间线锚点(供 rule 4 定位现有 anchor_id 去更新)。

    口径照搬 GET /api/scripts/{id}/timeline(script_timeline_anchors)。
    注意:该表是「剧本只读骨架(原著时间线)」,本身没有 anchor_type/satisfied 列
    (kind/satisfied 是 save 级收束机制 kb_* 表的语义,不在 script 级),故不返回这两个字段。
    用 label(=story_time_label)+ story_phase + 章节区间 + 标题/摘要定位即够;
    keywords/confidence 一并返回,便于 update_anchor 增量改(否则改这两项只能盲写覆盖)。
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
                "select id as anchor_id, story_time_label as label, story_phase, "
                "       chapter_min, chapter_max, sample_title, sample_summary, "
                "       keywords, confidence "
                "from script_timeline_anchors where script_id = %s "
                "order by chapter_min asc, id asc limit 300",
                (sid,),
            ).fetchall() or []
        if not rows:
            return f"(剧本 #{sid} 暂无时间线锚点。)"
        return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2, default=str)
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_update_anchor(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    anchor_id = args.get("anchor_id")
    if not anchor_id:
        return "失败: anchor_id 必填"
    try:
        aid = int(anchor_id)
    except (TypeError, ValueError):
        return "失败: anchor_id 必须是整数"
    try:
        from platform_app.api.script_edit import _write_commit
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            # ② 严格 owner 闸
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            before = db.execute(
                "select id, story_time_label from script_timeline_anchors "
                "where id = %s and script_id = %s",
                (aid, sid),
            ).fetchone()
            if not before:
                return f"失败: 锚点 #{aid} 不存在或不属于剧本 #{sid}"
            sets, params = [], []
            # story_summary 在 script_timeline_anchors 里列名是 sample_summary;这里直接收 sample_summary。
            for col in ("story_phase", "story_time_label", "sample_title", "sample_summary"):
                if col in args and args[col] is not None:
                    sets.append(f"{col}=%s")
                    params.append(str(args[col]))
            for col in ("chapter_min", "chapter_max"):
                if col in args and args[col] is not None:
                    sets.append(f"{col}=%s")
                    params.append(int(args[col]))
            if args.get("confidence") is not None:
                sets.append("confidence=%s")
                params.append(float(args["confidence"]))
            if "keywords" in args and isinstance(args["keywords"], list):
                # keywords 是 PostgreSQL 原生 text[]:参数直接绑 Python list,
                # psycopg 按数组写回;绝不 Jsonb / json.dumps(那会写坏 text[] 列)。
                sets.append("keywords=%s")
                params.append([str(x) for x in args["keywords"]])
            if not sets:
                return "失败: 没有要更新的字段"
            sets.append("updated_at=now()")
            params.extend([aid, sid])
            db.execute(
                f"update script_timeline_anchors set {', '.join(sets)} "
                f"where id=%s and script_id=%s",
                tuple(params),
            )
            # ③ 审计
            try:
                _write_commit(
                    db, script_id=sid, user_id=user_id, kind="anchor_edit",
                    message=f"编辑 anchor #{aid}",
                    payload={"table": "script_timeline_anchors", "op": "edit",
                             "ids": {"anchor_id": aid}},
                )
            except Exception:
                pass
            db.commit()
        return f"已更新时间线锚点 #{aid}(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_create_anchor(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """新建时间线锚点 —— 编辑器续写出「全新事件/时间节点」时用。

    与 update_anchor(只改已有)互补:本工具 INSERT 一行 source='editor' 的锚点,
    **时间线重建不会删它**(原著骨架 source='novel' 才会被删后重建)。
    唯一键 (script_id, story_phase, story_time_label):撞了 → do nothing + 提示改用 update_anchor。
    必填 story_time_label + chapter_min + chapter_max(该事件大致章节);story_phase 默认空。
    """
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    label = str(args.get("story_time_label") or "").strip()
    if not label:
        return "失败: story_time_label 必填(新事件的时间/节点名)"
    if args.get("chapter_min") is None or args.get("chapter_max") is None:
        return "失败: chapter_min / chapter_max 必填(该事件大致所处章节)"
    try:
        cmin = int(args["chapter_min"]); cmax = int(args["chapter_max"])
    except (TypeError, ValueError):
        return "失败: chapter_min / chapter_max 必须是整数"
    if cmax < cmin:
        cmax = cmin
    phase = str(args.get("story_phase") or "")
    summary = str(args.get("sample_summary") or "")[:1900]
    title = str(args.get("sample_title") or "")[:200]
    try:
        confidence = float(args["confidence"]) if args.get("confidence") is not None else 0.7
    except (TypeError, ValueError):
        confidence = 0.7
    # keywords 是 PostgreSQL 原生 text[]:直接绑 Python list(绝不 Jsonb)。
    keywords = [str(x) for x in args["keywords"]] if isinstance(args.get("keywords"), list) else []
    try:
        from platform_app.api.script_edit import _write_commit
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            # ② 严格 owner 闸(写)
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            row = db.execute(
                """
                insert into script_timeline_anchors
                  (script_id, story_phase, story_time_label, chapter_min, chapter_max,
                   chapter_count, sample_title, sample_summary, keywords, confidence, source)
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'editor')
                on conflict (script_id, story_phase, story_time_label) do nothing
                returning id
                """,
                (sid, phase, label, cmin, cmax, max(1, cmax - cmin + 1),
                 title, summary, keywords, confidence),
            ).fetchone()
            if not row:
                return (
                    f"失败: 剧本 #{sid} 已有同名节点「{label}」(阶段「{phase or '未分阶段'}」)。"
                    "要改它请用 update_anchor(先 list_anchors 拿 anchor_id),不要重复新建。"
                )
            aid = int(row["id"])
            try:
                _write_commit(
                    db, script_id=sid, user_id=user_id, kind="anchor_add",
                    message=f"新增 anchor「{label}」",
                    payload={"table": "script_timeline_anchors", "op": "add",
                             "ids": {"anchor_id": aid}, "source": "editor"},
                )
            except Exception:
                pass
            db.commit()
        return (
            f"已新建时间线锚点 #{aid}「{label}」(剧本 #{sid};来源 editor,"
            "时间线重建不会删它)"
        )
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_delete_anchor(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """删除一个时间线锚点(anchor_id 必填,先 list_anchors 拿 id)。不可逆,删前向用户确认。
    注意:若它是原著骨架(source=novel),时间线重建可能会再生成;作者新增的(source=editor)删了不再生。"""
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    try:
        aid = int(args.get("anchor_id"))
    except (TypeError, ValueError):
        return "失败: anchor_id 必填且为整数(先 list_anchors 拿 id)"
    try:
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            row = db.execute(
                "select story_time_label, source from script_timeline_anchors where id=%s and script_id=%s",
                (aid, sid),
            ).fetchone()
            if not row:
                return f"失败: 锚点 #{aid} 不存在或不属于剧本 #{sid}"
            db.execute("delete from script_timeline_anchors where id=%s and script_id=%s", (aid, sid))
            try:
                from platform_app.api.script_edit import _write_commit
                _write_commit(db, script_id=sid, user_id=user_id, kind="anchor_delete",
                              message=f"删除锚点 #{aid}「{row['story_time_label']}」",
                              payload={"table": "script_timeline_anchors", "op": "delete",
                                       "ids": {"anchor_id": aid}})
            except Exception:
                pass
            db.commit()
        _note = "(原著骨架,重建可能再生成)" if str(row.get("source") or "") == "novel" else "(作者新增,删了不再生)"
        return f"已删除时间线锚点 #{aid}「{row['story_time_label']}」(剧本 #{sid}){_note}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


