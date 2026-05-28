from __future__ import annotations

from typing import Any

from platform_app import runtime
from platform_app.db import connect, expose, init_db
from platform_app.knowledge._search import _search_chunks
from platform_app.knowledge._utils import _query_tokens


def retrieve_runtime_context(
    query: str,
    *,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    top_k: int = 3,
    user_id: int | None = None,
) -> str:
    """按当前用户的 runtime 拿剧本 chunks。

    多用户安全：user_id 给定时严格按 user 读 runtime + 校验 save 归属。
    不给 user_id 仅在本地匿名（兼容旧逻辑），多用户场景一定要传。
    """
    meta = runtime.read_runtime(user_id=user_id)
    if not meta:
        return ""
    save_id = int(meta.get("save_id") or 0)
    if not save_id:
        return ""
    # 严格校验 runtime 属于当前 user
    if user_id and int(meta.get("user_id") or 0) != int(user_id):
        return ""
    with connect() as db:
        if user_id:
            save = db.execute(
                "select * from game_saves where id = %s and user_id = %s",
                (save_id, int(user_id)),
            ).fetchone()
        else:
            save = db.execute("select * from game_saves where id = %s", (save_id,)).fetchone()
        if not save:
            return ""
        return retrieve_script_context(
            int(save["script_id"]),
            query,
            chapter_min=chapter_min,
            chapter_max=chapter_max,
            top_k=top_k,
            db=db,
        )


def retrieve_script_context(
    script_id: int,
    query: str,
    *,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    top_k: int = 3,
    db=None,
) -> str:
    owns_connection = db is None
    if owns_connection:
        init_db()
        cm = connect()
        db = cm.__enter__()
    try:
        parts: list[str] = []
        fact_rows = db.execute(
            """
            select chapter, title, story_time_label, summary, events
            from chapter_facts
            where script_id = %s
              and (%s::integer is null or chapter >= %s)
              and (%s::integer is null or chapter <= %s)
            order by chapter
            limit %s
            """,
            (script_id, chapter_min, chapter_min, chapter_max, chapter_max, max(1, top_k + 2)),
        ).fetchall()
        if fact_rows:
            lines = []
            for row in fact_rows:
                events = row.get("events") or []
                event_text = "；".join(str(item.get("event", "")) for item in events[:2] if isinstance(item, dict))
                lines.append(
                    f"第{row['chapter']}章《{row['title']}》｜{row.get('story_time_label') or ''}\n"
                    f"摘要：{(row.get('summary') or '')[:180]}\n"
                    f"事件：{event_text[:220]}"
                )
            parts.append("=== Postgres ChapterFact ===\n" + "\n\n".join(lines))

        tokens = _query_tokens(query)
        chunk_rows = _search_chunks(db, script_id, tokens, chapter_min, chapter_max, top_k)
        if chunk_rows:
            parts.append(
                "=== Postgres 原文片段 ===\n"
                + "\n\n".join(
                    f"[第{row['chapter_index']}章片段]\n{row['content'][:360].strip()}"
                    for row in chunk_rows
                )
            )
        return "\n\n".join(parts)
    finally:
        if owns_connection:
            cm.__exit__(None, None, None)
