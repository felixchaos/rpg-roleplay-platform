"""kb/episodic.py — 永恒记忆 · 情景召回(玩家自己的游戏历史)。

把存档域 COW 事件表 kb_events 向量化 + 按当前情境语义召回 top-k。与原著 RAG(检索剧本正文)
正交:这是"玩家创造的过去时态"。检索沿 born_commit 谱系 CTE 过滤 → **分支隔离天然**:一个分支
只召回自己血缘的事件(rewind / 平行线不串味)。绝不写 script 域、绝不写扁平 save_history_anchors。

嵌入走用户 embed 偏好(廉价 embedder),失败 / 未配置 / pgvector 不可用时降级到
**确定性关键词召回**(稀有 token 打分,零嵌入依赖 —— 生产实证 77k 事件仅 0.65% 有嵌入:
平台 embed 地区受限、BYOK embedder 几乎无人配,语义路径对绝大多数用户是死的,
确定性兜底才是主路径),绝不阻断回合。写嵌入在回合之外异步做,不进 GM 关键路径事务。
"""
from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

_EMBED_BATCH = 16  # 每次后处理最多补嵌入多少条(防一回合事件过多拖慢)

# ── 确定性关键词召回(无 embedder 时的主路径) ──────────────────────────
_CJK_RUN = re.compile(r"[一-鿿]{2,}")
# 查询侧停用 gram:中文高频功能词/叙事套话,任何语料里都常见,不携带情景信息。
# 语料侧的常见 gram 由 df 过滤兜(见 _score_events),这里只挡查询侧最常见的。
_STOP_GRAMS = frozenset((
    "一个", "什么", "没有", "知道", "现在", "已经", "自己", "他们", "我们", "你们",
    "这个", "那个", "时候", "可以", "不是", "就是", "但是", "然后", "开始", "继续",
    "出现", "看到", "听到", "感到", "觉得", "地方", "东西", "事情", "这里", "那里",
    "怎么", "为什", "么样", "一下", "一起", "之前", "之后", "还是", "或者", "如果",
    "告诉", "打算", "准备", "想起", "回想",
))
_VECTOR_SCORE_FLOOR = 0.45  # 向量路径相关性下限:top-k 无阈值会把不相关往事硬塞满 5 条
_KEYWORD_SCORE_FLOOR = 3    # 关键词路径:单三字 gram(如人名)=3 可过;单二字 gram(=2)永不过
_KEYWORD_CORPUS_CAP = 3000  # 每次最多拉多少条事件参与打分(防超长档拖慢;近因优先)


def _query_grams(text: str) -> set[str]:
    """从查询文本提取 2/3 字 CJK gram(滑窗),去停用。中文无空格,滑窗 gram 是
    无分词依赖的确定性折衷;常见 gram 靠停用表+语料 df 过滤双重压制。"""
    grams: set[str] = set()
    for run in _CJK_RUN.findall(text or ""):
        for n in (2, 3):
            for i in range(len(run) - n + 1):
                g = run[i:i + n]
                if g not in _STOP_GRAMS:
                    grams.add(g)
    return grams


def _score_events(query_text: str, events: list[dict]) -> list[tuple[int, dict]]:
    """纯函数:稀有 gram 重叠打分。返回 [(score, event)] 仅含过阈值者,分高在前。

    df 过滤(穷人版 IDF):gram 在语料 >25% 事件中出现 → 太常见不计分(该档全程都在
    沼泽,「沼泽」不携带区分信息;真正稀有的人名/物名才是召回信号)。
    阈值宁漏勿误:单个二字 gram 永不足以召回(太弱),单个三字 gram(人名典型长度)可以。
    """
    grams = _query_grams(query_text)
    if not grams or not events:
        return []
    texts: list[str] = []
    for e in events:
        parts = [str(e.get("summary") or "")]
        loc = str(e.get("location") or "")
        if loc:
            parts.append(loc)
        p = e.get("participants")
        if isinstance(p, (list, tuple)):
            parts.extend(str(x) for x in p)
        elif p:
            parts.append(str(p))
        texts.append(" ".join(parts))
    n = len(texts)
    df_cap = max(2, int(n * 0.25))
    rare: dict[str, int] = {}
    for g in grams:
        df = sum(1 for t in texts if g in t)
        if 0 < df <= df_cap:
            rare[g] = df
    if not rare:
        return []
    scored: list[tuple[int, dict]] = []
    for t, e in zip(texts, events):
        hit = [g for g in rare if g in t]
        if not hit:
            continue
        # 三字 gram 内嵌的二字 gram 不重复计分(「康拉德」命中则「康拉」「拉德」不再加)
        hit.sort(key=len, reverse=True)
        kept: list[str] = []
        for g in hit:
            if not any(g in k for k in kept):
                kept.append(g)
        score = sum(len(g) for g in kept)
        if score >= _KEYWORD_SCORE_FLOOR:
            scored.append((score, e))
    scored.sort(key=lambda se: (-se[0], -int(se[1].get("id") or 0)))
    return scored


