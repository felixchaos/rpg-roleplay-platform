"""extract/embed.py — Pass 3 规范实体嵌入(Vertex text-embedding-004, 768 维)。

复用 platform_app.knowledge.embedding._embed_batch(现网已用)。写 kb_canon_entities.embedding。
自托管 BGE-M3 未部署 → 用 Vertex 768(更省、已 live)。设计 A_extraction.md §6 Pass3。
"""
from __future__ import annotations


def embed_canon_entities(db, script_id: int, *, batch_size: int = 64, only_missing: bool = True) -> dict:
    """给规范实体生成嵌入(name + summary 拼接)。返回 {embedded, skipped}。"""
    from platform_app.knowledge.embedding import _embed_batch, _vec_literal

    where = "script_id = %s" + (" and embedding is null" if only_missing else "")
    rows = db.execute(
        f"select id, name, summary, aliases from kb_canon_entities where {where} order by id",
        (script_id,),
    ).fetchall()
    if not rows:
        return {"embedded": 0, "skipped": 0}

    embedded = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        texts = []
        for r in chunk:
            aliases = r.get("aliases") or []
            alias_str = "、".join(aliases) if isinstance(aliases, list) else ""
            texts.append(f"{r['name']} {alias_str} {r.get('summary') or ''}".strip())
        vecs = _embed_batch(texts)
        if not vecs:
            continue
        for r, vec in zip(chunk, vecs):
            db.execute(
                "update kb_canon_entities set embedding = %s where id = %s",
                (_vec_literal(vec), r["id"]),
            )
            embedded += 1
    return {"embedded": embedded, "skipped": len(rows) - embedded}


def search_canon_by_vector(db, script_id: int, query_vec_literal: str, *, top_k: int = 6,
                           progress_chapter: int | None = None) -> list[dict]:
    """pgvector cosine 检索规范实体(进度过滤)。query_vec_literal = embed_query 产物。"""
    sql = (
        "select logical_key, name, type, summary, first_revealed_chapter, "
        "1 - (embedding <=> %s) as score "
        "from kb_canon_entities where script_id = %s and embedding is not null"
    )
    args: list = [query_vec_literal, script_id]
    if progress_chapter is not None:
        sql += " and (first_revealed_chapter <= %s or public_knowledge)"
        args.append(progress_chapter)
    sql += " order by embedding <=> %s limit %s"
    args.extend([query_vec_literal, top_k])
    return db.execute(sql, tuple(args)).fetchall()
