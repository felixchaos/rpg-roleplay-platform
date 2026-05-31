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
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from psycopg.types.json import Jsonb

from .db import connect, expose, init_db

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
# 进程内 thread 跟踪表 (best-effort)。多 worker 部署时只对当前 worker 可见，
# 跨 worker 协调依赖 DB advisory lock (cluster.try_acquire_job_lock)。
# daemon thread 在 worker 退出时自动清理 — 不需要手动 cleanup。
_RUNNING: dict[str, threading.Thread] = {}  # job_id → thread


class MissingUserCredentialError(ValueError):
    """Raised when a paid/user-scoped LLM pipeline has no user credential."""

    def __init__(self, api_id: str, model: str, credential_api_id: str):
        self.api_id = api_id
        self.model = model
        self.credential_api_id = credential_api_id
        super().__init__("需要先配置自己的 API Key 后才能继续知识流水线")


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
    require_user_llm_credential(user_id)
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
        from .cluster import release_job_lock, try_acquire_job_lock
        if not try_acquire_job_lock(f"import_job:{job_id}"):
            # 已被别的 worker 占了，直接退出（那个 worker 会处理）
            return
    except Exception:
        try_acquire_job_lock = None  # type: ignore[assignment]
        release_job_lock = None  # type: ignore[assignment]

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

        # ── 阶段 2.5: story_phase LLM 推断（facts 后，一次 LLM call）────────
        if ctl.is_cancelled():
            return _finalize_cancelled(ctl)
        _stage_story_phase_llm(ctl, user_id, script_id)

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
                "select * from documents where script_id = %s and chapter_id = %s",
                (script_id, chapter["id"]),
            ).fetchone()
            if not doc_row:
                doc_row = knowledge._upsert_document(db, book, script, chapter)  # type: ignore[assignment]
            fact = knowledge._fact_from_chapter(chapter, summaries, known_names, known_locations, known_concepts)
            knowledge._upsert_chapter_fact(db, book, script, chapter, doc_row, fact)
            if (i + 1) % 10 == 0 or i == len(chapters) - 1:
                ctl.update(stage_progress=i + 1)
    return len(chapters)


def _resolve_extractor_llm(user_id: int) -> tuple[str, str]:
    """解析拆书流水线 LLM 配置。

    优先级:
      1. user_preferences["extractor.api_id"] / ["extractor.model_real_name"]
      2. user_preferences["agent.api_id"] / ["agent.model_real_name"]
      3. 默认: vertex_ai / gemini-3.5-flash

    返回 (api_id, model)。
    """
    from agents._harness import resolve_api_and_model
    api_id, model = resolve_api_and_model(
        user_id,
        api_pref_key="extractor.api_id",
        model_pref_key="extractor.model_real_name",
        default_api="vertex_ai",
        default_model="gemini-3.5-flash",
    )
    return _normalize_llm_api_id(api_id), model


def _normalize_llm_api_id(api_id: str) -> str:
    """Normalize legacy/UI provider ids to backend catalog ids."""
    value = (api_id or "").strip()
    if value in {"vertex", "vertex_ai", "agent_platform", "AgentPlatform"}:
        return "vertex_ai"
    return value


def _credential_api_id_for(api_id: str) -> str:
    return "AgentPlatform" if api_id == "vertex_ai" else api_id


def require_user_llm_credential(user_id: int) -> dict[str, str]:
    """Preflight paid LLM work before any import writes user-visible data."""
    api_id, model = _resolve_extractor_llm(user_id)
    _require_user_llm_credential(user_id, api_id, model)
    return {
        "api_id": api_id,
        "model": model,
        "credential_api_id": _credential_api_id_for(api_id),
    }


def _api_kind(api_id: str) -> str:
    try:
        from model_registry import find_api, load_model_catalog
        api = find_api(load_model_catalog(), api_id) or {}
        return str(api.get("kind") or api_id)
    except Exception:
        return api_id


def _has_user_llm_credential(user_id: int | None, api_id: str) -> bool:
    if not user_id:
        return False
    if _api_kind(api_id) == "vertex_ai" or api_id == "vertex_ai":
        try:
            from core.vertex_sa import has_user_sa
            return has_user_sa(int(user_id), "AgentPlatform")
        except Exception:
            return False
    try:
        from platform_app.user_credentials import get_credential
        cred = get_credential(int(user_id), api_id)
        return bool(cred and cred.get("key"))
    except Exception:
        return False


