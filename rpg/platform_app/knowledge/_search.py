from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)

_VEC_COLUMN_CACHE: dict[str, bool] = {}

# script_id → (embed_api_id, embed_model, cached_at) 进程内 cache
# TTL = 300s：workers=2 时，worker B 最多 5 分钟后自动感知到 worker A 重嵌后的新 meta
_SCRIPT_EMBED_META_CACHE: dict[int, tuple[str, str, float]] = {}
_SCRIPT_EMBED_META_TTL = 300.0

# 「没绑定 embed model」告警降频(2026-07-05 生产实证):未绑定的剧本每次检索
# (每 query 一次)都打一条 WARNING → 刷屏。同一 (script_id, 进程) 只需要提醒一次
# 「该重新拆书绑定」,后续降为 debug。模块级 set,进程重启/TTL 均不重置(未绑定
# 状态在重新 embed 前不会变化,没有必要按时间过期——过期只会让噪声重新出现)。
_UNBOUND_EMBED_WARNED: set[int] = set()


def _vector_column_exists(db, table: str) -> bool:
    if table in _VEC_COLUMN_CACHE:
        return _VEC_COLUMN_CACHE[table]
    try:
        # 必须是**真 pgvector 类型**列(udt_name='vector')才算"有向量列"。无 pgvector 的部署
        # (桌面捆绑版)migration 89 会建 jsonb 同名占位列让 `is not null` 计数不报错,但那种列
        # 不能跑 <=> 相似度 → 这里按 udt_name 区分,jsonb 占位列返回 False,自动退化到关键词检索。
        row = db.execute(
            "select 1 from information_schema.columns "
            "where table_name = %s and column_name = 'embedding_vec' and udt_name = 'vector'",
            (table,),
        ).fetchone()
        _VEC_COLUMN_CACHE[table] = bool(row)
    except Exception:
        _VEC_COLUMN_CACHE[table] = False
    return _VEC_COLUMN_CACHE[table]


def _get_script_embed_meta(db, script_id: int) -> tuple[str, str]:
    """从 scripts 表读取建库时绑定的 (embed_api_id, embed_model)。
    结果 cache 在进程内，TTL=300s（workers=2 下保证跨进程最终一致）。
    返回空字符串表示尚未绑定，调用方需 fallback。
    """
    now = time.monotonic()
    cached = _SCRIPT_EMBED_META_CACHE.get(script_id)
    if cached is not None:
        api_id_c, model_c, ts = cached
        if now - ts < _SCRIPT_EMBED_META_TTL:
            return api_id_c, model_c
        # TTL 过期：从 DB 重新拉
    try:
        row = db.execute(
            "select embed_api_id, embed_model from scripts where id = %s",
            (script_id,),
        ).fetchone()
        if row:
            result_api = row["embed_api_id"] or ""
            result_model = row["embed_model"] or ""
            _SCRIPT_EMBED_META_CACHE[script_id] = (result_api, result_model, now)
            return result_api, result_model
    except Exception as exc:
        log.debug("[_search] _get_script_embed_meta failed for script %s: %s", script_id, exc)
    return ("", "")


