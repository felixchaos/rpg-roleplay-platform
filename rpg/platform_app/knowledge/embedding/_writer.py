"""platform_app.knowledge.embedding._writer — 后台 batch embedding 作业 + 进度/运行锁。

拆包前住在单文件 embedding.py。本模块承载「写库」侧:遍历 chunks/cards/worldbook 分批嵌入、
进度查询(embed_status)、跨进程运行锁(Redis + 进程内 _EMBED_QUEUE_RUNNING)、后台线程编排。
可变全局 _EMBED_QUEUE_RUNNING 与其读写方(_embed_is_running / _embed_chunks_loop / embed_script)
同居于此。

_resolve_embed_config / _embed_batch / embedding_preflight 定义在公共层 __init__(测试在包命名
空间上 patch 它们),这里按需引用 —— 与 __init__ 构成一处受控的有序循环导入:__init__ 在定义完
这些名字之后,末尾才 `from ._writer import ...`,故本模块 import 时它们已就绪。

拆包唯一非逐字改动:原 `from ..db import connect` 是相对 embedding.py(住 platform_app.knowledge)
的两点导入 = platform_app.db;本模块下沉一层后改写成绝对 `from platform_app.db import ...`
(同一目标模块,行为零变化)。
"""
from __future__ import annotations

import threading
import time
from typing import Any

# 受控循环导入(见模块 docstring)。retry_cap 测试按 patch-where-defined 在 _writer 命名空间
# patch _resolve_embed_config / _embed_batch,故必须是本模块的模块级名字。
from platform_app.knowledge.embedding import (  # noqa: E402  (有序循环导入)
    _embed_batch,
    _resolve_embed_config,
    embedding_preflight,
)

from ._base import (
    _MAX_EMBED_BATCH_RETRIES,
    BATCH_SIZE,
    EMBED_DIM,
    EMBED_MODEL,
    PER_CHUNK_CHAR_LIMIT,
    _vec_literal,
    log,
    provider_lacks_embedding,
)

# Redis key 前缀,用于跨进程去重;TTL 略大于预期最长 embed 时长,兼做宕机自动解锁。
_EMBED_REDIS_PREFIX = "rpg:embed_running:"
_EMBED_REDIS_TTL = 3600  # 1 小时,足够大,宕机后 Redis 自动释放锁
_EMBED_QUEUE_RUNNING: dict[int, bool] = {}  # script_id → 是否在跑(进程内降级标志,多 worker 各自独立)


def _embed_redis_acquire(script_id: int) -> bool:
    """跨进程去重:用 Redis SETNX+EXPIRE 申请运行权。

    返回 True 表示本进程/worker 拿到运行权;False 表示已有其它 worker 在跑该 script。
    Redis 不可用时 → 优雅降级,返回 True(进程内标志兜底,单 worker 语义不变)。
    """
    try:
        from redis_bus import get_sync_client
        r = get_sync_client()
        if r is None:
            return True  # Redis 未配或不可达,降级到进程内标志
        key = f"{_EMBED_REDIS_PREFIX}{script_id}"
        acquired = r.set(key, "1", nx=True, ex=_EMBED_REDIS_TTL)
        return bool(acquired)
    except Exception as exc:
        log.warning("[embedding] redis acquire failed (graceful degradation): %s", exc)
        return True  # 降级:允许本进程继续,进程内标志兜底


def _embed_redis_release(script_id: int) -> None:
    """释放 Redis 运行权锁(finally 保证调用)。Redis 不可达时静默忽略。"""
    try:
        from redis_bus import get_sync_client
        r = get_sync_client()
        if r is None:
            return
        r.delete(f"{_EMBED_REDIS_PREFIX}{script_id}")
    except Exception as exc:
        log.warning("[embedding] redis release failed (TTL will auto-expire): %s", exc)


def _embed_is_running(script_id: int) -> bool:
    """检查某 script 是否有任意 worker 在跑 embedding(跨进程感知)。

    优先查 Redis;Redis 不可达时退回本进程内标志。
    """
    try:
        from redis_bus import get_sync_client
        r = get_sync_client()
        if r is not None:
            return bool(r.exists(f"{_EMBED_REDIS_PREFIX}{script_id}"))
    except Exception:
        pass
    return _EMBED_QUEUE_RUNNING.get(script_id, False)


