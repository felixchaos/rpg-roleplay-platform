"""task 51: Vertex text-embedding-004 + pgvector 双层检索。

设计思路(基于 LightRAG / novel2graph 双层检索范式):
- 块层(document_chunks.embedding_vec): 全书切块的向量,用于 RAG 语义召回
- 实体层(character_cards.embedding_vec, worldbook_entries.embedding_vec):
  角色/世界书条目的向量,GM 提到人名时按向量找完整卡片

embedding model: Google `text-embedding-004` (768 维,多语言含中文)
batch size: 100 chunks/请求(API 限 250,留 buffer)
存储: pgvector(已 brew install + CREATE EXTENSION)
查询: `embedding_vec <=> query_vec` cosine distance + ivfflat 索引

入口:
- `embed_query(text)` → str(vector) 给 `_search._embed_query` 用
- `embed_script(script_id, user_id)` → 后台 batch embed 全书 chunks + cards + worldbook
- `embed_status(script_id)` → 进度查询
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

EMBED_MODEL = "text-embedding-004"
EMBED_DIM = 768
# Vertex text-embedding-004 限制:**单请求总 token ≤ 20000**(不是 250 项)。
# 中文 chunk 平均 ~200 token,100 项已经超过 20K → 400 INVALID_ARGUMENT。
# 减到 30 项 × ~600 char ≈ 9000 tokens,留足 50% buffer 处理长 chunk。
BATCH_SIZE = 30
# 每个 chunk 文本上限(char),配合 batch_size 控制总 token。
# Vertex 中文 ~1 char/0.5 token,2400 char ≈ 1200 token;30 × 1200 = 36000 仍超。
# 改成 1200 char/chunk ≈ 600 token;30 × 600 = 18000 安全。
PER_CHUNK_CHAR_LIMIT = 1200
# 进程内 cache,避免 ChatPipeline 每次 _embed_query 都重新 import vertex SDK
_VERTEX_CLIENT_CACHE: dict[str, Any] = {}
_EMBED_QUEUE_RUNNING: dict[int, bool] = {}  # script_id → 是否在跑


def _get_vertex_client():
    """复用 GameMaster vertex backend 的 client (共享 vertex_sa.json 凭证)。
    放 cache 避免每次重新 init Google SDK。"""
    if "client" in _VERTEX_CLIENT_CACHE:
        return _VERTEX_CLIENT_CACHE["client"]
    try:
        from google import genai
        import json
        import os
        from pathlib import Path

        # 找 vertex_sa.json:env > rpg/ > project root
        sa_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or ""
        if not sa_path or not os.path.exists(sa_path):
            for p in [
                Path(__file__).resolve().parents[2] / "vertex_sa.json",   # rpg/
                Path(__file__).resolve().parents[3] / "vertex_sa.json",   # project root
            ]:
                if p.exists():
                    sa_path = str(p)
                    break
        if not sa_path or not os.path.exists(sa_path):
            log.warning("[embedding] vertex_sa.json not found")
            _VERTEX_CLIENT_CACHE["client"] = None
            return None
        with open(sa_path) as f:
            sa = json.load(f)
        # Vertex AI text-embedding 走 location='us-central1' 比 global 稳定
        client = genai.Client(vertexai=True, project=sa["project_id"], location="us-central1")
        _VERTEX_CLIENT_CACHE["client"] = client
        return client
    except Exception as e:
        log.warning("[embedding] vertex client init failed: %s", e)
        _VERTEX_CLIENT_CACHE["client"] = None
        return None


def _embed_batch(texts: list[str]) -> list[list[float]] | None:
    """调 Vertex text-embedding-004,返 768 维向量列表。失败返 None。"""
    if not texts:
        return []
    client = _get_vertex_client()
    if client is None:
        return None
    try:
        from google.genai import types
        # Vertex embedding API 一次接受 list[str],返回 list[ContentEmbedding]
        resp = client.models.embed_content(
            model=EMBED_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_DOCUMENT",  # 文档侧用 RETRIEVAL_DOCUMENT,查询侧用 RETRIEVAL_QUERY
                output_dimensionality=EMBED_DIM,
            ),
        )
        return [list(e.values) for e in resp.embeddings]
    except Exception as e:
        log.warning("[embedding] batch embed failed (%d items): %s", len(texts), e)
        return None


def embed_query(text: str) -> str | None:
    """task 51: query 文本 → 768 维向量字符串。
    `_search._embed_query` 的 production 实现。失败返 None 自动 fallback ILIKE。

    与 _embed_batch 区别:task_type=RETRIEVAL_QUERY(更适合 query 侧),只 embed 1 个。
    """
    text = (text or "").strip()
    if not text:
        return None
    client = _get_vertex_client()
    if client is None:
        return None
    try:
        from google.genai import types
        resp = client.models.embed_content(
            model=EMBED_MODEL,
            contents=[text],
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=EMBED_DIM,
            ),
        )
        vec = list(resp.embeddings[0].values)
        # pgvector 接受 "[v1,v2,...]" 字符串
        return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"
    except Exception as e:
        log.warning("[embedding] embed_query failed: %s", e)
        return None


def _vec_literal(v: list[float]) -> str:
    """list[float] → pgvector "[..]" 字面量。"""
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def embed_status(script_id: int) -> dict[str, Any]:
    """查询某剧本的 embedding 进度。"""
    from ..db import connect
    with connect() as db:
        chunks_total = db.execute(
            "select count(*) as c from document_chunks where script_id = %s",
            (script_id,),
        ).fetchone()["c"]
        chunks_done = db.execute(
            "select count(*) as c from document_chunks where script_id = %s and embedding_vec is not null",
            (script_id,),
        ).fetchone()["c"]
        cards_total = db.execute(
            "select count(*) as c from character_cards where script_id = %s",
            (script_id,),
        ).fetchone()["c"]
        cards_done = db.execute(
            "select count(*) as c from character_cards where script_id = %s and embedding_vec is not null",
            (script_id,),
        ).fetchone()["c"]
        wb_total = db.execute(
            "select count(*) as c from worldbook_entries where script_id = %s",
            (script_id,),
        ).fetchone()["c"]
        wb_done = db.execute(
            "select count(*) as c from worldbook_entries where script_id = %s and embedding_vec is not null",
            (script_id,),
        ).fetchone()["c"]
    return {
        "running": _EMBED_QUEUE_RUNNING.get(script_id, False),
        "chunks": {"done": chunks_done, "total": chunks_total},
        "cards": {"done": cards_done, "total": cards_total},
        "worldbook": {"done": wb_done, "total": wb_total},
        "model": EMBED_MODEL,
        "dim": EMBED_DIM,
    }


def _embed_chunks_loop(script_id: int, user_id: int) -> None:
    """后台线程:遍历 document_chunks 分批调 Vertex,写 embedding_vec。"""
    from ..db import connect
    log.info("[embedding] start chunks: script_id=%s user=%s", script_id, user_id)

    while True:
        with connect() as db:
            # 拉一批未 embed 的(只拉 id+content,内存友好)
            rows = db.execute(
                "select id, content from document_chunks "
                "where script_id = %s and embedding_vec is null "
                "order by chapter_index, chunk_index limit %s",
                (script_id, BATCH_SIZE),
            ).fetchall()
        if not rows:
            break

        texts = [r["content"][:PER_CHUNK_CHAR_LIMIT] for r in rows]  # 见模块顶 PER_CHUNK_CHAR_LIMIT 注释
        vecs = _embed_batch(texts)
        if vecs is None:
            log.warning("[embedding] batch failed, sleeping 30s then retry")
            time.sleep(30)
            continue
        if len(vecs) != len(rows):
            log.warning("[embedding] vec count mismatch: got %d expected %d", len(vecs), len(rows))
            break

        with connect() as db:
            for r, v in zip(rows, vecs):
                db.execute(
                    "update document_chunks set embedding_vec = %s::vector, embedded_at = now() where id = %s",
                    (_vec_literal(v), r["id"]),
                )
        log.info("[embedding] chunks +%d (script_id=%s)", len(rows), script_id)

    # entity 层:character_cards
    with connect() as db:
        cards = db.execute(
            "select id, name, identity, personality, appearance from character_cards "
            "where script_id = %s and embedding_vec is null",
            (script_id,),
        ).fetchall()
    if cards:
        for i in range(0, len(cards), BATCH_SIZE):
            batch = cards[i:i+BATCH_SIZE]
            texts = [
                # 拼接成"角色档案",embedding 更准
                f"{c['name']}。{c.get('identity') or ''}。{(c.get('personality') or '')[:1000]}。{(c.get('appearance') or '')[:500]}"
                for c in batch
            ]
            vecs = _embed_batch(texts)
            if vecs is None:
                continue
            with connect() as db:
                for c, v in zip(batch, vecs):
                    db.execute(
                        "update character_cards set embedding_vec = %s::vector, embedded_at = now() where id = %s",
                        (_vec_literal(v), c["id"]),
                    )
        log.info("[embedding] cards +%d (script_id=%s)", len(cards), script_id)

    # entity 层:worldbook_entries
    with connect() as db:
        wb = db.execute(
            "select id, title, content from worldbook_entries "
            "where script_id = %s and embedding_vec is null",
            (script_id,),
        ).fetchall()
    if wb:
        for i in range(0, len(wb), BATCH_SIZE):
            batch = wb[i:i+BATCH_SIZE]
            texts = [
                f"{w['title']}。{(w.get('content') or '')[:2000]}"
                for w in batch
            ]
            vecs = _embed_batch(texts)
            if vecs is None:
                continue
            with connect() as db:
                for w, v in zip(batch, vecs):
                    db.execute(
                        "update worldbook_entries set embedding_vec = %s::vector, embedded_at = now() where id = %s",
                        (_vec_literal(v), w["id"]),
                    )
        log.info("[embedding] worldbook +%d (script_id=%s)", len(wb), script_id)

    _EMBED_QUEUE_RUNNING[script_id] = False
    log.info("[embedding] done script_id=%s", script_id)


def embed_script(user_id: int, script_id: int) -> dict[str, Any]:
    """触发后台 embedding。fire-and-forget,前端 poll embed_status。

    安全:要求 script.owner_id == user_id 才能触发。
    幂等:已有 embedding_vec 的行跳过,可重复调。
    """
    from ..db import connect, init_db
    init_db()
    with connect() as db:
        row = db.execute(
            "select id from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
    if not row:
        raise ValueError("无权访问该剧本")
    if _EMBED_QUEUE_RUNNING.get(script_id):
        return {"ok": True, "already_running": True, "status": embed_status(script_id)}
    if _get_vertex_client() is None:
        return {
            "ok": False,
            "error": "未连接 Vertex AI · 请先在 .env 配置 vertex_sa.json 或在「设置 → API 设置」启用 Vertex",
        }
    _EMBED_QUEUE_RUNNING[script_id] = True
    threading.Thread(target=_embed_chunks_loop, args=(script_id, user_id), daemon=True).start()
    return {"ok": True, "status": embed_status(script_id)}