def _embed_query(
    text: str,
    *,
    script_id: int | None = None,
    user_id: int | None = None,
    db=None,
) -> str | None:
    """把 query 文本转 vector(768) 字符串。

    P0-fix: 召回时必须用与建库完全相同的 (api_id, model)，否则向量空间错乱。
    优先级:
      1. scripts.embed_api_id / embed_model（建库时锁定）
      2. user_id BYOK（仅 script_id 为 None 时，如 admin 工具）
      3. 系统默认 vertex + text-embedding-004

    失败返 None 自动 fallback ILIKE。
    """
    force_api_id: str | None = None
    force_model: str | None = None

    if script_id is not None and db is not None:
        api_id_locked, model_locked = _get_script_embed_meta(db, script_id)
        if api_id_locked and model_locked:
            force_api_id = api_id_locked
            force_model = model_locked
        else:
            # 已有剧本未绑定：fall back 到系统默认，发出警告。
            # 降频:同一 script_id 每 query 都会重新走到这里(每轮检索都调 _embed_query)
            # → 同一句 WARNING 刷屏。该 script 的绑定状态在重新 embed 前不会变化,
            # 第一次告警已经把「需要重新拆书」的信息传达到位,进程内只报一次即可,
            # 后续降为 debug(仍留痕,不完全消音)。
            if script_id not in _UNBOUND_EMBED_WARNED:
                _UNBOUND_EMBED_WARNED.add(script_id)
                log.warning(
                    "[_search] 召回剧本 %s 没绑定 embed model，fall back 到系统默认。"
                    "重新拆书后会绑定正确的 embed model。(本进程内此后降为 debug)",
                    script_id,
                )
            else:
                log.debug(
                    "[_search] 召回剧本 %s 没绑定 embed model，fall back 到系统默认。",
                    script_id,
                )
    elif script_id is not None and db is None:
        log.warning(
            "[_search] _embed_query: script_id=%s 但未传 db，无法读取建库 embed meta，"
            "fall back 到 user/系统默认。",
            script_id,
        )

    try:
        from .embedding import embed_query as _eq
        return _eq(text, user_id=user_id, force_api_id=force_api_id, force_model=force_model)
    except Exception as exc:
        log.debug("[_search] _embed_query failed: %s", exc)
        return None


