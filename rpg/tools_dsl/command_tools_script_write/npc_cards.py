"""command_tools_script_write §NPC 角色卡族(拆包 2026-07-14,纯机械搬家零行为变化)。

NPC 角色卡:update(复用 character_cards.upsert,全量覆盖式先读现卡叠加)+ create。
严格 owner 闸;name 必填;tags 落进 metadata。
"""
from __future__ import annotations

from typing import Any

from ._helpers import _resolve_sid, _strlist


def _t_update_npc_card(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    card_id = args.get("card_id")
    if not card_id:
        return "失败: card_id 必填(先用 list_script_npcs 拿到角色卡 id)"
    try:
        cid = int(card_id)
    except (TypeError, ValueError):
        return "失败: card_id 必须是整数"
    try:
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        # ② 严格 owner 闸(upsert_character_card 内部用 _require_script_owner 也会再查,
        #    这里前置一道给统一友好失败串)。
        with connect() as db:
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            # 取现有卡作为基底:upsert_character_card 是「全量覆盖式」,缺省字段会被清空,
            # 故先读现卡、再用 args 里出现的字段叠加,避免漏传字段被抹掉。name 是必填(空会报错)。
            existing = db.execute(
                "select * from character_cards where id = %s and script_id = %s and card_type='npc'",
                (cid, sid),
            ).fetchone()
            if not existing:
                return f"失败: 角色卡 #{cid} 不存在或不属于剧本 #{sid}"
        base = dict(existing)
        # 撤销快照:存改前全字段(供作者一键撤回 AI 对角色卡的改动)。upsert 是全量覆盖式,
        # 把这些原值喂回即可还原。
        _card_before = {k: base.get(k) for k in (
            "name", "full_name", "aliases", "identity", "appearance", "personality",
            "speech_style", "current_status", "secrets", "background", "sample_dialogue",
            "importance", "first_revealed_chapter", "enabled")}
        _card_before["metadata"] = base.get("metadata") or {}
        # 只接受这些字段(不收 avatar_path —— 头像走专用端点)。
        editable = (
            "name", "full_name", "aliases", "identity", "appearance", "personality",
            "speech_style", "current_status", "secrets", "background", "sample_dialogue",
            "tags", "importance", "first_revealed_chapter", "enabled",
        )
        payload: dict[str, Any] = {"id": cid}
        for k in editable:
            if k in args and args[k] is not None:
                payload[k] = args[k]
            elif k in base and base[k] is not None:
                payload[k] = base[k]
        # name 必填:确保有值(取 args 或现卡)。
        if not (str(payload.get("name") or "").strip()):
            return "失败: name 不能为空"
        # tags 不是 character_cards 直接列(存进 metadata),upsert 不读 tags → 落进 metadata。
        if "tags" in payload:
            meta = dict(base.get("metadata") or {})
            meta["tags"] = _strlist(payload.pop("tags"))
            payload["metadata"] = meta
        # ③ 复用 character_cards.upsert(内部 _require_script_owner + Jsonb 化 aliases/sample_dialogue)。
        from platform_app.knowledge.character_cards import upsert_character_card
        upsert_character_card(user_id, sid, payload)
        # 审计
        try:
            from platform_app.api.script_edit import _write_commit
            with connect() as adb:
                if script_owned(adb, sid, user_id):
                    _write_commit(
                        adb, script_id=sid, user_id=user_id, kind="card_edit",
                        message=f"编辑 NPC 角色卡 #{cid}",
                        payload={"table": "character_cards", "op": "edit",
                                 "ids": {"card_id": cid},
                                 "fields": [k for k in editable if args.get(k) is not None],
                                 "before": _card_before, "undoable": True},
                    )
                    adb.commit()
        except Exception:
            pass
        return f"已更新 NPC 角色卡 #{cid}(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _t_create_npc_card(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    """为剧本「新建」一张 NPC 角色卡(name 必填)。可基于别的剧本/正文情节创建新角色。
    要改已有卡用 update_npc_card(先 list_script_npcs 拿 id)。"""
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    name = str(args.get("name") or "").strip()
    if not name:
        return "失败: name 必填(角色名)"
    try:
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            dup = db.execute(
                "select id from character_cards where script_id=%s and card_type='npc' and name=%s limit 1",
                (sid, name),
            ).fetchone()
            if dup:
                return (f"失败: 剧本 #{sid} 已有同名 NPC「{name}」(#{dup['id']})。"
                        "要改它用 update_npc_card,不要重复新建。")
        payload: dict[str, Any] = {"name": name}
        for k in ("full_name", "aliases", "identity", "appearance", "personality",
                  "speech_style", "current_status", "secrets", "background",
                  "sample_dialogue", "importance", "first_revealed_chapter"):
            if k in args and args[k] is not None:
                payload[k] = args[k]
        if isinstance(args.get("tags"), list):
            payload["metadata"] = {"tags": _strlist(args["tags"])}
        from platform_app.knowledge.character_cards import upsert_character_card
        row = upsert_character_card(user_id, sid, payload)
        cid = int(row["id"]) if isinstance(row, dict) and row.get("id") else None
        try:
            from platform_app.api.script_edit import _write_commit
            with connect() as adb:
                if script_owned(adb, sid, user_id):
                    _write_commit(adb, script_id=sid, user_id=user_id, kind="card_add",
                                  message=f"新增 NPC 角色卡「{name}」",
                                  payload={"table": "character_cards", "op": "add",
                                           "ids": {"card_id": cid}})
                    adb.commit()
        except Exception:
            pass
        return f"已新建 NPC 角色卡「{name}」(#{cid},剧本 #{sid})" if cid else \
            f"已新建 NPC 角色卡「{name}」(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


