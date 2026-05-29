from __future__ import annotations

from typing import Any

_VEC_COLUMN_CACHE: dict[str, bool] = {}


def _vector_column_exists(db, table: str) -> bool:
    if table in _VEC_COLUMN_CACHE:
        return _VEC_COLUMN_CACHE[table]
    try:
        row = db.execute(
            "select 1 from information_schema.columns "
            "where table_name = %s and column_name = 'embedding_vec'",
            (table,),
        ).fetchone()
        _VEC_COLUMN_CACHE[table] = bool(row)
    except Exception:
        _VEC_COLUMN_CACHE[table] = False
    return _VEC_COLUMN_CACHE[table]


def _embed_query(text: str) -> str | None:
    """把 query 文本转 vector(768) 字符串。

    task 51: 接 Vertex text-embedding-004 (768 维)。失败返 None 自动 fallback ILIKE。
    embedding 模块 lazy import 避免冷启动开销;client 内部 cache。
    """
    try:
        from .embedding import embed_query as _eq
        return _eq(text)
    except Exception:
        return None


def _search_chunks(db, script_id: int, tokens: list[str], chapter_min: int | None, chapter_max: int | None, top_k: int) -> list[dict[str, Any]]:
    """检索：vector + BM25-like 双路。

    1. 如果有 query 的 vector embedding（_embed_query 拿到），且 document_chunks 有 embedding_vec，
       走 vector 余弦距离 ORDER BY embedding_vec <=> %s。
    2. 否则走原来的 ILIKE 词频。

    vector 不可用时 _embed_query 返 None，自动退化。
    """
    if not tokens:
        return []
    # 试 vector 路径
    vector_query = _embed_query(" ".join(tokens))
    if vector_query is not None and _vector_column_exists(db, "document_chunks"):
        try:
            # task 52: 两阶段查询 — 内层用 cosine 距离选 top_K(语义相关),
            # 外层按 chapter_index ASC 排序(时间线顺序)。
            # 这样 GM 拿到的 chunks 按章节顺序呈现,不会把第 800 章的事件
            # 当"当前回合已发生历史"误读。
            query = """
                select id, chapter_index, content, score from (
                  select id, chapter_index, content,
                         (1 - (embedding_vec <=> %s::vector)) as score
                  from document_chunks
                  where script_id = %s
                    and embedding_vec is not null
                    and (%s::integer is null or chapter_index >= %s)
                    and (%s::integer is null or chapter_index <= %s)
                  order by embedding_vec <=> %s::vector
                  limit %s
                ) ranked
                order by chapter_index asc, score desc
            """
            return db.execute(query, (
                vector_query, script_id,
                chapter_min, chapter_min, chapter_max, chapter_max,
                vector_query, max(1, min(top_k, 8)),
            )).fetchall()
        except Exception:
            pass  # vector 失败回退 ILIKE

    # 原 ILIKE 路径
    score_clauses = []
    where_clauses = []
    score_params: list[Any] = []
    where_params: list[Any] = []
    for token in tokens[:8]:
        pattern = f"%{token}%"
        score_clauses.append("case when content ilike %s then 1 else 0 end")
        where_clauses.append("content ilike %s")
        score_params.append(pattern)
        where_params.append(pattern)
    query = f"""
        select id, chapter_index, content,
               ({' + '.join(score_clauses)}) as score
        from document_chunks
        where script_id = %s
          and (%s::integer is null or chapter_index >= %s)
          and (%s::integer is null or chapter_index <= %s)
          and ({' or '.join(where_clauses)})
        order by score desc, chapter_index asc, chunk_index asc
        limit {max(1, min(top_k, 8))}
    """
    params = score_params + [script_id, chapter_min, chapter_min, chapter_max, chapter_max] + where_params
    return db.execute(query, tuple(params)).fetchall()


def _search_entities(
    db,
    script_id: int,
    query_text: str,
    *,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    top_k_cards: int = 3,
    top_k_wb: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    """task 51/52: LightRAG 双层检索的第二层 — entity 层。

    **时间线对齐**(task 52 关键): chapter_max 是 GM 当前回合"可见的最大章节",
    硬过滤掉 first_chapter > chapter_max 的角色/词条 —— 否则第 1 章玩家会被召回
    第 391 章才出现的莉莉丝,严重剧透。chapter_min 同理(防止已过去章节不再相关)。

    Returns: {"cards": [...], "worldbook": [...]}
    """
    out = {"cards": [], "worldbook": []}
    if not query_text:
        return out
    vec = _embed_query(query_text)
    if not vec:
        return out  # 没 embedding 跑不动,自动跳过

    if _vector_column_exists(db, "character_cards"):
        try:
            out["cards"] = db.execute(
                """
                select id, name, identity, personality, appearance,
                       first_chapter, last_seen_chapter,
                       (1 - (embedding_vec <=> %s::vector)) as score
                from character_cards
                where script_id = %s
                  and embedding_vec is not null
                  and enabled = true
                  -- task 52: 时间线硬过滤,GM 不该看到玩家还没读到的章节里的角色
                  and (%s::integer is null
                       or first_chapter is null
                       or first_chapter <= %s)
                order by embedding_vec <=> %s::vector
                limit %s
                """,
                (vec, script_id, chapter_max, chapter_max, vec,
                 max(1, min(top_k_cards, 8))),
            ).fetchall()
        except Exception:
            pass

    if _vector_column_exists(db, "worldbook_entries"):
        try:
            out["worldbook"] = db.execute(
                """
                select id, title, content, first_chapter, last_seen_chapter,
                       (1 - (embedding_vec <=> %s::vector)) as score
                from worldbook_entries
                where script_id = %s
                  and embedding_vec is not null
                  and (%s::integer is null
                       or first_chapter is null
                       or first_chapter <= %s)
                order by embedding_vec <=> %s::vector
                limit %s
                """,
                (vec, script_id, chapter_max, chapter_max, vec,
                 max(1, min(top_k_wb, 8))),
            ).fetchall()
        except Exception:
            pass

    return out