def _search_chunks(
    db,
    script_id: int,
    tokens: list[str],
    chapter_min: int | None,
    chapter_max: int | None,
    top_k: int,
    *,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """检索：vector + BM25-like 双路。

    1. 如果有 query 的 vector embedding（_embed_query 拿到），且 document_chunks 有 embedding_vec，
       走 vector 余弦距离 ORDER BY embedding_vec <=> %s。
    2. 否则走原来的 ILIKE 词频。

    vector 不可用时 _embed_query 返 None，自动退化。
    """
    if not tokens:
        return []
    # 试 vector 路径 — 传 script_id + db 确保用建库时的 embed model
    vector_query = _embed_query(" ".join(tokens), script_id=script_id, user_id=user_id, db=db)
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
    user_id: int | None = None,
    save_id: int | None = None,
    mode: str = "none",
) -> dict[str, list[dict[str, Any]]]:
    """task 51/52: LightRAG 双层检索的第二层 — entity 层。

    **时间线对齐**(task 52 关键 + BUG-1 修复): chapter_max 是 GM 当前回合"可见的最大章节"
    (= 玩家进度,见 retrieve_script_context 把 progress_chapter 钳进来)。硬过滤掉
    first_revealed_chapter > chapter_max 的角色/词条 —— 否则第 1 章玩家会被召回第 391 章
    才出现的莉莉丝,严重剧透。

    进度列统一到 first_revealed_chapter(character_cards 自 v28 有;worldbook 自 v53 有),
    not null default 0(0=开局即可见,与 kb_canon_entities / canon_repo._reveal_clause 同约定)。
    **方向铁律**:不再用 `first_chapter is null` 放行 —— 过滤直接 `first_revealed_chapter <= %s`,
    未知/NULL 一律收紧(`null <= x` 为 false → 不召回),绝不放行后期实体。
    旧代码引用从不存在的 first_chapter/last_seen_chapter 列 → 裸 except 静默返空(漏功能但不剧透);
    本修复恢复召回的同时保持"不剧透"。

    Returns: {"cards": [...], "worldbook": [...]}
    """
    out = {"cards": [], "worldbook": []}
    if not query_text:
        return out
    vec = _embed_query(query_text, script_id=script_id, user_id=user_id, db=db)
    if not vec:
        # 无 embedding 时退化为 ILIKE 兜底(与 _search_chunks 同策略)
        tokens = [t for t in query_text.split() if t][:8]
        if not tokens:
            return out
        _OLD_GATE = "(%s::integer is null or first_revealed_chapter <= %s)"
        gate_sql, gate_params = _OLD_GATE, [chapter_max, chapter_max]
        for table, name_col, result_key, extra_cols in [
            ("character_cards", "name", "cards", "identity, personality, appearance,"),
            ("worldbook_entries", "title", "worldbook", "content,"),
        ]:
            enabled_clause = " and enabled = true" if table == "character_cards" else ""
            where_parts = [f"{name_col} ilike %s" for _ in tokens]
            patterns = [f"%{t}%" for t in tokens]
            try:
                rows = db.execute(
                    f"select id, {name_col}, {extra_cols} first_revealed_chapter, 0.5 as score "
                    f"from {table} "
                    f"where script_id = %s{enabled_clause} "
                    f"and ({' or '.join(where_parts)}) "
                    f"and {gate_sql} "
                    f"limit %s",
                    (*patterns, script_id, *gate_params, max(1, min(top_k_cards if result_key == "cards" else top_k_wb, 8))),
                ).fetchall()
                out[result_key] = rows
            except Exception:
                pass
        return out

    # P4(S2):门控有两套。旧=标量 `first_revealed_chapter <= chapter_max`(2 个 chapter_max 占位符);
    # 新=前沿 reveal_clause_v2(save_id)(1 个 save_id 占位符)。用 *gate_params 展开自动适配占位符个数。
    from kb.reveal import _frontier_on, _frontier_shadow, _shadow_diff_log, reveal_clause_v2
    use_v2 = save_id is not None and _frontier_on(save_id)
    _OLD_GATE = "(%s::integer is null or first_revealed_chapter <= %s)"
    if use_v2:
        gate_sql, gate_params = reveal_clause_v2(int(save_id), mode, prefix="", has_public_knowledge=False, has_famous=False, progress_chapter=chapter_max)
    else:
        gate_sql, gate_params = _OLD_GATE, [chapter_max, chapter_max]

    def _gate_ids(table: str, extra: str, g: str, p: list) -> set:
        """某门控放行的全集 id(不带 vector/limit),供影子比对隔离纯门控差异。"""
        return {r["id"] for r in db.execute(
            f"select id from {table} where script_id=%s and embedding_vec is not null{extra} and {g}",
            (script_id, *p)).fetchall()}

    def _shadow(table: str, extra: str, tag: str) -> None:
        """对比旧标量门控 vs 新前沿门控的放行全集(与 vector 排序/limit 无关)。"""
        old_g, old_p = _OLD_GATE, [chapter_max, chapter_max]
        new_g, new_p = reveal_clause_v2(int(save_id), mode, prefix="", has_public_knowledge=False, has_famous=False, progress_chapter=chapter_max)
        _shadow_diff_log(tag, _gate_ids(table, extra, old_g, old_p),
                         _gate_ids(table, extra, new_g, new_p))

    if _vector_column_exists(db, "character_cards"):
        try:
            out["cards"] = db.execute(
                f"""
                select id, name, identity, personality, appearance,
                       first_revealed_chapter,
                       (1 - (embedding_vec <=> %s::vector)) as score
                from character_cards
                where script_id = %s
                  and embedding_vec is not null
                  and enabled = true
                  -- BUG-1/P4: 时间线硬过滤,GM 不该看到玩家还没读到的章节里的角色。
                  and {gate_sql}
                order by embedding_vec <=> %s::vector
                limit %s
                """,
                (vec, script_id, *gate_params, vec, max(1, min(top_k_cards, 8))),
            ).fetchall()
            if _frontier_shadow() and save_id is not None:
                _shadow("character_cards", " and enabled = true", "_search cards")
        except Exception:
            pass

    if _vector_column_exists(db, "worldbook_entries"):
        try:
            out["worldbook"] = db.execute(
                f"""
                select id, title, content, first_revealed_chapter,
                       (1 - (embedding_vec <=> %s::vector)) as score
                from worldbook_entries
                where script_id = %s
                  and embedding_vec is not null
                  and {gate_sql}
                order by embedding_vec <=> %s::vector
                limit %s
                """,
                (vec, script_id, *gate_params, vec, max(1, min(top_k_wb, 8))),
            ).fetchall()
            if _frontier_shadow() and save_id is not None:
                _shadow("worldbook_entries", "", "_search worldbook")
        except Exception:
            pass

    return out
