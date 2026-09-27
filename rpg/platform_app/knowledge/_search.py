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

# (script_id, table) → (有没有向量, 缓存时刻)。「有」缓存 300s;「没有」只缓存 60s ——
# 新剧本刚建完向量时,最多 1 分钟就开始走向量召回(本进程的写库作业结束时还会主动作废)。
_SCRIPT_HAS_VEC_CACHE: dict[tuple[int, str], tuple[bool, float]] = {}
_HAS_VEC_TTL = 300.0
_NO_VEC_TTL = 60.0
# 表 → 向量列。kb_canon_entities 的列名历史上叫 embedding(kb_nodes 视图里把它别名成 embedding_vec)。
_VEC_PRESENCE_TABLES: dict[str, str] = {
    "document_chunks": "embedding_vec",
    "character_cards": "embedding_vec",
    "worldbook_entries": "embedding_vec",
    "kb_nodes": "embedding_vec",
    "kb_canon_entities": "embedding",
}


def _vector_column_exists(db, table: str, column: str = "embedding_vec") -> bool:
    ck = table if column == "embedding_vec" else f"{table}.{column}"
    if ck in _VEC_COLUMN_CACHE:
        return _VEC_COLUMN_CACHE[ck]
    try:
        # 必须是**真 pgvector 类型**列(udt_name='vector')才算"有向量列"。无 pgvector 的部署
        # (桌面捆绑版)migration 89 会建 jsonb 同名占位列让 `is not null` 计数不报错,但那种列
        # 不能跑 <=> 相似度 → 这里按 udt_name 区分,jsonb 占位列返回 False,自动退化到关键词检索。
        row = db.execute(
            "select 1 from information_schema.columns "
            "where table_name = %s and column_name = %s and udt_name = 'vector'",
            (table, column),
        ).fetchone()
    except Exception as exc:
        # 查询出错(DB 抖动)只当本次没有,不写缓存:这个缓存没有 TTL,写进 False 会让这张表的
        # 向量召回在该 worker 的整个生命周期里都关掉。现在每次检索、嵌入前都先问这一句,碰上一次
        # 瞬时错误的概率不低。
        log.debug("[_search] _vector_column_exists(%s.%s) failed: %s", table, column, exc)
        return False
    _VEC_COLUMN_CACHE[ck] = bool(row)
    return _VEC_COLUMN_CACHE[ck]


def _script_has_vectors(db, script_id: int, table: str) -> bool:
    """该剧本在这张表(或 kb_nodes 视图)里有没有可比的向量:真 pgvector 列 + 至少一行非空。

    检索前先问这一句再嵌入查询:55/78 个剧本根本没有向量、桌面版没有 pgvector(jsonb /
    null::text 占位列),此前每回合照样先把查询文本发去嵌入、再发现没东西可比 ——
    白花用户的钱、占中转站配额。与 kb.episodic._retrieve_vector 的存在性门同一思路。
    """
    column = _VEC_PRESENCE_TABLES.get(table)
    if column is None:
        raise ValueError(f"unexpected table: {table}")
    if not _vector_column_exists(db, table, column):
        return False
    key = (int(script_id), table)
    now = time.monotonic()
    cached = _SCRIPT_HAS_VEC_CACHE.get(key)
    if cached is not None:
        has, ts = cached
        if now - ts < (_HAS_VEC_TTL if has else _NO_VEC_TTL):
            return has
    try:
        row = db.execute(
            f"select 1 from {table} where script_id = %s and {column} is not null limit 1",
            (int(script_id),),
        ).fetchone()
        has = bool(row)
    except Exception as exc:
        log.debug("[_search] _script_has_vectors(%s, %s) failed: %s", script_id, table, exc)
        return False  # 查不了就当没有:退关键词,不缓存
    _SCRIPT_HAS_VEC_CACHE[key] = (has, now)
    return has


