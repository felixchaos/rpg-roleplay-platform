"""knowledge._worldbook_repo — worldbook 的 SQL 层 (private)."""
from __future__ import annotations

# 列表 / 单条读取的列清单:除向量列以外的全部列。
# 不能 select *:embedding_vec(768 维)被 psycopg 当字符串带出来,一条约 7KB,占整行 JSON 九成;
# md-editor 资源管理器全量加载(fetch_all)一个有 1500 条已嵌入条目的剧本要下载十几 MB,
# 且以前每打开一条都再拉一遍全量。向量只给检索用,前端 / 编辑器 / 导出都不需要它。
# (first_chapter / last_seen_chapter 全库从未建过这两列,别加进来 —— 见 migrations 的注释。)
_WB_READ_COLS = (
    "id, book_id, script_id, title, content, keys, regex_keys, priority, token_budget, "
    "insertion_position, sticky_turns, cooldown_turns, probability, character_filter, "
    "scene_filter, enabled, metadata, created_at, updated_at, public_id, row_version, "
    "embedded_at, first_revealed_chapter, reveal_anchor_key, reveal_known"
)


def _db_select_worldbook_entries(db, script_id: int, before_id: int | None, page_limit: int) -> list:
    """repository: 按 script_id/cursor 分页查 worldbook_entries，返回 rows。"""
    return db.execute(
        f"""
        select {_WB_READ_COLS} from worldbook_entries
        where script_id = %s and (%s::bigint is null or id < %s)
        order by priority desc, id desc
        limit %s
        """,
        (script_id, before_id, before_id, page_limit + 1),
    ).fetchall()


def _db_select_all_worldbook_entries(db, script_id: int) -> list:
    """repository: 一次性取某剧本【全部】 worldbook_entries(供 owner 编辑器全量加载)。

    注:游标分页(`id < before_id`)与排序(`priority desc, id desc`)不一致,多页会漏掉
    低优先级/高 id 的条目 —— 编辑器需要全量管理,故走此无分页路径,绕开该游标缺陷。
    """
    return db.execute(
        f"""
        select {_WB_READ_COLS} from worldbook_entries
        where script_id = %s
        order by priority desc, id desc
        """,
        (script_id,),
    ).fetchall()


def _db_select_worldbook_entry(db, script_id: int, entry_id: int):
    """repository: 单条 worldbook_entries(按剧本收窄,别的剧本的 id 读不到)。"""
    return db.execute(
        f"select {_WB_READ_COLS} from worldbook_entries where script_id = %s and id = %s",
        (script_id, entry_id),
    ).fetchone()
