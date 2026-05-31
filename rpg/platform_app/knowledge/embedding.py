"""task 51: Vertex text-embedding-004 + pgvector 双层检索。

设计思路(基于 LightRAG / novel2graph 双层检索范式):
- 块层(document_chunks.embedding_vec): 全书切块的向量,用于 RAG 语义召回
- 实体层(character_cards.embedding_vec, worldbook_entries.embedding_vec):
  角色/世界书条目的向量,GM 提到人名时按向量找完整卡片

embedding model: Google `text-embedding-004` (768 维,多语言含中文) — 默认
BYOK: 用户可在 user_preferences 设置 embed.api_id / embed.model_real_name,
      并在 user_api_credentials 保存对应 provider 的 API key,覆盖系统默认。
batch size: 100 chunks/请求(API 限 250,留 buffer)
存储: pgvector(已 brew install + CREATE EXTENSION)
查询: `embedding_vec <=> query_vec` cosine distance + ivfflat 索引

入口:
- `embed_query(text, user_id)` → str(vector) 给 `_search._embed_query` 用
- `embed_script(script_id, user_id)` → 后台 batch embed 全书 chunks + cards + worldbook
- `embed_status(script_id)` → 进度查询
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

# 系统默认 embedding 配置(env 可覆盖,用户 BYOK 优先于 env)
DEFAULT_EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-004")
DEFAULT_EMBED_API_ID = os.environ.get("EMBED_API_ID", "vertex_ai")
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

# 向后兼容:保留 EMBED_MODEL 常量名(外部模块如 extract/ 直接引用它)
EMBED_MODEL = DEFAULT_EMBED_MODEL


def _resolve_embed_config(user_id: int | None) -> tuple[str, str, str, str]:
    """返回 (api_id, model, api_key, base_url_override)。
    生产鉴权模式下 env 兜底会被 resolve_api_key 禁止，只接受用户级凭证。
    """
    if user_id:
        try:
            from core.llm_backend import resolve_preferred_api, resolve_preferred_model
            from platform_app.user_credentials import resolve_api_key
            api_id = resolve_preferred_api(user_id, "embed.api_id") or DEFAULT_EMBED_API_ID
            model = resolve_preferred_model(user_id, "embed.model_real_name") or DEFAULT_EMBED_MODEL
            cred = resolve_api_key(user_id, api_id, env_fallback=os.environ.get("EMBED_API_KEY", ""))
            return api_id, model, cred.get("key", ""), cred.get("base_url_override", "")
        except Exception as exc:
            log.debug("[embedding] resolve_embed_config failed for user %s: %s", user_id, exc)
    # 无 user_id 或解析失败:纯 env 兜底
    return DEFAULT_EMBED_API_ID, DEFAULT_EMBED_MODEL, os.environ.get("EMBED_API_KEY", ""), ""


def _get_vertex_client(user_id: int | None = None):
    """返回 Vertex genai Client，按 user_id 走 BYOK 优先链。

    生产鉴权模式下 load_sa_credentials 会禁用服务器全局 SA fallback。
    """
    cache_key = f"client:{user_id}"
    if cache_key in _VERTEX_CLIENT_CACHE:
        return _VERTEX_CLIENT_CACHE[cache_key]
    try:
        from google import genai
        from core.vertex_sa import load_sa_credentials

        credentials, project_id = load_sa_credentials(user_id)
        if credentials is None or project_id is None:
            log.warning("[embedding] no Vertex SA available (user_id=%s)", user_id)
            _VERTEX_CLIENT_CACHE[cache_key] = None
            return None
        # Vertex AI text-embedding 走 location='us-central1' 比 global 稳定
        client = genai.Client(
            vertexai=True, project=project_id, location="us-central1",
            credentials=credentials,
        )
        _VERTEX_CLIENT_CACHE[cache_key] = client
        sa_src = f"user={user_id}" if user_id else "global"
        log.debug("[embedding] vertex client init ok (SA: %s, project=%s)", sa_src, project_id)
        return client
    except Exception as e:
        log.warning("[embedding] vertex client init failed: %s", e)
        _VERTEX_CLIENT_CACHE[cache_key] = None
        return None


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

_VERTEX_API_IDS = {"vertex", "google", "vertex_ai"}
_OPENAI_API_IDS = {"openai", "openai_compat"}
_COHERE_API_IDS = {"cohere"}


def _embed_via_vertex(model: str, texts: list[str], task_type: str = "RETRIEVAL_DOCUMENT", user_id: int | None = None) -> list[list[float]] | None:
    """调 Vertex genai SDK。model 为空时回退 DEFAULT_EMBED_MODEL。user_id 用于 BYOK SA 优先链。"""
    client = _get_vertex_client(user_id=user_id)
    if client is None:
        return None
    try:
        from google.genai import types
        resp = client.models.embed_content(
            model=model or DEFAULT_EMBED_MODEL,
            contents=texts,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=EMBED_DIM,
            ),
        )
        return [list(e.values) for e in resp.embeddings]
    except Exception as e:
        log.warning("[embedding] vertex embed failed (%d items): %s", len(texts), e)
        return None


def _embed_via_openai(model: str, api_key: str, texts: list[str], base_url: str = "") -> list[list[float]] | None:
    """OpenAI 兼容 embeddings API。base_url 为空则走官方 https://api.openai.com/v1。"""
    import urllib.request
    import urllib.error
    import json as _json
    effective_url = (base_url.rstrip("/") if base_url else "https://api.openai.com/v1") + "/embeddings"
    payload = _json.dumps({"model": model, "input": texts, "encoding_format": "float"}).encode()
    req = urllib.request.Request(
        effective_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = _json.loads(resp.read())
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        log.warning("[embedding] openai embed failed: %s %s", e.code, body[:200])
        return None
    except Exception as e:
        log.warning("[embedding] openai embed failed: %s", e)
        return None


def _embed_via_cohere(model: str, api_key: str, texts: list[str]) -> list[list[float]] | None:
    """Cohere embed API v2。"""
    try:
        import cohere  # type: ignore
        co = cohere.Client(api_key)
        resp = co.embed(texts=texts, model=model, input_type="search_document")
        return [list(e) for e in resp.embeddings]
    except ImportError:
        log.warning("[embedding] cohere SDK not installed; pip install cohere")
        return None
    except Exception as e:
        log.warning("[embedding] cohere embed failed: %s", e)
        return None


def _embed_provider_dispatch(
    api_id: str,
    model: str,
    api_key: str,
    texts: list[str],
    base_url: str = "",
    task_type: str = "RETRIEVAL_DOCUMENT",
    user_id: int | None = None,
) -> list[list[float]] | None:
    """根据 api_id 分发到对应 provider SDK。不识别 → 降级 vertex + warn。
    user_id 传给 Vertex 路径以走 BYOK SA 优先链。
    """
    if api_id in _VERTEX_API_IDS:
        return _embed_via_vertex(model, texts, task_type=task_type, user_id=user_id)
    if api_id in _OPENAI_API_IDS:
        if not api_key:
            log.warning("[embedding] openai api_id but no api_key; falling back to vertex")
            return _embed_via_vertex(model or DEFAULT_EMBED_MODEL, texts, task_type=task_type, user_id=user_id)
        return _embed_via_openai(model, api_key, texts, base_url=base_url)
    if api_id in _COHERE_API_IDS:
        if not api_key:
            log.warning("[embedding] cohere api_id but no api_key; falling back to vertex")
            return _embed_via_vertex(DEFAULT_EMBED_MODEL, texts, task_type=task_type, user_id=user_id)
        return _embed_via_cohere(model, api_key, texts)
    log.warning("[embedding] unknown api_id=%r; falling back to vertex", api_id)
    return _embed_via_vertex(DEFAULT_EMBED_MODEL, texts, task_type=task_type, user_id=user_id)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def _embed_batch(texts: list[str], user_id: int | None = None) -> list[list[float]] | None:
    """调 embedding provider,返向量列表。失败返 None。
    user_id 非 None 时走 BYOK 优先链,否则走系统默认 vertex SA。
    """
    if not texts:
        return []
    api_id, model, api_key, base_url = _resolve_embed_config(user_id)
    return _embed_provider_dispatch(api_id, model, api_key, texts, base_url=base_url, user_id=user_id)


def embed_query(
    text: str,
    user_id: int | None = None,
    force_api_id: str | None = None,
    force_model: str | None = None,
) -> str | None:
    """task 51 / P0-fix: query 文本 → 768 维向量字符串。
    `_search._embed_query` 的 production 实现。失败返 None 自动 fallback ILIKE。

    优先级链：
      1. force_api_id + force_model（召回路径：必须与建库时的 (api_id, model) 完全一致）
      2. user_id BYOK 配置（ad-hoc query / admin 工具）
      3. 系统默认 vertex_ai + text-embedding-004
    """
    text = (text or "").strip()
    if not text:
        return None
    if force_api_id and force_model:
        # 严格锁定建库时的 provider（召回侧强制路径）
        _, _, api_key, base_url = _resolve_embed_config(user_id)
        api_id, model = force_api_id, force_model
    else:
        api_id, model, api_key, base_url = _resolve_embed_config(user_id)
    vecs = _embed_provider_dispatch(api_id, model, api_key, [text], base_url=base_url, task_type="RETRIEVAL_QUERY", user_id=user_id)
    if not vecs:
        log.warning("[embedding] embed_query returned no vectors")
        return None
    vec = vecs[0]
    # pgvector 接受 "[v1,v2,...]" 字符串
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


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
        # v28: 多态后 embed 进度只统计 NPC 行(PC/persona 不参与剧本检索嵌入)
        cards_total = db.execute(
            "select count(*) as c from character_cards where script_id = %s and card_type = 'npc'",
            (script_id,),
        ).fetchone()["c"]
        cards_done = db.execute(
            "select count(*) as c from character_cards "
            "where script_id = %s and card_type = 'npc' and embedding_vec is not null",
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

    # P0-fix: 拆书开始时立即将 (api_id, model) 绑定到 scripts 表，
    # 保证召回时能读到确定的向量空间配置。
    _bind_api_id, _bind_model, _, _ = _resolve_embed_config(user_id)
    try:
        with connect() as db:
            db.execute(
                "update scripts set embed_api_id = %s, embed_model = %s where id = %s",
                (_bind_api_id, _bind_model, script_id),
            )
        log.info(
            "[embedding] bound embed meta to script %s: api_id=%s model=%s",
            script_id, _bind_api_id, _bind_model,
        )
        # 使新 meta 立即生效（进程内 cache 失效）
        from platform_app.knowledge._search import _SCRIPT_EMBED_META_CACHE
        _SCRIPT_EMBED_META_CACHE.pop(script_id, None)
    except Exception as exc:
        log.warning("[embedding] failed to bind embed meta to script %s: %s", script_id, exc)

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
        vecs = _embed_batch(texts, user_id=user_id)
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

    # task 52: entity 层 embed 之前,先回填 first_chapter / last_seen_chapter。
    # 这样下游 _search_entities 能按时间线硬过滤,GM 不会被召回未来章节的角色/词条。
    # 算法:全文 LIKE 搜 script_chapters.content,聚合 MIN/MAX chapter_index。
    # 一次性 SQL,~O(N × chapter_count),866 章场景下 ~200ms。
    with connect() as db:
        db.execute(
            """
            with char_first_last as (
              select cc.id as cc_id,
                     min(sc.chapter_index) as first_ch,
                     max(sc.chapter_index) as last_ch
              from character_cards cc
              join script_chapters sc on sc.script_id = cc.script_id
              where cc.script_id = %s
                and cc.card_type = 'npc'           -- v28: 章节边界回填只对 NPC 行
                and sc.content like '%%' || cc.name || '%%'
              group by cc.id
            )
            update character_cards cc
            set first_chapter = cfl.first_ch,
                last_seen_chapter = cfl.last_ch
            from char_first_last cfl
            where cc.id = cfl.cc_id
              and cc.first_chapter is null
              and cc.card_type = 'npc'
            """,
            (script_id,),
        )
        db.execute(
            "update worldbook_entries set first_chapter = 1 "
            "where script_id = %s and first_chapter is null",
            (script_id,),
        )
        log.info("[embedding] task 52: backfilled chapter boundaries for script %s", script_id)

    # entity 层:character_cards
    with connect() as db:
        cards = db.execute(
            "select id, name, identity, personality, appearance from character_cards "
            "where script_id = %s and card_type = 'npc' and embedding_vec is null",
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
            vecs = _embed_batch(texts, user_id=user_id)
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
            vecs = _embed_batch(texts, user_id=user_id)
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
    # 检查 embedding provider 是否可用：生产鉴权模式必须有用户 BYOK/API key。
    _api_id, _model, _api_key, _base_url = _resolve_embed_config(user_id)
    _provider_ok = (
        (_api_id in _VERTEX_API_IDS and _get_vertex_client(user_id=user_id) is not None)
        or (_api_id not in _VERTEX_API_IDS and bool(_api_key))
    )
    if not _provider_ok:
        return {
            "ok": False,
            "error": (
                f"未配置 {_api_id} embedding 凭证 · "
                "请在「设置 → API 设置」添加对应 API key"
            ),
        }
    _EMBED_QUEUE_RUNNING[script_id] = True
    threading.Thread(target=_embed_chunks_loop, args=(script_id, user_id), daemon=True).start()
    return {"ok": True, "status": embed_status(script_id)}
