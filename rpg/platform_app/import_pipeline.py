"""
import_pipeline.py — 拆书流水线（多阶段 + DB 进度 + 取消 + 预算）

整体流程：
  1. chunks        — 文本切块入 document_chunks
  2. facts         — 规则 ChapterFact 入 chapter_facts
  3. entities      — 高频人物名提取（不调 LLM，靠词频）
  4. cards         — LLM 给 top N 人物生成人设卡（可关）
  5. worldbook     — LLM 提取地点/势力/概念入世界书（可关）

每阶段：
  - 进度落 import_jobs.stage_progress / overall_progress
  - 每个 chunk 检查 cancel_requested，true → 标 cancelled 退出
  - usage_actual 累加真实 token / cost
"""
from __future__ import annotations

import json
import re
import secrets
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from psycopg.types.json import Jsonb

from .db import connect, init_db, expose


# ── 阶段定义 ────────────────────────────────────────────────────────
STAGES = [
    ("chunks",    "切块入库"),
    ("facts",     "章节事实"),
    ("entities",  "人物提取"),
    ("cards",     "人设卡生成"),
    ("worldbook", "世界书建立"),
]


# ── 后台执行器 ──────────────────────────────────────────────────────
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="import-pipe")
_RUNNING: dict[str, threading.Thread] = {}  # job_id → thread


