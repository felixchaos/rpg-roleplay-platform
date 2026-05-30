"""kb/canon_repo.py — Phase B 规范层(per-script,钉死只读)读写。

提取(Phase A Pass2)产出写这里;GM 服务读这里(带进度过滤防剧透 + 元知识模式)。
设计 BC_kb_schema_worldtree.md §2 + D_gm_serving.md §7/§8(决策3 已揭示集合)。
"""
from __future__ import annotations

from typing import Literal

from psycopg.types.json import Jsonb

ForeknowledgeMode = Literal["none", "partial", "omniscient"]


# ── 进度过滤(决策3:已揭示集合) ─────────────────────────────────────────────
def _reveal_clause(progress_chapter: int | None, mode: ForeknowledgeMode) -> tuple[str, list]:
    """返回 (sql 片段, 参数列表)。控制玩家在当前进度+元知识下能看到哪些规范知识。

    none        : first_revealed_chapter <= progress  或  public_knowledge
    partial     : 上 + metadata.famous=true(穿越者模糊知道大事)
    omniscient  : 不过滤
    progress=None: 不过滤(管理/编辑器视角)
    """
    if mode == "omniscient" or progress_chapter is None:
        return "true", []
    base = "(first_revealed_chapter <= %s or public_knowledge)"
    params: list = [progress_chapter]
    if mode == "partial":
        base = base[:-1] + " or (metadata->>'famous') = 'true')"
    return base, params


# ── kb_canon_entities ────────────────────────────────────────────────────────
_CANON_COLS = ("logical_key", "name", "aliases", "type", "summary", "attrs",
               "first_revealed_chapter", "public_knowledge", "importance", "metadata")


def upsert_canon_entity(db, script_id: int, logical_key: str, *, name: str, type: str,
                        aliases: list | None = None, summary: str = "", attrs: dict | None = None,
                        first_revealed_chapter: int = 0, public_knowledge: bool = False,
                        importance: int = 0, metadata: dict | None = None) -> dict:
    return db.execute(
        """
        insert into kb_canon_entities(script_id, logical_key, name, aliases, type, summary, attrs,
          first_revealed_chapter, public_knowledge, importance, metadata)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict(script_id, logical_key) do update set
          name=excluded.name, aliases=excluded.aliases, type=excluded.type, summary=excluded.summary,
          attrs=excluded.attrs, first_revealed_chapter=excluded.first_revealed_chapter,
          public_knowledge=excluded.public_knowledge, importance=excluded.importance, metadata=excluded.metadata
        returning *
        """,
        (script_id, logical_key, name, Jsonb(aliases or []), type, summary, Jsonb(attrs or {}),
         first_revealed_chapter, public_knowledge, importance, Jsonb(metadata or {})),
    ).fetchone()


def read_canon_entities(db, script_id: int, *, progress_chapter: int | None = None,
                        mode: ForeknowledgeMode = "none", entity_type: str | None = None,
                        limit: int | None = None) -> list[dict]:
    cols = ", ".join(_CANON_COLS)
    clause, params = _reveal_clause(progress_chapter, mode)
    sql = f"select {cols} from kb_canon_entities where script_id = %s and {clause}"
    args: list = [script_id, *params]
    if entity_type:
        sql += " and type = %s"
        args.append(entity_type)
    sql += " order by importance desc, logical_key"
    if limit:
        sql += " limit %s"
        args.append(limit)
    return db.execute(sql, tuple(args)).fetchall()


def lookup_canon_entity(db, script_id: int, logical_key: str, *, progress_chapter: int | None = None,
                        mode: ForeknowledgeMode = "none") -> dict | None:
    clause, params = _reveal_clause(progress_chapter, mode)
    row = db.execute(
        f"select {', '.join(_CANON_COLS)} from kb_canon_entities "
        f"where script_id=%s and logical_key=%s and {clause}",
        (script_id, logical_key, *params),
    ).fetchone()
    return row


# ── 规范世界线 DAG ───────────────────────────────────────────────────────────
def upsert_worldline(db, script_id: int, wl_key: str, *, label: str, parent_wl: str | None = None,
                     branch_at_node: str | None = None, is_primary: bool = False,
                     source: str = "extracted", metadata: dict | None = None) -> dict:
    return db.execute(
        """
        insert into script_worldlines(script_id, wl_key, label, parent_wl, branch_at_node, is_primary, source, metadata)
        values (%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict(script_id, wl_key) do update set
          label=excluded.label, parent_wl=excluded.parent_wl, branch_at_node=excluded.branch_at_node,
          is_primary=excluded.is_primary, source=excluded.source, metadata=excluded.metadata
        returning *
        """,
        (script_id, wl_key, label, parent_wl, branch_at_node, is_primary, source, Jsonb(metadata or {})),
    ).fetchone()


def upsert_worldline_node(db, script_id: int, wl_key: str, node_key: str, *, seq: int, label: str,
                          summary: str = "", chapter_min: int | None = None, chapter_max: int | None = None,
                          anchor_keys: list | None = None, must_preserve: list | None = None,
                          may_vary: list | None = None, causal_centrality: float = 0.0,
                          first_revealed_chapter: int = 0) -> dict:
    return db.execute(
        """
        insert into script_worldline_nodes(script_id, wl_key, node_key, seq, label, summary,
          chapter_min, chapter_max, anchor_keys, must_preserve, may_vary, causal_centrality, first_revealed_chapter)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict(script_id, wl_key, node_key) do update set
          seq=excluded.seq, label=excluded.label, summary=excluded.summary,
          chapter_min=excluded.chapter_min, chapter_max=excluded.chapter_max, anchor_keys=excluded.anchor_keys,
          must_preserve=excluded.must_preserve, may_vary=excluded.may_vary,
          causal_centrality=excluded.causal_centrality, first_revealed_chapter=excluded.first_revealed_chapter
        returning *
        """,
        (script_id, wl_key, node_key, seq, label, summary, chapter_min, chapter_max,
         Jsonb(anchor_keys or []), Jsonb(must_preserve or []), Jsonb(may_vary or []),
         causal_centrality, first_revealed_chapter),
    ).fetchone()


def read_worldlines(db, script_id: int) -> list[dict]:
    return db.execute(
        "select wl_key, label, parent_wl, branch_at_node, is_primary, source, metadata "
        "from script_worldlines where script_id=%s order by is_primary desc, wl_key",
        (script_id,),
    ).fetchall()


def read_worldline_nodes(db, script_id: int, wl_key: str, *, progress_chapter: int | None = None) -> list[dict]:
    sql = (
        "select wl_key, node_key, seq, label, summary, chapter_min, chapter_max, anchor_keys, "
        "must_preserve, may_vary, causal_centrality, first_revealed_chapter "
        "from script_worldline_nodes where script_id=%s and wl_key=%s"
    )
    args: list = [script_id, wl_key]
    if progress_chapter is not None:
        sql += " and first_revealed_chapter <= %s"
        args.append(progress_chapter)
    sql += " order by seq"
    return db.execute(sql, tuple(args)).fetchall()
