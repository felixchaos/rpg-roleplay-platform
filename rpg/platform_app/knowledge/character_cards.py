from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from platform_app.api._card_dto import card_page_payload, card_to_dto
from platform_app.db import connect, init_db, limit_value, page_payload
from platform_app.knowledge._character_cards_repo import (
    _db_delete_character_card,
    _db_get_character_card,
    _db_select_chapter_facts,
    _db_select_character_cards,
    _db_set_character_card_enabled,
    _db_set_protagonist,
)
from platform_app.knowledge._utils import _cursor_int, _require_script, _require_script_owner


def list_chapter_facts(user_id: int, script_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit)
    before_chapter = _cursor_int(cursor)
    with connect() as db:
        _require_script(db, user_id, script_id)
        rows = _db_select_chapter_facts(db, script_id, before_chapter, page_limit)
    payload = page_payload(rows, page_limit)
    if payload["items"]:
        payload["page"]["next_cursor"] = str(payload["items"][-1]["chapter"]) if payload["page"]["has_more"] else None
    return payload


def list_character_cards(user_id: int, script_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    """剧本 NPC 角色卡列表。v28 起返回统一 CharacterCardDTO(_card_dto.card_to_dto)。"""
    init_db()
    page_limit = limit_value(limit)
    before_id = _cursor_int(cursor)
    with connect() as db:
        _require_script(db, user_id, script_id)
        rows = _db_select_character_cards(db, script_id, before_id, page_limit)
    return card_page_payload(rows, page_limit)


def get_character_card(user_id: int, script_id: int, card_id: int) -> dict[str, Any] | None:
    """单条剧本 NPC 角色卡详情。v28 起返回统一 CharacterCardDTO。"""
    init_db()
    with connect() as db:
        _require_script(db, user_id, script_id)
        row = _db_get_character_card(db, script_id, card_id)
    return card_to_dto(row) if row else None


def upsert_character_card(user_id: int, script_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """创建/更新剧本 NPC 角色卡。card_id 给定就 update，否则 insert。

    v28: 加 full_name / background / first_revealed_chapter / importance / aliases 等字段。
    强制 card_type='npc',source='platform'(人工 API 路径,区分于 extract 链路 source='extracted')。
    """
    init_db()
    name = (payload.get("name") or "").strip()
    if not name:
        raise ValueError("character.name 不能为空")
    card_id = payload.get("id")
    fields = {
        "name": name,
        "full_name": (payload.get("full_name") or "").strip(),
        "aliases": Jsonb(payload.get("aliases") or []),
        "identity": (payload.get("identity") or "").strip(),
        "background": (payload.get("background") or "").strip(),
        "appearance": (payload.get("appearance") or "").strip(),
        "personality": (payload.get("personality") or "").strip(),
        "speech_style": (payload.get("speech_style") or "").strip(),
        "current_status": (payload.get("current_status") or "").strip(),
        "secrets": (payload.get("secrets") or "").strip(),
        "sample_dialogue": Jsonb(payload.get("sample_dialogue") or []),
        "first_revealed_chapter": int(payload.get("first_revealed_chapter") or 0),
        "importance": int(payload.get("importance") or 0),
        "token_budget": int(payload.get("token_budget") or 450),
        "priority": int(payload.get("priority") or 100),
        "enabled": bool(payload.get("enabled", True)),
        "metadata": Jsonb(payload.get("metadata") or {}),
    }
    with connect() as db:
        # task: P0 修复 — character_card upsert 是 WRITE,必须 owner-only。
        # 订阅者(user_script_subscriptions)即使能读也不能改原作者剧本的 NPC 卡。
        _require_script_owner(db, user_id, script_id)
        # book_id 是遗留可空列(归属看 script_id);有 books 行就带,没有就 NULL ——
        # 空白/未同步剧本也能直接建 NPC 卡,不再强制先 knowledge/sync。
        book = db.execute("select id from books where script_id = %s", (script_id,)).fetchone()
        book_id = int(book["id"]) if book else None
        if card_id:
            owned = db.execute(
                "select 1 from character_cards where id = %s and script_id = %s and card_type='npc'",
                (int(card_id), script_id),
            ).fetchone()
            if not owned:
                raise ValueError("character_card 不存在或不属于该剧本")
            # 改名预检:把卡改成已被**别的** NPC 占用的名字会撞 uq_character_cards_npc_name
            # (script_id,name)唯一约束 → 裸 UniqueViolation 冒成 500、前端「保存没反应」。
            # 先查冲突给出可行动 ValueError(端点 ValueError→400,前端能显示原因)。
            # 同名更新自身不算冲突(id<>),所以不改名的普通编辑不受影响。
            clash = db.execute(
                "select 1 from character_cards where script_id = %s and name = %s "
                "and card_type='npc' and id <> %s",
                (script_id, name, int(card_id)),
            ).fetchone()
            if clash:
                raise ValueError(f"该剧本已存在同名 NPC 角色卡「{name}」,请改用不同的名字"
                                 "(或先删除/合并重名卡)")
            db.execute(
                """
                update character_cards set
                  name=%(name)s, full_name=%(full_name)s, aliases=%(aliases)s,
                  identity=%(identity)s, background=%(background)s,
                  appearance=%(appearance)s, personality=%(personality)s,
                  speech_style=%(speech_style)s, current_status=%(current_status)s,
                  secrets=%(secrets)s, sample_dialogue=%(sample_dialogue)s,
                  first_revealed_chapter=%(first_revealed_chapter)s,
                  importance=%(importance)s, token_budget=%(token_budget)s,
                  priority=%(priority)s, enabled=%(enabled)s, metadata=%(metadata)s,
                  row_version=row_version+1, updated_at=now()
                where id=%(id)s and script_id=%(script_id)s and card_type='npc'
                """,
                {**fields, "id": int(card_id), "script_id": script_id},
            )
            row = db.execute("select * from character_cards where id = %s", (int(card_id),)).fetchone()
        else:
            row = db.execute(
                """
                insert into character_cards(
                  book_id, script_id, name, full_name, aliases, identity, background,
                  appearance, personality, speech_style, current_status, secrets,
                  sample_dialogue, first_revealed_chapter, importance,
                  token_budget, priority, enabled, metadata,
                  card_type, source, scope
                ) values (
                  %(book_id)s, %(script_id)s, %(name)s, %(full_name)s, %(aliases)s,
                  %(identity)s, %(background)s, %(appearance)s, %(personality)s,
                  %(speech_style)s, %(current_status)s, %(secrets)s,
                  %(sample_dialogue)s, %(first_revealed_chapter)s, %(importance)s,
                  %(token_budget)s, %(priority)s, %(enabled)s, %(metadata)s,
                  'npc', 'platform', 'script'
                )
                on conflict(script_id, name) where card_type = 'npc'
                do update set
                  full_name=excluded.full_name, aliases=excluded.aliases,
                  identity=excluded.identity, background=excluded.background,
                  appearance=excluded.appearance, personality=excluded.personality,
                  speech_style=excluded.speech_style, current_status=excluded.current_status,
                  secrets=excluded.secrets, sample_dialogue=excluded.sample_dialogue,
                  first_revealed_chapter=excluded.first_revealed_chapter,
                  importance=excluded.importance, token_budget=excluded.token_budget,
                  priority=excluded.priority, enabled=excluded.enabled,
                  metadata=excluded.metadata, source='platform', scope='script',
                  row_version=character_cards.row_version+1, updated_at=now()
                returning *
                """,
                {**fields, "book_id": book_id, "script_id": script_id},
            ).fetchone()
    return card_to_dto(row) or {}


def delete_character_card(user_id: int, script_id: int, card_id: int) -> dict[str, Any]:
    """删除剧本 NPC 角色卡。**仅 owner**。"""
    init_db()
    with connect() as db:
        _require_script_owner(db, user_id, script_id)
        cur = _db_delete_character_card(db, script_id, card_id)
    return {"ok": True, "deleted": bool(cur), "id": card_id}


def set_character_card_enabled(user_id: int, script_id: int, card_id: int, enabled: bool) -> dict[str, Any]:
    """快捷启停切换,给前端"在检索中临时屏蔽这个角色"用。**仅 owner**。
    v28 起返回统一 DTO。
    """
    init_db()
    with connect() as db:
        _require_script_owner(db, user_id, script_id)
        row = _db_set_character_card_enabled(db, script_id, card_id, enabled)
    if not row:
        raise ValueError("character_card 不存在")
    return card_to_dto(row) or {}


def set_character_card_protagonist(user_id: int, script_id: int, card_id: int) -> dict[str, Any]:
    """手动指定剧本主角(**仅 owner**)。清掉其它卡的主角标记,目标卡锁定为主角。

    解决两件事:
      1) canon importance 误判 → 把配角(如奶娘/亲近之人)标成主角,需要人工纠正;
      2) 纠正后重新提取(canon_extract → _rerank_cards_by_canon_importance)会再次按 LLM
         importance 覆盖回去 —— 这里写 metadata.protagonist_locked=true,重排逻辑见到锁
         就跳过,人工指定的主角不再被覆盖。
    v28 起返回统一 DTO。
    """
    init_db()
    with connect() as db:
        _require_script_owner(db, user_id, script_id)
        row = _db_set_protagonist(db, script_id, int(card_id))
    if not row:
        raise ValueError("character_card 不存在或不属于该剧本")
    return card_to_dto(row) or {}
