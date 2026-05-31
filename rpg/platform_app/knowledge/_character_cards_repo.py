"""knowledge._character_cards_repo — character_cards 的 SQL 层 (private)."""
from __future__ import annotations


def _db_select_chapter_facts(db, script_id: int, before_chapter: int | None, page_limit: int) -> list:
    """repository: 按 script_id/cursor 分页查 chapter_facts，返回 rows。"""
    return db.execute(
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


def _db_select_character_cards(db, script_id: int, before_id: int | None, page_limit: int) -> list:
    """repository: 按 script_id/cursor 分页查 character_cards (仅 NPC),返回 rows。

    v28: character_cards 多态后,显式 card_type='npc' 过滤,避免万一脏数据带 PC/persona 行混入。
    """
    return db.execute(
        """
        select * from character_cards
        where script_id = %s and card_type = 'npc' and (%s::bigint is null or id < %s)
        order by priority desc, id desc
        limit %s
        """,
        (script_id, before_id, before_id, page_limit + 1),
    ).fetchall()


def _db_get_character_card(db, script_id: int, card_id: int):
    """repository: 按 id+script_id 查单条 NPC character_card。"""
    return db.execute(
        "select * from character_cards where id = %s and script_id = %s and card_type = 'npc'",
        (card_id, script_id),
    ).fetchone()


def _db_delete_character_card(db, script_id: int, card_id: int):
    """repository: 按 id+script_id 删除 NPC character_card,返回 row 或 None。"""
    return db.execute(
        "delete from character_cards where id = %s and script_id = %s and card_type = 'npc' returning id",
        (card_id, script_id),
    ).fetchone()


def _db_set_character_card_enabled(db, script_id: int, card_id: int, enabled: bool):
    """repository: 更新 NPC character_card.enabled,返回 row 或 None。"""
    return db.execute(
        """
        update character_cards set enabled = %s, row_version = row_version + 1, updated_at = now()
        where id = %s and script_id = %s and card_type = 'npc'
        returning *
        """,
        (bool(enabled), card_id, script_id),
    ).fetchone()