def embed_status(script_id: int) -> dict[str, Any]:
    """查询某剧本的 embedding 进度。"""
    from platform_app.db import connect
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
        # 知识库人物(canon)用 kb_canon_entities.embedding(vector 列,**不是** embedding_vec)。
        # 此前漏算 canon → 前端「知识库人物」卡 es['canon']=undefined → 永远 0 条·状态未知,
        # 而重做估算/任务却按同样的列正常计数(2924/已完成 N) → 卡片与重做对不上(群反馈)。
        # 与 import_pipeline 估算口径完全一致(count where embedding is not null)。
        canon_total = db.execute(
            "select count(*) as c from kb_canon_entities where script_id = %s",
            (script_id,),
        ).fetchone()["c"]
        canon_done = db.execute(
            "select count(*) as c from kb_canon_entities where script_id = %s and embedding is not null",
            (script_id,),
        ).fetchone()["c"]
    return {
        "running": _embed_is_running(script_id),
        "chunks": {"done": chunks_done, "total": chunks_total},
        "cards": {"done": cards_done, "total": cards_total},
        "worldbook": {"done": wb_done, "total": wb_total},
        "canon": {"done": canon_done, "total": canon_total},
        "model": EMBED_MODEL,
        "dim": EMBED_DIM,
    }


def _embed_chunks_loop(script_id: int, user_id: int) -> None:
    """后台线程:遍历 document_chunks 分批调 Vertex,写 embedding_vec。

    P0:整个函数 try/finally 包裹,保证 _EMBED_QUEUE_RUNNING flag 总被清,
    daemon thread 异常死亡 / backend 重启后下次 embed_script 不会卡在
    already_running 状态。
    """
    log.info("[embedding] start chunks: script_id=%s user=%s", script_id, user_id)
    try:
        _embed_chunks_loop_inner(script_id, user_id)
    except Exception as exc:
        log.warning("[embedding] loop crashed for script %s: %s", script_id, exc, exc_info=True)
    finally:
        _EMBED_QUEUE_RUNNING[script_id] = False
        _embed_redis_release(script_id)  # 释放跨进程锁(Redis 不可达时静默忽略)
        log.info("[embedding] done script_id=%s (flag cleared)", script_id)


