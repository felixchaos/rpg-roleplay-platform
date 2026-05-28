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

    生产应接 Vertex/Anthropic/OpenAI 的 embedding API。当前简化：
    返回 None 让上层走 ILIKE 兜底。未来在这里接 embedding 服务即可全栈打通。
    """
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
            query = """
                select id, chapter_index, content,
                       (1 - (embedding_vec <=> %s::vector)) as score
                from document_chunks
                where script_id = %s
                  and embedding_vec is not null
                  and (%s::integer is null or chapter_index >= %s)
                  and (%s::integer is null or chapter_index <= %s)
                order by embedding_vec <=> %s::vector
                limit %s
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
