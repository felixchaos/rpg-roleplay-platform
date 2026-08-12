from __future__ import annotations

from platform_app import runtime
from platform_app.db import connect, init_db
from platform_app.knowledge._pin import effective_kb_script_id
from platform_app.knowledge._search import _search_chunks, _search_entities
from platform_app.knowledge._utils import _query_tokens
from platform_app.perms import owns_save


def retrieve_runtime_context(
    query: str,
    *,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    top_k: int = 3,
    user_id: int | None = None,
    progress_chapter: int | None = None,
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
        # 归属判定收敛到 perms.owns_save(user_id 给定时严格按 user 校验);
        # 不属 / 不存在均返 "" —— 沿用原契约(不抛)。
        if user_id and not owns_save(db, save_id, int(user_id)):
            return ""
        save = db.execute("select * from game_saves where id = %s", (save_id,)).fetchone()
        if not save:
            return ""
        # P4(S2):元知识模式从 game_sessions.worldline 取(无则 none),供 reveal_clause_v2 前沿门控。
        mode = "none"
        sess = db.execute(
            "select worldline from game_sessions where save_id=%s", (save_id,)
        ).fetchone()
        if sess and isinstance(sess.get("worldline"), dict):
            mode = sess["worldline"].get("foreknowledge_mode") or "none"
        return retrieve_script_context(
            int(save["script_id"]),
            query,
            chapter_min=chapter_min,
            chapter_max=chapter_max,
            top_k=top_k,
            user_id=user_id,
            db=db,
            progress_chapter=progress_chapter,
            save_id=save_id,
            mode=mode,
        )


def retrieve_script_context(
    script_id: int,
    query: str,
    *,
    chapter_min: int | None = None,
    chapter_max: int | None = None,
    top_k: int = 3,
    user_id: int | None = None,
    db=None,
    progress_chapter: int | None = None,
    save_id: int | None = None,
    mode: str = "none",
) -> str:
    owns_connection = db is None
    if owns_connection:
        init_db()
        cm = connect()
        db = cm.__enter__()
    try:
        parts: list[str] = []
        # pin 重定向:引用剧本(pinned/floating)检索读 pin 目标的数据;非 pin 原样,零影响。
        script_id = effective_kb_script_id(db, script_id)
        # 排序锚在玩家当前章,而不是窗口最前面。
        # 前科(v1.73.1,群反馈「ChapterFact 从第一章开始召回」):`order by chapter` + limit
        # 在宽窗口下恒取窗口头部——窗口一退化成 phase 区间(实测 1-78 章),玩家在第 67 章
        # 却永远只拿到第 1-5 章的摘要。窗口收窄是根修,这里是同一病灶的第二处:即便窗口正确,
        # 只要它比 limit 宽,靠前的章仍会挤掉玩家眼前的章。
        # progress 未知时保持原「窗口内从前往后」行为,零变化。
        if progress_chapter and int(progress_chapter) >= 1:
            _order = "order by abs(chapter - %s) asc, chapter desc"
            _order_params = (int(progress_chapter),)
        else:
            _order = "order by chapter"
            _order_params = ()
        fact_rows = db.execute(
            f"""
            select chapter, title, story_time_label, summary, events
            from chapter_facts
            where script_id = %s
              and (%s::integer is null or chapter >= %s)
              and (%s::integer is null or chapter <= %s)
            {_order}
            limit %s
            """,
            (script_id, chapter_min, chapter_min, chapter_max, chapter_max,
             *_order_params, max(1, top_k + 2)),
        ).fetchall()
        # 注入序仍按章号升序(给 GM 读的是时间顺序,不是相关性顺序)
        fact_rows = sorted(fact_rows, key=lambda r: int(r["chapter"] or 0))
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
        chunk_rows = _search_chunks(
            db,
            script_id,
            tokens,
            chapter_min,
            chapter_max,
            top_k,
            user_id=user_id,
        )
        if chunk_rows:
            parts.append(
                "=== Postgres 原文片段 ===\n"
                + "\n\n".join(
                    f"[第{row['chapter_index']}章片段]\n{row['content'][:360].strip()}"
                    for row in chunk_rows
                )
            )

        # task 51/52: LightRAG 双层检索第 2 层 — entity 层向量召回。
        # query 提到 NPC 名 / 地名 / 设定词时,直接返回完整人物卡 + 世界书条目,
        # GM 拿到的不是"片段+猜",而是"角色档案+确定信息"。
        # task 52 + BUG-1: 必须传 chapter_max 限制召回范围,防止剧透 — 第 1 章玩家
        # 向量召回不能拉第 391 章才出现的角色。
        # 剧透天花板 = min(场景窗口 chapter_max, 玩家进度 progress_chapter):
        #   - chapter_max 为 None(时间线未解析)时退到 progress(绝不放行全书);
        #   - chapter_max 高于 progress(/set 跳跃等边界)时钳回 progress;
        #   - progress_chapter 由 retrieve_context 每回合同步(BUG-3),且 spoiler-safe 默认 1。
        if progress_chapter is not None:
            _entity_ceiling = (
                int(progress_chapter) if chapter_max is None
                else min(int(chapter_max), int(progress_chapter))
            )
        else:
            _entity_ceiling = chapter_max  # 无进度上下文(管理/编辑器视角)→ 维持原 chapter_max
        try:
            ents = _search_entities(
                db, script_id, query,
                chapter_min=chapter_min,
                chapter_max=_entity_ceiling,
                top_k_cards=3, top_k_wb=3,
                user_id=user_id,
                save_id=save_id,
                mode=mode,
            )
            if ents.get("cards"):
                lines = []
                for c in ents["cards"]:
                    # 拼成 GM 易读的"角色档案"格式
                    bio = (c.get("identity") or "").strip()
                    persona = (c.get("personality") or "").strip()[:240]
                    look = (c.get("appearance") or "").strip()[:160]
                    lines.append(
                        f"《{c['name']}》(相关度 {c['score']:.2f})\n"
                        f"  身份:{bio or '—'}\n"
                        f"  性格:{persona or '—'}\n"
                        f"  外貌:{look or '—'}"
                    )
                parts.append("=== 角色档案(向量召回) ===\n" + "\n".join(lines))
            if ents.get("worldbook"):
                lines = [
                    f"《{w['title']}》(相关度 {w['score']:.2f}): {(w.get('content') or '')[:240]}"
                    for w in ents["worldbook"]
                ]
                parts.append("=== 世界书条目(向量召回) ===\n" + "\n".join(lines))
        except Exception:
            pass  # 向量未就绪 / Vertex 失败 → 跳过 entity 层,保留 chunks

        return "\n\n".join(parts)
    finally:
        if owns_connection:
            cm.__exit__(None, None, None)