def _require_user_llm_credential(user_id: int, api_id: str, model: str) -> None:
    """Production import pipeline must use user-scoped credentials only."""
    if not _has_user_llm_credential(user_id, api_id):
        raise MissingUserCredentialError(api_id, model, _credential_api_id_for(api_id))


def _stage_story_phase_llm(ctl: JobController, user_id: int, script_id: int) -> None:
    """facts 完成后，一次 LLM call 把章节范围分到 开端/发展/高潮/结局/番外。
    成功 → 按范围批量 update chapter_facts.story_phase；
    失败/解析不出 → 全部回退 "未明"。
    """
    api_id, model = _resolve_extractor_llm(user_id)

    with connect() as db:
        rows = db.execute(
            "select chapter, summary, title from chapter_facts "
            "where script_id = %s and (story_phase = '' or story_phase is null) "
            "order by chapter",
            (script_id,),
        ).fetchall()

    if not rows:
        return

    total = len(rows)
    # 均匀采样 ≤30 章喂给 LLM (成本控)；保留每章的 chapter 号让模型按号给区间
    if total <= 30:
        sample = rows
    else:
        step = max(1, total // 30)
        sample = rows[::step][:30]
    lines = "\n".join(
        f"第{r['chapter']}章《{r['title']}》: {(r['summary'] or '')[:120]}"
        for r in sample
    )
    prompt = (
        f"这本书共 {total} 章 (第 1 章 — 第 {total} 章)，以下是均匀采样的章节摘要。"
        "请把章节范围划分到这 5 个阶段:开端 / 发展 / 高潮 / 结局 / 番外。"
        "不需要每个阶段都出现 — 只列实际存在的。番外通常出现在书末。\n\n"
        "返回严格 JSON 数组，每段一项,无任何前后文字:\n"
        '[{"phase":"开端","start":1,"end":N},{"phase":"发展","start":N+1,"end":M},...]\n\n'
        f"章节摘要:\n{lines}"
    )
    try:
        from agents._harness import call_agent_json
        raw, last = call_agent_json(
            api_id, model,
            "你是小说剧情分析器,只输出 JSON 数组。",
            prompt,
            user_id,
            max_tokens=400,
            agent_kind="import_pipeline",
        )
        from .usage import compute_cost
        cost = float(compute_cost(api_id, model, last))
        ctl.add_usage(int(last.get("input_tokens", 0)), int(last.get("output_tokens", 0)), cost)

        valid = {"开端", "发展", "高潮", "结局", "番外"}
        ranges = _parse_json(raw)
        if not isinstance(ranges, list) or not ranges:
            raise ValueError("phase ranges 非数组")

        with connect() as db:
            for item in ranges:
                if not isinstance(item, dict):
                    continue
                phase = str(item.get("phase", "")).strip()
                if phase not in valid:
                    continue
                try:
                    start = int(item.get("start") or 1)
                    end = int(item.get("end") or total)
                except (TypeError, ValueError):
                    continue
                db.execute(
                    "update chapter_facts set story_phase = %s "
                    "where script_id = %s and chapter between %s and %s "
                    "and (story_phase = '' or story_phase is null)",
                    (phase, script_id, start, end),
                )
            # 剩余没匹配到的章 → 未明
            db.execute(
                "update chapter_facts set story_phase = '未明' "
                "where script_id = %s and (story_phase = '' or story_phase is null)",
                (script_id,),
            )
    except Exception:
        # fallback: 写 "未明" 而不留空
        try:
            with connect() as db:
                db.execute(
                    "update chapter_facts set story_phase = '未明' "
                    "where script_id = %s and (story_phase = '' or story_phase is null)",
                    (script_id,),
                )
        except Exception:
            pass


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
            # v28: 显式 card_type='npc' 过滤,虽然 PC/persona 当前没 script_id 不会被命中,
            # 但避免未来加跨表用法时静默污染候选词表
            "select name, aliases from character_cards where script_id = %s and card_type = 'npc'",
            (script_id,),
        ).fetchall():
            existing_names.add(r["name"])
            existing_names.update(r.get("aliases") or [])

    full_text = "\n".join(c["content"] for c in chapters)
    # 候选：2-3 字中文连续词，且不在常见停用词里
    candidates = re.findall(r"[一-鿿]{2,3}", full_text)
    # task 47: 复用 session.py 的统一 blacklist,避免维护两份。包含 40+ 高频副词/
    # 连词/语气词("不知道/起来/有德的/不过/这时候/看起来"等)+ 盗版宣传残留。
    from platform_app.knowledge.session import _CHINESE_NON_NAME_BLACKLIST
    stop = set(_CHINESE_NON_NAME_BLACKLIST)
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

    简化：调 call_agent_json 让模型按 JSON schema 输出。
    超时/失败的角色跳过，不阻断整个流水线。
    """
    from . import knowledge
    api_id, model = _resolve_extractor_llm(user_id)

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
        int(book_row["id"]) if book_row else None

    # 拉该 script 的 chapter_facts（用摘要做二次 pass 输入）
    with connect() as db:
        fact_rows = db.execute(
            "select chapter, summary, characters from chapter_facts "
            "where script_id = %s order by chapter",
            (script_id,),
        ).fetchall()

    generated = 0
    for i, entity in enumerate(targets):
        if ctl.is_cancelled():
            raise RuntimeError("cancelled")
        name = entity["name"]

        # 优先用 chapter_facts 摘要（信噪比高），fallback 到原始章节文本片段
        relevant_summaries = []
        for fr in fact_rows:
            chars = fr.get("characters") or []
            if isinstance(chars, list) and any(
                isinstance(c, dict) and c.get("name") == name for c in chars
            ):
                relevant_summaries.append(f"第{fr['chapter']}章: {(fr['summary'] or '')[:200]}")
            if len(relevant_summaries) >= 8:
                break

        if relevant_summaries:
            context = "章节摘要（该角色相关）：\n" + "\n".join(relevant_summaries)
        else:
            snippets = []
            for ch in chapters_idx:
                if name in ch["content"]:
                    snippets.append(ch["content"][:1500])
                    if len(snippets) >= 3:
                        break
            if not snippets:
                ctl.update(stage_progress=i + 1)
                continue
            context = "文本片段：\n" + "\n---\n".join(snippets)

        # task 47: 显式让 LLM 判断"这是真人名吗",false 时直接跳过不写卡。
        # 2-3 字中文 ngram 候选有大量副词/连词/动词性短语(为什么/的声音/紧接着/有德的)
        # 维护硬编码 blacklist 永远跟不上内容,LLM 一个布尔判断成本极低且精度高。
        prompt = (
            f"分析「{name}」是否是真实的角色人名(不是副词/连词/动词/地名/物品/碎片),返回严格 JSON:\n"
            "如果不是真人名,返回 {\"is_character\": false}\n"
            "如果是真人名,返回 {\n"
            "  \"is_character\": true,\n"
            "  \"identity\": \"身份/职业/势力\",\n"
            "  \"appearance\": \"外貌描述\",\n"
            "  \"personality\": \"性格特点\",\n"
            "  \"speech_style\": \"说话风格\",\n"
            "  \"secrets\": \"秘密或重要伏笔(如无则空字符串)\",\n"
            "  \"aliases\": [\"别名1\"]\n"
            "}\n\n"
            + context
        )
        try:
            from agents._harness import call_agent_json
            raw, last = call_agent_json(
                api_id, model,
                "你是角色卡提取器,严格判断 name 是否为真实角色人名。只输出 JSON。",
                prompt,
                user_id,
                max_tokens=700,
                agent_kind="import_pipeline",
            )
            data = _parse_json(raw)
            # 累 usage(无论是否写卡,LLM 都跑了)
            from .usage import compute_cost
            cost = float(compute_cost(api_id, model, last))
            ctl.add_usage(int(last.get("input_tokens", 0)), int(last.get("output_tokens", 0)), cost)
            # task 47: LLM 明确说不是人名 → 跳过;identity 为空也判定为假名(双保险)
            if data and data.get("is_character") is not False and (data.get("identity") or "").strip():
                # 写入 character_cards(含 secrets 字段)
                knowledge.upsert_character_card(user_id, script_id, {
                    "name": name,
                    "aliases": data.get("aliases") or [],
                    "identity": data.get("identity") or "",
                    "appearance": data.get("appearance") or "",
                    "personality": data.get("personality") or "",
                    "speech_style": data.get("speech_style") or "",
                    "secrets": data.get("secrets") or "",
                    "metadata": {"source": "llm_pipeline", "freq": entity["count"]},
                })
                generated += 1
        except Exception:
            pass
        ctl.update(stage_progress=i + 1)
    return generated


def _stage_worldbook(ctl: JobController, user_id: int, script_id: int) -> int:
    """LLM 从 chapter_facts 摘要 + facts 提取世界观条目入 worldbook_entries。"""
    api_id, model = _resolve_extractor_llm(user_id)

    with connect() as db:
        book_row = db.execute(
            "select id from books where script_id = %s", (script_id,),
        ).fetchone()
        if not book_row:
            return 0
        book_id = int(book_row["id"])

        # 用 chapter_facts 摘要 + locations/factions/concepts 作为输入（比原始文本信噪比高）
        fact_rows = db.execute(
            "select chapter, summary, locations, factions, concepts "
            "from chapter_facts where script_id = %s order by chapter limit 40",
            (script_id,),
        ).fetchall()

    ctl.update(stage_progress=0, stage_total=1)

    if fact_rows:
        summaries_block = "\n".join(
            f"第{r['chapter']}章: {(r['summary'] or '')[:100]}"
            for r in fact_rows[:30]
        )
        # 聚合高频地点/势力/概念作为提示
        from collections import Counter as _Counter
        loc_cnt: _Counter = _Counter()
        fac_cnt: _Counter = _Counter()
        con_cnt: _Counter = _Counter()
        for r in fact_rows:
            for item in (r.get("locations") or []):
                if isinstance(item, dict):
                    loc_cnt[item.get("name", "")] += item.get("count", 1)
            for item in (r.get("factions") or []):
                if isinstance(item, dict):
                    fac_cnt[item.get("name", "")] += item.get("count", 1)
            for item in (r.get("concepts") or []):
                if isinstance(item, dict):
                    con_cnt[item.get("name", "")] += item.get("count", 1)
        top_locs = [n for n, _ in loc_cnt.most_common(10) if n]
        top_facs = [n for n, _ in fac_cnt.most_common(10) if n]
        top_cons = [n for n, _ in con_cnt.most_common(10) if n]
        hints = (
            f"高频地点: {', '.join(top_locs)}\n"
            f"高频势力: {', '.join(top_facs)}\n"
            f"高频概念: {', '.join(top_cons)}\n"
        )
        seed = hints + "\n章节摘要：\n" + summaries_block
    else:
        with connect() as db:
            chapters = db.execute(
                "select content from script_chapters where script_id = %s order by chapter_index",
                (script_id,),
            ).fetchall()
        seed = "\n".join(c["content"] for c in chapters)[:8000]

    # 读取新提取管线已落库的纪元(若存在),作为铁律塞进 prompt,治 _stage_worldbook 独立 LLM
    # 凭空编"哥本哈根研究所 2927年创立"这种带具体年份的 hallucination
    era_lock = ""
    with connect() as db:
        era_row = db.execute(
            "select content from worldbook_entries where script_id=%s and title='纪元' limit 1",
            (script_id,),
        ).fetchone()
        if era_row and era_row.get("content"):
            era_lock = str(era_row["content"])[:200]
    era_iron_rule = (
        f"【纪元铁律】{era_lock}\n严禁在 content 中编造具体的创立年/事件年份;"
        "若必须提及年代,只能引用上述纪元,**绝不写真实历史年份**(1927/1935/1940 等)。\n"
        if era_lock else
        "【纪元约束】不要在 content 中编造具体年份(避免幻觉);只描述背景/角色/地理/势力关系。\n"
    )
    prompt = (
        era_iron_rule +
        "根据下面的章节摘要和高频实体，提取重要的世界观条目（地点/势力/概念），返回严格 JSON 数组：\n"
        "[{\"name\":\"...\",\"keys\":[\"关键词1\",\"关键词2\"],\"content\":\"≤200字解释\",\"priority\":80}]\n"
        "数量上限 20。\n\n" + seed
    )
    try:
        from agents._harness import call_agent_json
        raw, last = call_agent_json(
            api_id, model,
            "你是世界书编辑，只输出 JSON 数组。",
            prompt,
            user_id,
            max_tokens=2000,
            agent_kind="import_pipeline",
        )
        from .usage import compute_cost
        cost = float(compute_cost(api_id, model, last))
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