def invalidate_script_vector_presence(script_id: int) -> None:
    """写库作业结束时调用:本进程对该剧本「有没有向量」的缓存作废。"""
    for table in _VEC_PRESENCE_TABLES:
        _SCRIPT_HAS_VEC_CACHE.pop((int(script_id), table), None)


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
      2. 剧本没绑定(或没传 script_id)时:该用户当前的 BYOK 嵌入配置
      3. 该用户没配:只有 admin/vip 走平台 EMBED_*,普通用户不嵌入(返 None)

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
            # 已有剧本未绑定:改用该用户当前的嵌入配置(不是平台默认;普通用户没配就不嵌入),发出警告。
            # 降频:同一 script_id 每 query 都会重新走到这里(每轮检索都调 _embed_query)
            # → 同一句 WARNING 刷屏。该 script 的绑定状态在重新 embed 前不会变化,
            # 第一次告警已经把「需要重新拆书」的信息传达到位,进程内只报一次即可,
            # 后续降为 debug(仍留痕,不完全消音)。
            if script_id not in _UNBOUND_EMBED_WARNED:
                _UNBOUND_EMBED_WARNED.add(script_id)
                log.warning(
                    "[_search] 召回剧本 %s 没绑定 embed model,改用该用户当前的嵌入配置。"
                    "重新拆书后会绑定正确的 embed model。(本进程内此后降为 debug)",
                    script_id,
                )
            else:
                log.debug(
                    "[_search] 召回剧本 %s 没绑定 embed model,改用该用户当前的嵌入配置。",
                    script_id,
                )
    elif script_id is not None and db is None:
        log.warning(
            "[_search] _embed_query: script_id=%s 但未传 db,无法读取建库 embed meta,"
            "改用该用户当前的嵌入配置。",
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

    1. 该剧本的 document_chunks 有向量,且查询嵌入成功,走 vector 余弦距离
       ORDER BY embedding_vec <=> %s。
    2. 否则(没向量 / 嵌入失败 / 向量路在这个章窗口里一条都没有)走原来的 ILIKE 词频。

    先判断有没有向量再嵌入:零向量剧本和无 pgvector 的桌面版不再每回合白嵌入一次。
    向量路 0 行也退 ILIKE:此前零向量剧本在用户嵌入器正常时直接返回空列表,原文片段恒为空。
    """
    if not tokens:
        return []
    # 试 vector 路径 — 传 script_id + db 确保用建库时的 embed model
    vector_query = None
    if _script_has_vectors(db, script_id, "document_chunks"):
        vector_query = _embed_query(" ".join(tokens), script_id=script_id, user_id=user_id, db=db)
    if vector_query is not None:
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
            vrows = db.execute(query, (
                vector_query, script_id,
                chapter_min, chapter_min, chapter_max, chapter_max,
                vector_query, max(1, min(top_k, 8)),
            )).fetchall()
            if vrows:
                return vrows
            # 窗口内没有已嵌入的片段(部分嵌入 / 零向量)→ 落到 ILIKE,别让原文片段整段为空
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


def _worldbook_visibility(save_id: int | None) -> tuple[str, list[Any]]:
    """世界书「本档还该不该看见这条」闸:剧本侧 enabled + 存档侧 retirement。

    群反馈(行者无疆):剧本设定里**已关闭**的世界书条目照样被召回进 GM 记忆
    (截图里《食材与天财地宝》enabled=false 仍带着相关度 0.57 出现)。真因=向量
    召回层是唯一没过这道闸的世界书读路径 —— 常驻层(gm_serving/context_inject)、
    激活层(context_engine/loaders)、关键词层(retrieval/sources)、RATH(rath/engine)
    全都 `enabled=true`,只有这里裸查表;`enabled` 又只是关闭开关不脏化向量
    (test_worldbook_embed_hygiene 的约定),条目关了向量还在 → 一召一个准。
    retirement(worldbook_retire 工具的「本档停用」)是同一语义的存档级孪生,
    关键词层同样过滤,这里一并收口,免得两个「已停用」只挡住一个。
    """
    sql = "enabled = true"
    params: list[Any] = []
    if save_id is not None:
        sql += (
            " and not exists (select 1 from save_worldbook_overlays o "
            "where o.save_id = %s and o.kind = 'retirement' "
            "and o.retired_entry_id = worldbook_entries.id)"
        )
        params.append(int(save_id))
    return sql, params


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
    _OLD_GATE = "(%s::integer is null or first_revealed_chapter <= %s)"
    tokens = [t for t in query_text.split() if t][:8]
    # 先看两张表各自有没有向量:都没有就不嵌入查询(零向量剧本 / 桌面无 pgvector 不再白嵌入)
    has_vec = {
        "cards": _script_has_vectors(db, script_id, "character_cards"),
        "worldbook": _script_has_vectors(db, script_id, "worldbook_entries"),
    }
    # P4(S2):门控有两套。旧=标量 `first_revealed_chapter <= chapter_max`(2 个 chapter_max 占位符);
    # 新=前沿 reveal_clause_v2(save_id)(1 个 save_id 占位符)。用 *gate_params 展开自动适配占位符个数。
    # 门控先定下来,向量路和两处关键词兜底用同一道 —— 此前「查询没嵌入」那条兜底固定用旧标量门,
    # 「向量路空」那条跟前沿门,同一个剧本走哪条兜底就看嵌入成没成功,可见集不一致。
    from kb.reveal import _frontier_on, _frontier_shadow, _shadow_diff_log, reveal_clause_v2
    use_v2 = save_id is not None and _frontier_on(save_id)
    if use_v2:
        gate_sql, gate_params = reveal_clause_v2(int(save_id), mode, prefix="", has_public_knowledge=False, has_famous=False, progress_chapter=chapter_max)
    else:
        gate_sql, gate_params = _OLD_GATE, [chapter_max, chapter_max]

    vec = None
    if has_vec["cards"] or has_vec["worldbook"]:
        vec = _embed_query(query_text, script_id=script_id, user_id=user_id, db=db)
    if not vec:
        # 无 embedding 时退化为关键词兜底(与 _search_chunks 同策略)
        for result_key in ("cards", "worldbook"):
            out[result_key] = _ilike_entities(
                db, script_id, tokens, result_key, save_id=save_id,
                gate_sql=gate_sql, gate_params=list(gate_params),
                top_k=top_k_cards if result_key == "cards" else top_k_wb,
                query_text=query_text,
            )
        return out

    def _gate_ids(table: str, extra: str, extra_params: list, g: str, p: list) -> set:
        """某门控放行的全集 id(不带 vector/limit),供影子比对隔离纯门控差异。"""
        return {r["id"] for r in db.execute(
            f"select id from {table} where script_id=%s and embedding_vec is not null and {extra} and {g}",
            (script_id, *extra_params, *p)).fetchall()}

    def _shadow(table: str, extra: str, extra_params: list, tag: str) -> None:
        """对比旧标量门控 vs 新前沿门控的放行全集(与 vector 排序/limit 无关)。"""
        old_g, old_p = _OLD_GATE, [chapter_max, chapter_max]
        new_g, new_p = reveal_clause_v2(int(save_id), mode, prefix="", has_public_knowledge=False, has_famous=False, progress_chapter=chapter_max)
        _shadow_diff_log(tag, _gate_ids(table, extra, extra_params, old_g, old_p),
                         _gate_ids(table, extra, extra_params, new_g, new_p))

    if has_vec["cards"]:
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
                _shadow("character_cards", "enabled = true", [], "_search cards")
        except Exception:
            pass

    if has_vec["worldbook"]:
        _wb_vis_sql, _wb_vis_params = _worldbook_visibility(save_id)
        try:
            out["worldbook"] = db.execute(
                f"""
                select id, title, content, first_revealed_chapter,
                       (1 - (embedding_vec <=> %s::vector)) as score
                from worldbook_entries
                where script_id = %s
                  and embedding_vec is not null
                  -- 关闭(enabled=false)/本档停用(retirement)的条目不该再被召回。
                  and {_wb_vis_sql}
                  and {gate_sql}
                order by embedding_vec <=> %s::vector
                limit %s
                """,
                (vec, script_id, *_wb_vis_params, *gate_params, vec, max(1, min(top_k_wb, 8))),
            ).fetchall()
            if _frontier_shadow() and save_id is not None:
                _shadow("worldbook_entries", _wb_vis_sql, _wb_vis_params, "_search worldbook")
        except Exception:
            pass

    # 向量路一条都没拿到的表(这张表没向量 / 窗口内没有已嵌入的行 / 查询出错)退关键词,
    # 用同一道门控。此前只要查询嵌入成功就不再兜底:零向量或只嵌了一张表的剧本,
    # 人物卡 / 世界书召回静默归零。
    for result_key in ("cards", "worldbook"):
        if not out[result_key]:
            out[result_key] = _ilike_entities(
                db, script_id, tokens, result_key, save_id=save_id,
                gate_sql=gate_sql, gate_params=list(gate_params),
                top_k=top_k_cards if result_key == "cards" else top_k_wb,
                query_text=query_text,
            )

    return out


# result_key → (表, 名字列, 额外列)
_ENTITY_ILIKE_SPECS = {
    "cards": ("character_cards", "name", "identity, personality, appearance,"),
    "worldbook": ("worldbook_entries", "title", "content,"),
}


def _ilike_entities(
    db,
    script_id: int,
    tokens: list[str],
    result_key: str,
    *,
    save_id: int | None,
    gate_sql: str,
    gate_params: list[Any],
    top_k: int,
    query_text: str = "",
) -> list[dict[str, Any]]:
    """实体层关键词兜底。可见性闸与向量路一致。两种命中取并集:

    - 名字 / 标题 ILIKE 任一 token(token = 查询按空白切开的片段);
    - 查询原文里提到了这个名字 / 标题(至少 2 个字)。玩家输入是没有空格的中文整句,
      只按 token 匹配时整句会被当成一个 token 去比名字,几乎命不中 ——
      「我去仓库找康拉德」要召回的是名字叫「康拉德」的人物卡。
    名字越长越具体,排在前面。
    """
    query_text = (query_text or "").strip()
    if not tokens and not query_text:
        return []
    table, name_col, extra_cols = _ENTITY_ILIKE_SPECS[result_key]
    if table == "character_cards":
        vis_sql, vis_params = "enabled = true", []
    else:
        vis_sql, vis_params = _worldbook_visibility(save_id)
    where_parts = [f"{name_col} ilike %s" for _ in tokens]
    patterns: list[Any] = [f"%{t}%" for t in tokens]
    if query_text:
        where_parts.append(
            f"(char_length({name_col}) >= 2 and strpos(lower(%s::text), lower({name_col})) > 0)")
        patterns.append(query_text)
    try:
        return db.execute(
            f"select id, {name_col}, {extra_cols} first_revealed_chapter, 0.5 as score "
            f"from {table} "
            f"where script_id = %s and {vis_sql} "
            f"and ({' or '.join(where_parts)}) "
            f"and {gate_sql} "
            f"order by char_length({name_col}) desc, id "
            f"limit %s",
            # 注意:参数序必须跟占位符序:script_id → 可见性 → ilike patterns → 章节闸 → limit。
            # 旧代码把 patterns 排在 script_id 前面(占位符个数刚好对得上,故没人发现),
            # 每次都以 "%词%" 去比整数列 → 必抛 → 被下面的裸 except 吞掉:无向量时的
            # ILIKE 兜底其实一直是条死路(恒返空)。
            (script_id, *vis_params, *patterns, *gate_params, max(1, min(top_k, 8))),
        ).fetchall()
    except Exception:
        return []
