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


def _db_get_character_card_by_name(db, script_id: int, name: str):
    """repository: 按 script_id+name 查单条 NPC character_card(唯一约束
    uq_character_cards_npc_name 保证最多一条)。导入同名卡时用来判「新建还是更新」。"""
    return db.execute(
        "select * from character_cards where script_id = %s and name = %s and card_type = 'npc'",
        (script_id, name),
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


def _db_set_protagonist(db, script_id: int, card_id: int):
    """repository: 手动把某 NPC 卡设为该剧本「主角」(锁定,不被 canon importance 重排覆盖)。

    分两步(同一事务):
      1) 清掉本剧本所有 NPC 卡的 is_protagonist + protagonist_locked,并把占着主角位
         (priority>=110)的卡降回普通 100 —— 保证全剧本最多一张主角卡。
      2) 目标卡设 is_protagonist=true + protagonist_locked=true(锁定标记让
         _rerank_cards_by_canon_importance 跳过它;重新提取不会再被 LLM importance 覆盖)
         + priority=110(召回排序排第一)。
    返回目标 row;目标不存在/不属于该剧本/非 NPC → None(调用方抛 ValueError)。
    """
    db.execute(
        """
        update character_cards
        set metadata = coalesce(metadata, '{}'::jsonb)
                        || jsonb_build_object('is_protagonist', false, 'protagonist_locked', false),
            priority = case when priority >= 110 then 100 else priority end,
            row_version = row_version + 1, updated_at = now()
        where script_id = %s and card_type = 'npc'
        """,
        (script_id,),
    )
    return db.execute(
        """
        update character_cards
        set metadata = coalesce(metadata, '{}'::jsonb)
                        || jsonb_build_object('is_protagonist', true, 'protagonist_locked', true),
            priority = 110, row_version = row_version + 1, updated_at = now()
        where id = %s and script_id = %s and card_type = 'npc'
        returning *
        """,
        (card_id, script_id),
    ).fetchone()