def embed_pending_events(save_id: int, user_id: int | None, *, limit: int = _EMBED_BATCH) -> int:
    """把本存档尚未嵌入的 kb_events(embedding_vec IS NULL)补嵌入。回合后 fire-and-forget 调。
    返回成功嵌入条数;无 embedder / pgvector 时返 0(静默,保持 NULL 等下次)。"""
    if not save_id:
        return 0
    try:
        from platform_app.db import connect, init_db
        from platform_app.knowledge.embedding import embed_query
        init_db()
        with connect() as db:
            rows = db.execute(
                "select id, summary from kb_events "
                "where save_id=%s and embedding_vec is null and coalesce(summary,'')<>'' "
                "order by id desc limit %s",
                (int(save_id), int(limit)),
            ).fetchall()
        n = 0
        for r in rows or []:
            vec = embed_query(str(r.get("summary") or ""), user_id)  # 用户 embed 偏好
            if not vec:
                break  # 无可用 embedder → 整批放弃(下次或换 embedder 再补),不空转
            with connect() as db:
                db.execute(
                    "update kb_events set embedding_vec=%s::vector where id=%s and save_id=%s",
                    (vec, int(r["id"]), int(save_id)),
                )
                if hasattr(db, "commit"):
                    db.commit()
            n += 1
        return n
    except Exception as exc:
        log.warning("[episodic] embed_pending_events skip: %s", exc)
        return 0


def retrieve_episodic(
    save_id: int, commit_id: int, user_id: int | None, query_text: str, *, k: int = 5,
) -> list[dict]:
    """沿当前分支谱系召回 top-k 相关历史事件。向量优先(带相关性下限,不硬塞不相关往事),
    无 embedder / pgvector / 无嵌入数据 / 向量全不过阈 → **确定性关键词召回兜底**
    (生产主路径:全库嵌入覆盖 0.65%)。两路都空 → 返 [](默认休眠,不注入)。

    返回 [{logical_key, summary, story_time, location, participants, score}],score 越高越相关。"""
    if not (save_id and commit_id and (query_text or "").strip()):
        return []
    try:
        from kb.live_repo import _ANCESTRY
        from platform_app.db import connect, init_db
        init_db()
        # 正确性门:本存档一条嵌入事件都没有(生产常态,全库覆盖 0.65%)→ 嵌入查询向量
        # 毫无意义,跳过 embed_query —— 否则每回合白挨最多两次注定失败的 HTTP(用户
        # embedder+admin fallback 皆 400)拖慢回合。有嵌入才值得付这次调用。
        with connect() as db:
            _has_vec = db.execute(
                "select 1 from kb_events where save_id=%s and embedding_vec is not null limit 1",
                (int(save_id),),
            ).fetchone()
        qv = None
        if _has_vec:
            try:
                from platform_app.knowledge.embedding import embed_query
                qv = embed_query(query_text, user_id)
            except Exception:
                qv = None
        if qv:
            sql = _ANCESTRY + """
            select logical_key, summary, story_time, location, participants,
                   (1 - (embedding_vec <=> %(qv)s::vector)) as score
            from kb_events
            where save_id = %(save)s
              and born_commit in (select cid from ancestry)
              and retired_at_commit is null
              and embedding_vec is not null
            order by embedding_vec <=> %(qv)s::vector
            limit %(k)s
            """
            with connect() as db:
                rows = db.execute(
                    sql, {"commit": int(commit_id), "save": int(save_id), "qv": qv, "k": int(k)},
                ).fetchall()
            hits = [dict(r) for r in (rows or [])
                    if float(r.get("score") or 0.0) >= _VECTOR_SCORE_FLOOR]
            if hits:
                return hits
        return retrieve_episodic_keyword(save_id, commit_id, query_text, k=k)
    except Exception as exc:
        log.warning("[episodic] retrieve_episodic skip: %s", exc)
        return []


def retrieve_episodic_keyword(
    save_id: int, commit_id: int, query_text: str, *, k: int = 5,
) -> list[dict]:
    """确定性关键词召回:谱系过滤后在 Python 里稀有 gram 打分(见 _score_events)。
    零嵌入依赖、零 LLM;打分过阈才返回(宁漏勿误,空结果=不注入)。"""
    if not (save_id and commit_id and (query_text or "").strip()):
        return []
    try:
        from kb.live_repo import _ANCESTRY
        from platform_app.db import connect, init_db
        init_db()
        sql = _ANCESTRY + """
        select id, logical_key, summary, story_time, location, participants
        from kb_events
        where save_id = %(save)s
          and born_commit in (select cid from ancestry)
          and retired_at_commit is null
          and coalesce(summary, '') <> ''
        order by id desc
        limit %(cap)s
        """
        with connect() as db:
            rows = db.execute(
                sql, {"commit": int(commit_id), "save": int(save_id), "cap": _KEYWORD_CORPUS_CAP},
            ).fetchall()
        events = [dict(r) for r in (rows or [])]
        scored = _score_events(query_text, events)
        out: list[dict] = []
        for score, e in scored[: max(1, int(k))]:
            e = dict(e)
            e.pop("id", None)
            e["score"] = score
            out.append(e)
        return out
    except Exception as exc:
        log.warning("[episodic] retrieve_episodic_keyword skip: %s", exc)
        return []
