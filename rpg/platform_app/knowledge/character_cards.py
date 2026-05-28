from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from platform_app.db import connect, expose, init_db, limit_value, page_payload
from platform_app.knowledge._utils import _cursor_int, _require_script


def list_chapter_facts(user_id: int, script_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit)
    before_chapter = _cursor_int(cursor)
    with connect() as db:
        _require_script(db, user_id, script_id)
        rows = db.execute(
            """
            select id, public_id, chapter, title, summary, story_phase, story_time_label,
                   scene_count, token_estimate, confidence, created_at, updated_at
            from chapter_facts
            where script_id = %s and (%s::integer is null or chapter > %s)
            order by chapter asc
            limit %s
            """,
            (script_id, before_chapter, before_chapter, page_limit + 1),
        ).fetchall()
    payload = page_payload(rows, page_limit)
    if payload["items"]:
        payload["page"]["next_cursor"] = str(payload["items"][-1]["chapter"]) if payload["page"]["has_more"] else None
    return payload


def list_character_cards(user_id: int, script_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit)
    before_id = _cursor_int(cursor)
    with connect() as db:
        _require_script(db, user_id, script_id)
        rows = db.execute(
            """
            select * from character_cards
            where script_id = %s and (%s::bigint is null or id < %s)
            order by priority desc, id desc
            limit %s
            """,
            (script_id, before_id, before_id, page_limit + 1),
        ).fetchall()
    return page_payload(rows, page_limit)


def get_character_card(user_id: int, script_id: int, card_id: int) -> dict[str, Any] | None:
    """单条剧本角色卡详情"""
    init_db()
    with connect() as db:
        _require_script(db, user_id, script_id)
        row = db.execute(
            "select * from character_cards where id = %s and script_id = %s",
            (card_id, script_id),
        ).fetchone()
    return expose(row) if row else None


def upsert_character_card(user_id: int, script_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """创建/更新剧本角色卡。card_id 给定就 update，否则 insert。"""
    init_db()
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("character.name 不能为空")
    card_id = payload.get("id")
    fields = {
        "name": name,
        "aliases": Jsonb(payload.get("aliases") or []),
        "identity": (payload.get("identity") or "").strip(),
        "appearance": (payload.get("appearance") or "").strip(),
        "personality": (payload.get("personality") or "").strip(),
        "speech_style": (payload.get("speech_style") or "").strip(),
        "current_status": (payload.get("current_status") or "").strip(),
        "secrets": (payload.get("secrets") or "").strip(),
        "sample_dialogue": Jsonb(payload.get("sample_dialogue") or []),
        "token_budget": int(payload.get("token_budget") or 450),
        "priority": int(payload.get("priority") or 100),
        "enabled": bool(payload.get("enabled", True)),
        "metadata": Jsonb(payload.get("metadata") or {}),
    }
    with connect() as db:
        script = _require_script(db, user_id, script_id)
        book = db.execute("select id from books where script_id = %s", (script_id,)).fetchone()
        if not book:
            raise ValueError("剧本 book 未初始化，先调一次 /api/scripts/{id}/knowledge/sync")
        book_id = int(book["id"])
        if card_id:
            owned = db.execute(
                "select 1 from character_cards where id = %s and script_id = %s",
                (int(card_id), script_id),
            ).fetchone()
            if not owned:
                raise ValueError("character_card 不存在或不属于该剧本")
            db.execute(
                """
                update character_cards set
                  name=%(name)s, aliases=%(aliases)s,
                  identity=%(identity)s, appearance=%(appearance)s,
                  personality=%(personality)s, speech_style=%(speech_style)s,
                  current_status=%(current_status)s, secrets=%(secrets)s,
                  sample_dialogue=%(sample_dialogue)s, token_budget=%(token_budget)s,
                  priority=%(priority)s, enabled=%(enabled)s, metadata=%(metadata)s,
                  row_version=row_version+1, updated_at=now()
                where id=%(id)s and script_id=%(script_id)s
                """,
                {**fields, "id": int(card_id), "script_id": script_id},
            )
            row = db.execute("select * from character_cards where id = %s", (int(card_id),)).fetchone()
        else:
            row = db.execute(
                """
                insert into character_cards(
                  book_id, script_id, name, aliases, identity, appearance, personality,
                  speech_style, current_status, secrets, sample_dialogue,
                  token_budget, priority, enabled, metadata
                ) values (
                  %(book_id)s, %(script_id)s, %(name)s, %(aliases)s, %(identity)s,
                  %(appearance)s, %(personality)s, %(speech_style)s, %(current_status)s,
                  %(secrets)s, %(sample_dialogue)s, %(token_budget)s,
                  %(priority)s, %(enabled)s, %(metadata)s
                )
                returning *
                """,
                {**fields, "book_id": book_id, "script_id": script_id},
            ).fetchone()
    return expose(row) or {}


def delete_character_card(user_id: int, script_id: int, card_id: int) -> dict[str, Any]:
    """删除剧本角色卡。"""
    init_db()
    with connect() as db:
        _require_script(db, user_id, script_id)
        cur = db.execute(
            "delete from character_cards where id = %s and script_id = %s returning id",
            (card_id, script_id),
        ).fetchone()
    return {"ok": True, "deleted": bool(cur), "id": card_id}


def set_character_card_enabled(user_id: int, script_id: int, card_id: int, enabled: bool) -> dict[str, Any]:
    """快捷启停切换，给前端"在检索中临时屏蔽这个角色"用。"""
    init_db()
    with connect() as db:
        _require_script(db, user_id, script_id)
        row = db.execute(
            """
            update character_cards set enabled = %s, row_version = row_version + 1, updated_at = now()
            where id = %s and script_id = %s
            returning *
            """,
            (bool(enabled), card_id, script_id),
        ).fetchone()
    if not row:
        raise ValueError("character_card 不存在")
    return expose(row) or {}