def _embed_chunks_loop_inner(script_id: int, user_id: int) -> None:
    """实际工作循环 — 由 _embed_chunks_loop 包裹保证 flag 清理"""
    from platform_app.db import connect

    # P0-fix: 拆书开始时立即将 (api_id, model) 绑定到 scripts 表，
    # 保证召回时能读到确定的向量空间配置。
    _bind_api_id, _bind_model, _bind_key, _bind_base = _resolve_embed_config(user_id)
    # 权威闸:选了没有 embedding 接口的 provider(deepseek/anthropic 等)→ 快速失败 + 清晰指引,
    # 不绑坏 meta、不进 5 次 404 重试(~2.5 分钟)。这是各层校验(picker/preflight)漏掉的兜底。
    if provider_lacks_embedding(_bind_api_id, _bind_base):
        raise RuntimeError(
            f"嵌入器 provider「{_bind_api_id}」没有向量嵌入(embedding)接口,无法为 script {script_id} 建向量索引。"
            f"请在「设置 → API & 模型 → RAG 模型」改选支持 embedding 的 provider"
            f"(如 OpenAI text-embedding-3 / 阿里 dashscope / siliconflow / Vertex,或本地 bge/nomic),再重新导入。"
        )
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
        from platform_app.knowledge._search import _SCRIPT_EMBED_META_CACHE, _UNBOUND_EMBED_WARNED
        _SCRIPT_EMBED_META_CACHE.pop(script_id, None)
        # 重新绑定成功：该 script 不再是「未绑定」状态，把降频标记也一并清掉，
        # 万一将来又变回未绑定（如再次拆书失败），警告能重新提醒一次而不是永久沉默。
        _UNBOUND_EMBED_WARNED.discard(script_id)
    except Exception as exc:
        log.warning("[embedding] failed to bind embed meta to script %s: %s", script_id, exc)

    _consecutive_fails = 0
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
            _consecutive_fails += 1
            if _consecutive_fails >= _MAX_EMBED_BATCH_RETRIES:
                # 连续失败达上限:大概率 provider 永久故障(坏 key/配额/模型下线)。
                # 抛出 → _embed_chunks_loop 优雅收尾(清 flag、线程退出),不再无限 spin。
                raise RuntimeError(
                    f"embedding batch 连续失败 {_consecutive_fails} 次,放弃 script {script_id}"
                    f"(剩余 chunk 留 null 待修复 provider 后重试)"
                )
            log.warning("[embedding] batch failed (%d/%d), sleeping 30s then retry",
                        _consecutive_fails, _MAX_EMBED_BATCH_RETRIES)
            time.sleep(30)
            continue
        _consecutive_fails = 0  # 成功一批即重置连续失败计数(仅对持续性故障熔断)
        if len(vecs) != len(rows):
            # 行数不匹配(供应商异常)。原来直接 break → 整个 script 剩余 chunk 永不 embed
            # 且静默(RAG 召回残缺)。改为:写入可匹配的前 N 对(保证推进),再继续下一批;
            # 0 匹配才放弃(避免死循环)。
            _n = min(len(vecs), len(rows))
            log.error("[embedding] vec count mismatch: got %d expected %d (script_id=%s) — 写入前 %d 对后继续",
                      len(vecs), len(rows), script_id, _n)
            if _n == 0:
                break
            with connect() as db:
                for r, v in zip(rows[:_n], vecs[:_n]):
                    db.execute(
                        "update document_chunks set embedding_vec = %s::vector, embedded_at = now() where id = %s",
                        (_vec_literal(v), r["id"]),
                    )
            continue

        with connect() as db:
            for r, v in zip(rows, vecs):
                db.execute(
                    "update document_chunks set embedding_vec = %s::vector, embedded_at = now() where id = %s",
                    (_vec_literal(v), r["id"]),
                )
        log.info("[embedding] chunks +%d (script_id=%s)", len(rows), script_id)

    # BUG-1: 旧 task 52 在此用全文 LIKE 回填 character_cards/worldbook_entries 的
    # first_chapter / last_seen_chapter —— 但那两列全库从未建过,整段 SQL 恒抛
    # "column does not exist",被 try/except 静默吞,从未生效过。
    # 进度过滤已统一到 first_revealed_chapter:character_cards 由 extraction/resolve 写
    # (v28 _sync upsert),worldbook_entries 由 migration v53 补列 + 从 metadata.chapter_min
    # 回填。_search_entities 直接读 first_revealed_chapter,无需此回填。故移除死代码避免误导。

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


def embed_script(user_id: int, script_id: int) -> dict[str, Any]:
    """触发后台 embedding。fire-and-forget,前端 poll embed_status。

    安全:要求 script.owner_id == user_id 才能触发。
    幂等:已有 embedding_vec 的行跳过,可重复调。
    """
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        row = db.execute(
            "select id from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
    if not row:
        raise ValueError("无权访问该剧本")
    if _embed_is_running(script_id):
        return {"ok": True, "already_running": True, "status": embed_status(script_id)}
    # 检查 embedding provider 是否可用：生产鉴权模式必须有用户 BYOK/API key。
    preflight = embedding_preflight(user_id)
    if not preflight.get("ok"):
        return preflight
    # 申请运行权(跨进程 SETNX,降级到进程内标志)。
    # 二次检查 + acquire 之间存在极小窗口,最坏情况两 worker 同时进入跑一次重复 embed;
    # embed 本身幂等(skip already-embedded rows),重复可接受。
    if not _embed_redis_acquire(script_id):
        return {"ok": True, "already_running": True, "status": embed_status(script_id)}
    _EMBED_QUEUE_RUNNING[script_id] = True
    threading.Thread(target=_embed_chunks_loop, args=(script_id, user_id), daemon=True).start()
    return {"ok": True, "status": embed_status(script_id)}
