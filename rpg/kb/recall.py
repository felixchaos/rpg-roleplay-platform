"""kb.recall — P5 召回统一层(时间感知知识库)。

设计:docs/design/O_temporal_kb_unification.md §4。
现状:召回散落在 retrieval.retrieve_context(600+ 行 8 步)/ _search / loaders / formatters / context_inject
多入口。本模块提供【单一】确定性召回入口 recall():查 kb_nodes 视图(canon/角色/世界书三源 UNION ALL)
+ reveal_clause_v2 前沿门控 + derived_progress_chapter 章窗口上界,统一打分去重。

**保守落地(本轮)**:flag 门控(RPG_TKB_RECALL 默认 off),默认走旧 retrieve_context、新路仅 flag on 才返回;
影子模式双跑 diff 落日志。绝不硬删旧路径(_split_anchor_pending / retrieve_context 全步骤原样保留)。
graph 扩边(kb_edges)本轮降级(W_graph*0),退役 _split_anchor_pending 留后。

铁律:全确定性,不调 LLM(守 feedback_harness_determinism);剧透门控单一真源 reveal_clause_v2;
章窗口走 derived_progress_chapter(绝不读可能被旧猜章器冲高的 worldline 标量);异常降级旧路。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("kb.recall")

_TRUTHY = ("1", "true", "on", "yes")

# 打分权重(env 可覆盖 RPG_RECALL_W_*)。graph 本轮降级=0,不参与。
W_VEC = float(os.environ.get("RPG_RECALL_W_VEC", "0.45"))
W_KW = float(os.environ.get("RPG_RECALL_W_KW", "0.25"))
W_RECENCY = float(os.environ.get("RPG_RECALL_W_RECENCY", "0.10"))

_KB_BODY_MARKER = "=== 知识库召回(统一层) ==="
_ANCHOR_MARKER = "=== 世界线收束·接下来的锚点 ==="  # 契约护栏:必须出现在渲染串里供 _split_anchor_pending


# ── flag 闸(仿 kb.reveal,独立 env) ─────────────────────────────────────────
def _recall_on(save_id: int | None = None) -> bool:
    """P5 统一召回总闸。RPG_TKB_RECALL 默认 off;RPG_TKB_RECALL_SAVES 白名单按 save 灰度。"""
    if os.environ.get("RPG_TKB_RECALL", "off").strip().lower() not in _TRUTHY:
        return False
    saves = os.environ.get("RPG_TKB_RECALL_SAVES", "").strip()
    if saves and save_id is not None:
        return str(int(save_id)) in {s.strip() for s in saves.split(",") if s.strip()}
    # 「只对新游戏开」闸(与 _frontier_on 同语义):RPG_TKB_RECALL_MIN_SAVE_ID=N → 仅 save_id>=N 走统一召回。
    min_id = os.environ.get("RPG_TKB_RECALL_MIN_SAVE_ID", "").strip()
    if min_id and save_id is not None:
        try:
            if int(save_id) < int(min_id):
                return False
        except (TypeError, ValueError):
            pass
    return True


def _recall_shadow() -> bool:
    """影子比对开关 RPG_TKB_RECALL_SHADOW(默认 off):双跑 diff 落日志,绝不改返回值。"""
    return os.environ.get("RPG_TKB_RECALL_SHADOW", "off").strip().lower() in _TRUTHY


# ── 纯打分函数(无 DB,可单测) ───────────────────────────────────────────────
def _clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def _recency(chapter: Any, ceil_chap: int) -> float:
    """章节越接近当前进度上界,recency 越高。无章号→0.5(中性)。"""
    try:
        ch = int(chapter)
    except (TypeError, ValueError):
        return 0.5
    if ch <= 0 or ceil_chap <= 0:
        return 0.5
    if ch >= ceil_chap:
        return 1.0
    # 线性:距 ceil 越近越高,最远(ch=1)给 0.2 底
    return _clamp01(0.2 + 0.8 * (ch / max(1, ceil_chap)))


def _priority_norm(importance: Any, max_importance: int) -> float:
    try:
        imp = int(importance or 0)
    except (TypeError, ValueError):
        imp = 0
    return _clamp01(imp / max(1, max_importance))


def _keyword_hits(node: dict, tokens: list[str]) -> int:
    """node 的 name/aliases/body 命中多少个 query token(去重计数)。"""
    if not tokens:
        return 0
    hay = " ".join(str(node.get(k) or "") for k in ("name", "body")).lower()
    aliases = node.get("aliases")
    if isinstance(aliases, list):
        hay += " " + " ".join(str(a) for a in aliases).lower()
    return sum(1 for t in tokens if t and t.lower() in hay)


def score(node: dict, *, ceil_chap: int, max_importance: int) -> float:
    """统一打分(确定性)。vscore 来自 SQL 余弦(无则 0);kw 命中按 priority 归一加权;recency 按章距。
    graph 本轮降级(不计)。各项独立加权,缺一项不报错。"""
    s_vec = _clamp01(float(node.get("vscore") or 0.0))
    s_kw = _clamp01((node.get("kw_hits") or 0) / 3.0) * _priority_norm(
        node.get("importance"), max_importance)
    s_rec = _recency(node.get("first_revealed_chapter"), ceil_chap)
    return W_VEC * s_vec + W_KW * s_kw + W_RECENCY * s_rec


@dataclass
class RecallResult:
    candidates: list[dict] = field(default_factory=list)  # 去重打分后的 kb_nodes(供渲染/影子)
    chunks: list[dict] = field(default_factory=list)       # 原文片段
    ceil_chap: int = 1
    tokens_used: int = 0


_KN_COLS = ("node_kind", "node_key", "name", "subtype", "body",
            "first_revealed_chapter", "importance", "aliases", "reveal_anchor_key")


def recall(save_id: int, query: str, *, mode: str = "none", token_budget: int = 6000,
           progress_chapter: int | None = None, db=None) -> RecallResult:
    """统一召回:kb_nodes(向量+关键词,reveal_clause_v2 门控)+ 原文 chunks(章窗口)。
    确定性、不调 LLM。progress_chapter 给定则用作章窗口上界,否则 derived_progress_chapter。"""
    from kb.reveal import derived_progress_chapter, reveal_clause_v2

    def _run(_db) -> RecallResult:
        ceil_chap = int(progress_chapter) if progress_chapter else derived_progress_chapter(
            save_id, db=_db)
        ceil_chap = max(1, ceil_chap)
        # 传进度章给前沿门控:到达第 N 章 ⇒ 1..N 章 kb_nodes 可见(开局=第1章/序章可见),
        # 不被空前沿误藏(修复「序章人物/世界知识全无,GM 自由发挥脱离原著」)。
        clause, params = reveal_clause_v2(int(save_id), mode, prefix="kn.", progress_chapter=ceil_chap)  # 三源统一门控
        cols = ", ".join("kn." + c for c in _KN_COLS)
        # 解析 script_id 一次:embed_query 必须用建库锁定的 embedder(否则向量空间错乱,
        # 静默错召回);_search_chunks 也复用。NULL(酒馆档)→ None,下游各自安全降级。
        _srow = _db.execute("select script_id, user_id from game_saves where id=%s", (int(save_id),)).fetchone()
        _script_id = (_srow or {}).get("script_id") if _srow else None
        # 召回侧 query 必须用建库锁定的 embedder(force_api_id/model),而该 provider 是 BYOK —
        # 取 key 需 user_id。原来没传 → resolve_api_key(None,..) 无 key → 降级 vertex(异空间)
        # 或 'unknown api_id' 静默退化为关键词召回。传存档属主 id 即可命中其凭证。
        _owner_id = (_srow or {}).get("user_id") if _srow else None

        from platform_app.knowledge._utils import _query_tokens
        tokens = _query_tokens(query) or []

        by_key: dict[tuple, dict] = {}

        def _merge(rows: list[dict], vscore_key: str | None) -> None:
            for r in rows:
                k = (r["node_kind"], r["node_key"])
                node = by_key.get(k) or dict(r)
                if vscore_key and r.get(vscore_key) is not None:
                    node["vscore"] = max(float(node.get("vscore") or 0.0), float(r[vscore_key]))
                node["kw_hits"] = _keyword_hits(node, tokens)
                by_key[k] = node

        # 向量路(有 embedder 才走;空间不一致/无 key → 静默跳过,降级关键词)
        vec = None
        try:
            from platform_app.knowledge._search import _embed_query
            vec = _embed_query(query, script_id=_script_id, user_id=_owner_id, db=_db) if query else None
        except Exception as exc:
            log.debug("[recall] embed 跳过: %s", exc)
        if vec:
            try:
                vrows = _db.execute(
                    f"select {cols}, (1 - (kn.embedding_vec <=> %s::vector)) as vscore "
                    f"from kb_nodes kn where kn.script_id = (select script_id from game_saves where id=%s) "
                    f"and kn.embedding_vec is not null and {clause} "
                    f"order by kn.embedding_vec <=> %s::vector limit 24",
                    (vec, int(save_id), *params, vec),
                ).fetchall()
                _merge(vrows, "vscore")
            except Exception as exc:
                log.debug("[recall] 向量路跳过: %s", exc)

        # 关键词路(任一 token 命中 name/body → OR,非拼接;对齐 _search_chunks 语义)
        toks = [t for t in tokens[:8] if t]
        if toks:
            ors = " or ".join(["kn.name ilike %s or kn.body ilike %s"] * len(toks))
            like_params: list = []
            for t in toks:
                p = f"%{t}%"
                like_params += [p, p]
            try:
                krows = _db.execute(
                    f"select {cols} from kb_nodes kn "
                    f"where kn.script_id = (select script_id from game_saves where id=%s) "
                    f"and ({ors}) and {clause} "
                    f"order by kn.importance desc limit 24",
                    (int(save_id), *like_params, *params),
                ).fetchall()
                _merge(krows, None)
            except Exception as exc:
                log.debug("[recall] 关键词路跳过: %s", exc)

        cands = list(by_key.values())
        max_imp = max((int(c.get("importance") or 0) for c in cands), default=1)
        for c in cands:
            c["score"] = score(c, ceil_chap=ceil_chap, max_importance=max_imp)
        cands.sort(key=lambda c: c["score"], reverse=True)

        # token 预算贪心(粗估 len//2)。
        used, picked = 0, []
        for c in cands:
            cost = (len(str(c.get("name") or "")) + len(str(c.get("body") or ""))) // 2 + 8
            if used + cost > token_budget:
                continue
            picked.append(c)
            used += cost

        # 原文片段(章窗口,复用 _search_chunks)
        chunk_rows = []
        if _script_id:
            try:
                from platform_app.knowledge._search import _search_chunks
                chunk_rows = _search_chunks(_db, int(_script_id), tokens, None, ceil_chap, 4, user_id=_owner_id) or []
            except Exception as exc:
                log.debug("[recall] chunks 跳过: %s", exc)

        return RecallResult(candidates=picked, chunks=chunk_rows, ceil_chap=ceil_chap,
                            tokens_used=used)

    if db is not None:
        return _run(db)
    from platform_app.db import connect, init_db
    init_db()
    with connect() as _db2:
        return _run(_db2)


# ── 渲染回旧路同构字符串(契约保持) ─────────────────────────────────────────
def _render_kb_body(result: RecallResult) -> str:
    if not result.candidates and not result.chunks:
        return ""
    parts: list[str] = []
    if result.candidates:
        lines = [_KB_BODY_MARKER,
                 f"(按当前进度 ch≤{result.ceil_chap} 的揭示集合召回,已防剧透)"]
        for c in result.candidates[:12]:
            body = (str(c.get("body") or "")).strip().replace("\n", " ")[:220]
            kind = {"canon_entity": "词条", "character": "角色", "worldbook": "世界书"}.get(
                c.get("node_kind"), c.get("node_kind") or "")
            lines.append(f"· [{kind}] {c.get('name')}（相关度 {c.get('score', 0):.2f}）"
                         + (f"：{body}" if body else ""))
        parts.append("\n".join(lines))
    if result.chunks:
        clines = ["=== Postgres 原文片段 ==="]
        for r in result.chunks:
            clines.append(f"[第{r.get('chapter_index')}章片段]\n{str(r.get('content') or '')[:360].strip()}")
        parts.append("\n\n".join(clines))
    return "\n\n".join(parts)


def render_compat_string(result: RecallResult, anchor_section: str = "") -> str:
    """渲染回与 retrieve_context 同构的字符串。anchor_section = 旧路 _split_anchor_pending 切出的
    「世界线收束·接下来的锚点」段【原样复用】(契约关键段不重造,零风险);KB body 由 recall 提供。"""
    parts: list[str] = []
    body = _render_kb_body(result)
    if body:
        parts.append(body)
    if anchor_section:  # 必含 _ANCHOR_MARKER(由旧路产出),供 NovelRetrievalProvider 切割
        parts.append(anchor_section)
    return "\n\n".join(parts)


# ── retrieve_fn 契约兼容包装(唯一接入点,签名 == retrieval.retrieve_context) ──
def retrieve_fn_compat(query, *, state=None, user_id=None, script_id=None) -> str:
    """flag off → 委托旧 retrieve_context(进度副作用照常);flag on → recall 渲染串(锚点段复用旧路);
    shadow → 双跑 diff 落日志、返回旧路。异常一律降级旧路(绝不破回合)。"""
    from retrieval import retrieve_context

    def _old() -> str:
        return retrieve_context(query, state=state, user_id=user_id, script_id=script_id)

    # S3:先用【无 DB】的全局闸早退 —— flag 全 off 时绝不解析 save_id(那会多打一次 runtime_checkouts
    # 连接),保住「flag-off 零变化」连开销维度也成立。仅在激活后才解析 save_id 走带白名单的 _recall_on。
    on_global = os.environ.get("RPG_TKB_RECALL", "off").strip().lower() in _TRUTHY
    shadow = _recall_shadow()
    if not on_global and not shadow:
        return _old()
    try:
        from retrieval import _resolve_save_id_from_user
        save_id = _resolve_save_id_from_user(user_id) if user_id else None
    except Exception:
        save_id = None
    on = _recall_on(save_id)
    if (not on and not shadow) or save_id is None:
        return _old()  # 未命中白名单 / 无存档上下文 → 旧路

    old_text = _old()  # 始终先跑(承载进度 materialize 副作用 + 提供锚点段)
    try:
        from context_providers.novel import _read_progress_and_mode, _split_anchor_pending
        progress, mode = _read_progress_and_mode(state, save_id)
        anchor_section, _ = _split_anchor_pending(old_text)
        result = recall(save_id, query or "", mode=mode, progress_chapter=progress)
        new_text = render_compat_string(result, anchor_section)
    except Exception as exc:
        log.warning("[recall] 新路异常,降级旧路: %s", exc)
        return old_text

    if shadow:
        try:
            from kb.reveal import _shadow_diff_log
            from context_providers.novel import _split_anchor_pending as _split
            _, old_body = _split(old_text)
            # S4:传【纯长度字符串】集合(无前缀),否则 old/new 永不相等、每回合误报 warning。
            _shadow_diff_log("recall body len",
                             {str(len(old_body))}, {str(len(_render_kb_body(result)))})
        except Exception:
            pass

    # S1:flag on 但 recall 三路全 miss(如 curator 空 query)→ 召回为空会丢光旧 RAG body,
    # 降级旧路(old_text 已含进度副作用,保留)。绝不让 GM 拿到空知识库。
    if on and not result.candidates and not result.chunks:
        log.debug("[recall] 新路召回为空,降级旧路(save_id=%s)", save_id)
        return old_text
    return new_text if on else old_text
