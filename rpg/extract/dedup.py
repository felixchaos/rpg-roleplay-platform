"""extract/dedup.py — Phase G 公开书内容指纹去重(成本核心)。

同一本公开书只提取一次,后续导入命中指纹 → 直接复用规范层(kb_canon_* + worldlines +
constant worldbook),零 LLM 成本。设计 docs/design/G_ops_cost.md §3。
"""
from __future__ import annotations

import hashlib
import re


def content_fingerprint(text: str, chapter_count: int = 0, word_count: int = 0) -> str:
    """归一正文(去空白)hash + 章数 + 字数。同书不同导入应得同指纹。"""
    norm = re.sub(r"\s+", "", text or "")
    h = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:32]
    return f"{h}:{chapter_count}:{word_count}"


def find_shareable_twin(db, fingerprint: str, *, exclude_script_id: int | None = None) -> int | None:
    """找已提取(有 kb_canon_entities)的 shareable 同指纹剧本 id。"""
    if not fingerprint:
        return None
    rows = db.execute(
        """
        select s.id from scripts s
        where s.content_fingerprint = %s and s.shareable = true
          and exists (select 1 from kb_canon_entities k where k.script_id = s.id)
        order by s.id
        """,
        (fingerprint,),
    ).fetchall()
    for r in rows:
        if exclude_script_id and r["id"] == exclude_script_id:
            continue
        return r["id"]
    return None


def copy_canon_layer(db, src_script_id: int, dst_script_id: int) -> dict:
    """把规范层从 src 复制到 dst(零 LLM)。kb_canon_entities + worldlines + nodes + timeline。"""
    n_ent = db.execute(
        """
        insert into kb_canon_entities(script_id, logical_key, name, aliases, type, summary, attrs,
          first_revealed_chapter, public_knowledge, importance, metadata, embedding)
        select %s, logical_key, name, aliases, type, summary, attrs,
          first_revealed_chapter, public_knowledge, importance, metadata, embedding
        from kb_canon_entities where script_id = %s
        on conflict(script_id, logical_key) do nothing
        """,
        (dst_script_id, src_script_id),
    ).rowcount
    db.execute(
        "insert into script_worldlines(script_id, wl_key, label, parent_wl, branch_at_node, is_primary, source, metadata) "
        "select %s, wl_key, label, parent_wl, branch_at_node, is_primary, source, metadata from script_worldlines where script_id=%s "
        "on conflict(script_id, wl_key) do nothing",
        (dst_script_id, src_script_id),
    )
    db.execute(
        "insert into script_worldline_nodes(script_id, wl_key, node_key, seq, label, summary, chapter_min, chapter_max, "
        "anchor_keys, must_preserve, may_vary, causal_centrality, first_revealed_chapter) "
        "select %s, wl_key, node_key, seq, label, summary, chapter_min, chapter_max, anchor_keys, must_preserve, may_vary, "
        "causal_centrality, first_revealed_chapter from script_worldline_nodes where script_id=%s "
        "on conflict(script_id, wl_key, node_key) do nothing",
        (dst_script_id, src_script_id),
    )
    db.execute(
        "insert into script_timeline_anchors(script_id, story_phase, story_time_label, chapter_min, chapter_max, chapter_count, confidence) "
        "select %s, story_phase, story_time_label, chapter_min, chapter_max, chapter_count, confidence from script_timeline_anchors where script_id=%s "
        "on conflict(script_id, story_phase, story_time_label) do nothing",
        (dst_script_id, src_script_id),
    )
    return {"entities_copied": n_ent, "reused_from": src_script_id}


def update_fingerprint(db, script_id: int, fingerprint: str, *, shareable: bool | None = None) -> None:
    if shareable is None:
        db.execute("update scripts set content_fingerprint=%s where id=%s", (fingerprint, script_id))
    else:
        db.execute("update scripts set content_fingerprint=%s, shareable=%s where id=%s",
                   (fingerprint, shareable, script_id))