# ══════════════════════════════════════════════════════════════════════
#  预算预估（不入库，仅估算）
# ══════════════════════════════════════════════════════════════════════
def estimate_budget(
    chapter_count: int,
    total_words: int,
    *,
    enable_cards: bool = True,
    enable_worldbook: bool = True,
    cards_top_n: int = 30,
    model_api_id: str = "vertex_ai",
    model_real_name: str = "gemini-3.5-flash",
) -> dict[str, Any]:
    """开始导入前的预算。

    估算依据：
    - chunks: 0 token（确定性，只切块）
    - facts: 0 token（确定性，规则匹配）
    - entities: 0 token（确定性词频）
    - cards: top_n 个角色 × 每个 ~3000 token in + 800 out
    - worldbook: ~20 条目 × 每条 ~2000 token in + 400 out

    时间估算：
    - 确定性阶段：100 章/秒
    - LLM 阶段：每次请求 ~3s
    """
    try:
        from model_probe import get_pricing
        pricing = get_pricing(model_api_id, model_real_name) or {}
    except Exception:
        pricing = {}
    input_price = float(pricing.get("input", 1.0))   # USD per million
    output_price = float(pricing.get("output", 5.0))

    cards_calls = cards_top_n if enable_cards else 0
    worldbook_calls = 20 if enable_worldbook else 0
    cards_input = cards_calls * 3000
    cards_output = cards_calls * 800
    wb_input = worldbook_calls * 2000
    wb_output = worldbook_calls * 400

    total_input = cards_input + wb_input
    total_output = cards_output + wb_output
    cost_usd = (total_input * input_price + total_output * output_price) / 1_000_000

    eta_sec = (
        chapter_count / 100              # chunks
        + chapter_count / 100            # facts
        + 0.5                            # entities (instant)
        + cards_calls * 3                # cards
        + worldbook_calls * 3            # worldbook
    )

    return {
        "ok": True,
        "model": {"api_id": model_api_id, "real_name": model_real_name, "pricing": pricing},
        "stages": [
            {"id": "chunks",    "label": "切块入库", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "eta_sec": chapter_count / 100, "deterministic": True},
            {"id": "facts",     "label": "章节事实", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "eta_sec": chapter_count / 100, "deterministic": True},
            {"id": "entities",  "label": "人物提取", "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "eta_sec": 0.5, "deterministic": True},
            {"id": "cards",     "label": "人设卡生成", "tokens_in": cards_input, "tokens_out": cards_output,
             "cost_usd": round((cards_input * input_price + cards_output * output_price) / 1_000_000, 4),
             "eta_sec": cards_calls * 3, "enabled": enable_cards, "calls": cards_calls},
            {"id": "worldbook", "label": "世界书建立", "tokens_in": wb_input, "tokens_out": wb_output,
             "cost_usd": round((wb_input * input_price + wb_output * output_price) / 1_000_000, 4),
             "eta_sec": worldbook_calls * 3, "enabled": enable_worldbook, "calls": worldbook_calls},
        ],
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost_usd": round(cost_usd, 4),
        "total_eta_sec": int(eta_sec),
        "chapter_count": chapter_count,
        "total_words": total_words,
    }


# ══════════════════════════════════════════════════════════════════════
#  Job 控制：DB 状态读写 + 取消信号
# ══════════════════════════════════════════════════════════════════════
class JobController:
    """封装单个 import_job 的 DB 状态操作。worker 用 self.update() 写进度，
    self.is_cancelled() 检查是否被用户取消。"""

    def __init__(self, job_id: str):
        self.job_id = job_id

    def _exec(self, sql: str, params: tuple) -> None:
        init_db()
        with connect() as db:
            db.execute(sql, params)

    def update(self, **fields) -> None:
        """部分更新当前 job 的字段（status/stage/stage_progress/...）"""
        if not fields:
            return
        sets = []
        params: list[Any] = []
        for k, v in fields.items():
            if k in ("budget_estimate", "usage_actual", "stages"):
                sets.append(f"{k} = %s")
                params.append(Jsonb(v))
            else:
                sets.append(f"{k} = %s")
                params.append(v)
        sets.append("updated_at = now()")
        params.append(self.job_id)
        self._exec(f"update import_jobs set {', '.join(sets)} where job_id = %s", tuple(params))

    def is_cancelled(self) -> bool:
        init_db()
        with connect() as db:
            row = db.execute(
                "select cancel_requested, status from import_jobs where job_id = %s",
                (self.job_id,),
            ).fetchone()
        return bool(row and (row.get("cancel_requested") or row.get("status") == "cancelled"))

    def add_usage(self, input_tokens: int, output_tokens: int, cost_usd: float) -> None:
        init_db()
        with connect() as db:
            db.execute(
                """
                update import_jobs
                   set usage_actual = jsonb_set(
                       jsonb_set(
                           jsonb_set(usage_actual,
                               '{input_tokens}', to_jsonb(coalesce((usage_actual->>'input_tokens')::int,0) + %s)),
                           '{output_tokens}', to_jsonb(coalesce((usage_actual->>'output_tokens')::int,0) + %s)),
                       '{cost_usd}', to_jsonb(coalesce((usage_actual->>'cost_usd')::float,0) + %s)),
                       updated_at = now()
                 where job_id = %s
                """,
                (input_tokens, output_tokens, cost_usd, self.job_id),
            )


# ══════════════════════════════════════════════════════════════════════
#  公共入口
# ══════════════════════════════════════════════════════════════════════
def schedule_full_import(
    user_id: int,
    script_id: int,
    *,
    enable_cards: bool = True,
    enable_worldbook: bool = True,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """启动一次完整拆书流水线，返回 job_id。"""
    init_db()
    # 去重 + 限流（同 script 已有 running 任务直接返回那个 job）
    with connect() as db:
        existing = db.execute(
            """
            select job_id from import_jobs
            where user_id = %s and script_id = %s
              and status in ('pending', 'running')
            order by id desc limit 1
            """,
            (user_id, script_id),
        ).fetchone()
        if existing:
            return {"ok": True, "job_id": existing["job_id"], "reused": True}

        # per-user 并发上限 1
        active = db.execute(
            "select count(*) as n from import_jobs where user_id = %s and status in ('pending','running')",
            (user_id,),
        ).fetchone()
        if int(active["n"] if active else 0) >= 1:
            raise ValueError("您已有 1 个导入任务在跑，请等其完成或取消")

        job_id = f"imp_{script_id}_{secrets.token_hex(6)}"
        db.execute(
            """
            insert into import_jobs(job_id, user_id, script_id, status, stage, overall_total, budget_estimate)
            values (%s, %s, %s, 'pending', 'pending', %s, %s)
            """,
            (job_id, user_id, script_id, len(STAGES), Jsonb(budget or {})),
        )

    options = {"enable_cards": enable_cards, "enable_worldbook": enable_worldbook}
    th = threading.Thread(target=_run_pipeline, args=(job_id, user_id, script_id, options), daemon=True)
    _RUNNING[job_id] = th
    th.start()
    return {"ok": True, "job_id": job_id, "reused": False}


def get_job_status(user_id: int, job_id: str | None = None, script_id: int | None = None) -> dict[str, Any]:
    """读 DB 拿任务状态。"""
    init_db()
    with connect() as db:
        if job_id:
            row = db.execute(
                "select * from import_jobs where job_id = %s and user_id = %s",
                (job_id, user_id),
            ).fetchone()
        elif script_id:
            row = db.execute(
                "select * from import_jobs where script_id = %s and user_id = %s order by id desc limit 1",
                (script_id, user_id),
            ).fetchone()
        else:
            return {"ok": False, "error": "需要 job_id 或 script_id"}
    if not row:
        return {"ok": True, "found": False}
    return {"ok": True, "found": True, "job": expose(row)}


def cancel_job(user_id: int, job_id: str) -> dict[str, Any]:
    """请求取消：worker 在下个检查点会退出。"""
    init_db()
    with connect() as db:
        row = db.execute(
            "update import_jobs set cancel_requested = true, updated_at = now() "
            "where job_id = %s and user_id = %s returning status",
            (job_id, user_id),
        ).fetchone()
    if not row:
        raise ValueError("job 不存在")
    return {"ok": True, "current_status": row.get("status")}


def list_jobs(user_id: int, limit: int = 20) -> dict[str, Any]:
    """列出本人最近 N 个任务（dashboard 用）。"""
    init_db()
    with connect() as db:
        rows = db.execute(
            "select * from import_jobs where user_id = %s order by id desc limit %s",
            (user_id, int(limit)),
        ).fetchall()
    return {"ok": True, "items": [expose(r) for r in rows], "total": len(rows)}


# ══════════════════════════════════════════════════════════════════════
#  Worker：跑完整流水线
# ══════════════════════════════════════════════════════════════════════
def _run_pipeline(job_id: str, user_id: int, script_id: int, options: dict[str, Any]) -> None:
    # 多 worker 部署：advisory lock 防止同 job 被多 worker 同时跑
    try:
        from .cluster import try_acquire_job_lock, release_job_lock
        if not try_acquire_job_lock(f"import_job:{job_id}"):
            # 已被别的 worker 占了，直接退出（那个 worker 会处理）
            return
    except Exception:
        try_acquire_job_lock = None
        release_job_lock = None

    ctl = JobController(job_id)
    ctl.update(status="running", stages=[{"id": s[0], "label": s[1], "status": "pending"} for s in STAGES])
    init_db()
    with connect() as db:
        db.execute("update import_jobs set started_at = now() where job_id = %s", (job_id,))

    stages_progress = []
    try:
        # ── 阶段 1: chunks ────────────────────────────────
        if ctl.is_cancelled():
            return _finalize_cancelled(ctl)
        ctl.update(stage="chunks", overall_progress=0)
        chunks_n = _stage_chunks(ctl, script_id, user_id)
        stages_progress.append({"id": "chunks", "status": "done", "count": chunks_n})
        ctl.update(stages=stages_progress, overall_progress=1)

        # ── 阶段 2: facts ────────────────────────────────
        if ctl.is_cancelled():
            return _finalize_cancelled(ctl)
        ctl.update(stage="facts")
        facts_n = _stage_facts(ctl, script_id, user_id)
        stages_progress.append({"id": "facts", "status": "done", "count": facts_n})
        ctl.update(stages=stages_progress, overall_progress=2)

        # ── 阶段 3: entities（高频人物名）────────────────
        if ctl.is_cancelled():
            return _finalize_cancelled(ctl)
        ctl.update(stage="entities")
        entities = _stage_entities(ctl, script_id, user_id)
        stages_progress.append({"id": "entities", "status": "done", "count": len(entities)})
        ctl.update(stages=stages_progress, overall_progress=3)

        # ── 阶段 4: cards（LLM, 可关）────────────────────
        if options.get("enable_cards", True):
            if ctl.is_cancelled():
                return _finalize_cancelled(ctl)
            ctl.update(stage="cards")
            cards_n = _stage_cards(ctl, user_id, script_id, entities)
            stages_progress.append({"id": "cards", "status": "done", "count": cards_n})
        else:
            stages_progress.append({"id": "cards", "status": "skipped"})
        ctl.update(stages=stages_progress, overall_progress=4)

        # ── 阶段 5: worldbook（LLM, 可关）─────────────────
        if options.get("enable_worldbook", True):
            if ctl.is_cancelled():
                return _finalize_cancelled(ctl)
            ctl.update(stage="worldbook")
            wb_n = _stage_worldbook(ctl, user_id, script_id)
            stages_progress.append({"id": "worldbook", "status": "done", "count": wb_n})
        else:
            stages_progress.append({"id": "worldbook", "status": "skipped"})
        ctl.update(stages=stages_progress, overall_progress=5)

        # 完成
        with connect() as db:
            db.execute(
                "update import_jobs set status='done', stage='done', finished_at=now() where job_id=%s",
                (job_id,),
            )
    except Exception as exc:
        import traceback
        err = f"{exc}\n{traceback.format_exc()[:500]}"
        with connect() as db:
            db.execute(
                "update import_jobs set status='failed', error=%s, finished_at=now() where job_id=%s",
                (err, job_id),
            )
    finally:
        _RUNNING.pop(job_id, None)
        try:
            if release_job_lock:
                release_job_lock(f"import_job:{job_id}")
        except Exception:
            pass


def _finalize_cancelled(ctl: JobController) -> None:
    with connect() as db:
        db.execute(
            "update import_jobs set status='cancelled', stage='cancelled', finished_at=now() where job_id=%s",
            (ctl.job_id,),
        )


# ══════════════════════════════════════════════════════════════════════
#  阶段实现
# ══════════════════════════════════════════════════════════════════════
def _stage_chunks(ctl: JobController, script_id: int, user_id: int) -> int:
    """切块入 document_chunks（确定性，无 LLM）"""
    from . import knowledge
    with connect() as db:
        chapters = db.execute(
            "select * from script_chapters where script_id = %s order by chapter_index",
            (script_id,),
        ).fetchall()
        if not chapters:
            return 0
        script = db.execute(
            "select * from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not script:
            raise ValueError("script not found")
        book = knowledge._ensure_book(db, script)

        ctl.update(stage_progress=0, stage_total=len(chapters))
        chunk_count = 0
        for i, chapter in enumerate(chapters):
            if ctl.is_cancelled():
                raise RuntimeError("cancelled")
            doc = knowledge._upsert_document(db, book, script, chapter)
            db.execute("delete from document_chunks where document_id = %s", (doc["id"],))
            for ci, content in enumerate(knowledge._chunk_text(chapter["content"])):
                knowledge._insert_chunk(db, book, script, chapter, doc, ci, content)
                chunk_count += 1
            if (i + 1) % 5 == 0 or i == len(chapters) - 1:
                ctl.update(stage_progress=i + 1)
    return chunk_count


def _stage_facts(ctl: JobController, script_id: int, user_id: int) -> int:
    """规则 ChapterFact 入 chapter_facts（确定性）"""
    from . import knowledge
    chars = knowledge._load_characters()
    world = knowledge._load_world()
    summaries = knowledge._load_summaries()
    known_names = knowledge._known_names(chars)
    known_locations = knowledge._known_locations(world)
    known_concepts = knowledge._known_concepts(world)

    with connect() as db:
        script = db.execute(
            "select * from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        book = knowledge._ensure_book(db, script)
        chapters = db.execute(
            "select * from script_chapters where script_id = %s order by chapter_index",
            (script_id,),
        ).fetchall()
        ctl.update(stage_progress=0, stage_total=len(chapters))
        for i, chapter in enumerate(chapters):
            if ctl.is_cancelled():
                raise RuntimeError("cancelled")
            doc_row = db.execute(
                "select * from documents where script_id = %s and chapter_index = %s",
                (script_id, chapter["chapter_index"]),
            ).fetchone()
            if not doc_row:
                doc_row = knowledge._upsert_document(db, book, script, chapter)
            fact = knowledge._fact_from_chapter(chapter, summaries, known_names, known_locations, known_concepts)
            knowledge._upsert_chapter_fact(db, book, script, chapter, doc_row, fact)
            if (i + 1) % 10 == 0 or i == len(chapters) - 1:
                ctl.update(stage_progress=i + 1)
    return len(chapters)


def _stage_entities(ctl: JobController, script_id: int, user_id: int) -> list[dict[str, Any]]:
    """高频人名提取（中文 2-3 字 + 出现次数排序）。

    简化策略：从 character_cards 已有别名 + 文本里出现的高频候选名合并。
    实际生产可换更聪明的 NER。
    """
    with connect() as db:
        chapters = db.execute(
            "select content from script_chapters where script_id = %s",
            (script_id,),
        ).fetchall()
        existing_names = set()
        for r in db.execute(
            "select name, aliases from character_cards where script_id = %s",
            (script_id,),
        ).fetchall():
            existing_names.add(r["name"])
            existing_names.update(r.get("aliases") or [])

    full_text = "\n".join(c["content"] for c in chapters)
    # 候选：2-3 字中文连续词，且不在常见停用词里
    candidates = re.findall(r"[一-鿿]{2,3}", full_text)
    stop = {"什么", "怎么", "这个", "那个", "时候", "可以", "已经", "如何",
            "为什么", "因为", "所以", "只是", "就是", "还是", "但是"}
    counter = Counter(c for c in candidates if c not in stop)
    ctl.update(stage_progress=1, stage_total=1)

    # top 50 高频 + existing cards 名字合并
    top_n = [{"name": n, "count": cnt} for n, cnt in counter.most_common(50)]
    for n in existing_names:
        if not any(x["name"] == n for x in top_n):
            top_n.append({"name": n, "count": counter.get(n, 0)})
    return top_n[:60]


def _stage_cards(ctl: JobController, user_id: int, script_id: int, entities: list[dict[str, Any]]) -> int:
    """LLM 给 top N 人物生成人设卡。

    简化：调 GameMaster.call_structured 让模型按 JSON schema 输出。
    超时/失败的角色跳过，不阻断整个流水线。
    """
    from . import knowledge
    try:
        from agents.gm import GameMaster
        gm = GameMaster(user_id=user_id)
    except Exception as exc:
        # 没配 user 凭证：跳过这阶段
        return 0

    top_n = 30
    targets = [e for e in entities[:top_n] if e["count"] >= 5]
    ctl.update(stage_progress=0, stage_total=len(targets))

    # 取每个角色的最相关文本片段（出现该名字的前 3 章节）
    with connect() as db:
        chapters_idx = db.execute(
            "select chapter_index, content from script_chapters where script_id = %s order by chapter_index",
            (script_id,),
        ).fetchall()
        book_row = db.execute(
            "select id from books where script_id = %s", (script_id,),
        ).fetchone()
        book_id = int(book_row["id"]) if book_row else None

    generated = 0
    for i, entity in enumerate(targets):
        if ctl.is_cancelled():
            raise RuntimeError("cancelled")
        name = entity["name"]
        # 找前 3 个含该名字的章节，截首 1500 字
        snippets = []
        for ch in chapters_idx:
            if name in ch["content"]:
                snippets.append(ch["content"][:1500])
                if len(snippets) >= 3:
                    break
        if not snippets:
            ctl.update(stage_progress=i + 1)
            continue

        prompt = (
            f"从下面文本里提取角色「{name}」的设定，返回严格 JSON：\n"
            "{ \"identity\": \"身份/势力\", \"appearance\": \"外貌\", "
            "\"personality\": \"性格\", \"speech_style\": \"说话风格\", "
            "\"aliases\": [\"别名1\",\"别名2\"] }\n\n"
            "文本：\n" + "\n---\n".join(snippets)
        )
        try:
            raw = gm._backend.call_structured(
                system="你是角色卡提取器，只输出 JSON。",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600,
            )
            data = _parse_json(raw)
            if data:
                # 累 usage
                last = getattr(gm._backend, "last_usage", {}) or {}
                from .usage import compute_cost
                cost = float(compute_cost(gm.api_id, gm._backend.model_name, last))
                ctl.add_usage(int(last.get("input_tokens", 0)), int(last.get("output_tokens", 0)), cost)
                # 写入 character_cards
                knowledge.upsert_character_card(user_id, script_id, {
                    "name": name,
                    "aliases": data.get("aliases") or [],
                    "identity": data.get("identity") or "",
                    "appearance": data.get("appearance") or "",
                    "personality": data.get("personality") or "",
                    "speech_style": data.get("speech_style") or "",
                    "metadata": {"source": "llm_pipeline", "freq": entity["count"]},
                })
                generated += 1
        except Exception:
            pass
        ctl.update(stage_progress=i + 1)
    return generated


def _stage_worldbook(ctl: JobController, user_id: int, script_id: int) -> int:
    """LLM 提取地点/势力/概念入 worldbook_entries。"""
    from . import knowledge
    try:
        from agents.gm import GameMaster
        gm = GameMaster(user_id=user_id)
    except Exception:
        return 0

    with connect() as db:
        chapters = db.execute(
            "select content from script_chapters where script_id = %s order by chapter_index",
            (script_id,),
        ).fetchall()
        book_row = db.execute(
            "select id from books where script_id = %s", (script_id,),
        ).fetchone()
        if not book_row:
            return 0
        book_id = int(book_row["id"])

    # 把全书前 8000 字作为种子（成本控）
    seed = "\n".join(c["content"] for c in chapters)[:8000]
    ctl.update(stage_progress=0, stage_total=1)

    prompt = (
        "从下面文本里提取重要的世界观条目（地点/势力/概念），返回严格 JSON 数组：\n"
        "[{\"name\":\"...\",\"keys\":[\"关键词1\",\"关键词2\"],\"content\":\"≤200字解释\",\"priority\":80}]\n"
        "数量上限 20。\n\n文本：\n" + seed
    )
    try:
        raw = gm._backend.call_structured(
            system="你是世界书编辑，只输出 JSON 数组。",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2000,
        )
        last = getattr(gm._backend, "last_usage", {}) or {}
        from .usage import compute_cost
        cost = float(compute_cost(gm.api_id, gm._backend.model_name, last))
        ctl.add_usage(int(last.get("input_tokens", 0)), int(last.get("output_tokens", 0)), cost)
        entries = _parse_json(raw) or []
        if not isinstance(entries, list):
            entries = []
        count = 0
        with connect() as db:
            for entry in entries[:20]:
                if not isinstance(entry, dict) or not entry.get("name"):
                    continue
                db.execute(
                    """
                    insert into worldbook_entries(
                      book_id, script_id, title, keys, content, priority, enabled, metadata
                    ) values (%s, %s, %s, %s, %s, %s, true, %s)
                    on conflict do nothing
                    """,
                    (
                        book_id, script_id,
                        str(entry["name"])[:120],
                        Jsonb(entry.get("keys") or [entry["name"]]),
                        str(entry.get("content") or "")[:2000],
                        int(entry.get("priority") or 80),
                        Jsonb({"source": "llm_pipeline"}),
                    ),
                )
                count += 1
        ctl.update(stage_progress=1)
        return count
    except Exception:
        ctl.update(stage_progress=1)
        return 0


def _parse_json(text: str) -> Any:
    if not text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.I | re.M).strip()
    m = re.search(r"[\[\{].*[\]\}]", cleaned, re.S)
    if m:
        cleaned = m.group(0)
    try:
        return json.loads(cleaned)
    except Exception:
        return None
