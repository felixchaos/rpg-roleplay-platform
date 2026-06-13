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
from typing import Any

from psycopg.types.json import Jsonb

from .db import connect, expose, init_db

# ── 阶段定义 ────────────────────────────────────────────────────────
# v29 (一站完成): wizard 末尾 chain LLM extract + 嵌入 → 用户上传后所有模块齐备
#   chunks/facts/entities/cards/worldbook 沿用旧路径,新增:
#   canon_extract → 弧段 LLM 抽 → 写 kb_canon_entities + 时间线 + canon-based worldbook
#   anchors       → 报告时间线条数(canon_extract 已写,这里只 verify+report)
#   embeddings    → 触发 chunks/cards/worldbook 向量化(canon embed 在 canon_extract 内已做)
STAGES = [
    ("chunks",        "切块入库"),
    ("facts",         "章节事实"),
    ("entities",      "人物提取"),
    ("cards",         "人设卡生成"),
    ("worldbook",     "世界书建立"),
    ("canon_extract", "规范实体提取"),
    ("anchors",       "时间线锚点"),
    ("embeddings",    "向量化"),
]


# ── 全局并发 semaphore（最多 2 个导入同时跑，第 3+ 个排队）──────────────
# 用 threading.Semaphore 而非 asyncio.Semaphore：流水线跑在 daemon thread 里，
# acquire() 在 worker thread 中阻塞，不占用 FastAPI event loop。
_IMPORT_GLOBAL_SEM = threading.Semaphore(2)
# 当前正在等待 semaphore 的任务数（排队深度）。原子 +1/-1 用 _QUEUE_LOCK。
_QUEUE_DEPTH: int = 0
_QUEUE_LOCK = threading.Lock()

# ── 进程内 thread 跟踪表（best-effort）──────────────────────────────
# 多 worker 部署时只对当前 worker 可见，
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


class MissingEmbeddingCredentialError(ValueError):
    """Raised when an embedding rebuild cannot run with the user's credentials."""

    def __init__(self, payload: dict[str, Any]):
        self.payload = dict(payload)
        self.api_id = str(payload.get("api_id") or "")
        self.model = str(payload.get("model") or "")
        self.credential_api_id = str(payload.get("credential_api_id") or self.api_id)
        super().__init__(
            str(payload.get("error") or payload.get("hint") or "需要先配置向量嵌入凭证")
        )


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

    # 字段名对齐 extract/budget.estimate + llm-extract/estimate 用的
    # est_input_tokens / est_output_tokens / est_usd / tokens_est / time_est_sec
    # (前端 Wizard ImportEstimateView 期望 tokens_est+time_est_sec)。
    # 同时保留旧 tokens_in/out/cost_usd 别名给老调用方,这版双写一段时间。
    def _stage(id_, label, ti, to, cost, eta, **extra):
        return {
            "id": id_, "label": label,
            "tokens_est": ti + to,
            "est_input_tokens": ti, "est_output_tokens": to,
            "est_usd": cost,
            "time_est_sec": eta,
            # 旧字段别名,兼容现有读取
            "tokens_in": ti, "tokens_out": to, "cost_usd": cost, "eta_sec": eta,
            **extra,
        }
    cards_cost = round((cards_input * input_price + cards_output * output_price) / 1_000_000, 4)
    wb_cost = round((wb_input * input_price + wb_output * output_price) / 1_000_000, 4)
    return {
        "ok": True,
        "model": {"api_id": model_api_id, "real_name": model_real_name, "pricing": pricing},
        "stages": [
            _stage("chunks",    "切块入库", 0, 0, 0.0, chapter_count / 100, deterministic=True),
            _stage("facts",     "章节事实", 0, 0, 0.0, chapter_count / 100, deterministic=True),
            _stage("entities",  "人物提取", 0, 0, 0.0, 0.5,                deterministic=True),
            _stage("cards",     "人设卡生成", cards_input, cards_output, cards_cost,
                   cards_calls * 3, enabled=enable_cards, calls=cards_calls),
            _stage("worldbook", "世界书建立", wb_input, wb_output, wb_cost,
                   worldbook_calls * 3, enabled=enable_worldbook, calls=worldbook_calls),
        ],
        # 全局聚合 — 同时给新名 + 老名
        "est_input_tokens": total_input,
        "est_output_tokens": total_output,
        "est_usd": round(cost_usd, 4),
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
        """部分更新当前 job 的字段（status/stage/stage_progress/...）

        phase_backend: 新增 warnings(jsonb), module/source/before_count/after_count/sub_kind 字段。
        """
        if not fields:
            return
        sets = []
        params: list[Any] = []
        for k, v in fields.items():
            if k in ("budget_estimate", "usage_actual", "stages", "warnings"):
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
        # kind='full_pipeline' — 区别于 llm_extract(纯 LLM 重提取);
        # 之前缺 kind 字段,list_jobs / kind 过滤会漏掉这类任务
        # 初始状态:若全局 semaphore 已被占满(当前等待数 > 0 或无空闲槽位)则先标 queued,
        # worker thread acquire 到 sem 后再改为 running。
        with _QUEUE_LOCK:
            initial_status = "queued" if _QUEUE_DEPTH > 0 else "pending"
        db.execute(
            """
            insert into import_jobs(job_id, user_id, script_id, kind, status, stage, overall_total, budget_estimate)
            values (%s, %s, %s, 'full_pipeline', %s, 'pending', %s, %s)
            """,
            (job_id, user_id, script_id, initial_status, len(STAGES), Jsonb(budget or {})),
        )

    options = {"enable_cards": enable_cards, "enable_worldbook": enable_worldbook}
    th = threading.Thread(target=_run_pipeline, args=(job_id, user_id, script_id, options), daemon=True)
    _RUNNING[job_id] = th
    th.start()
    return {"ok": True, "job_id": job_id, "reused": False}


def get_job_status(user_id: int, job_id: str | None = None, script_id: int | None = None) -> dict[str, Any]:
    """读 DB 拿任务状态。

    pending/running 阶段 import_jobs.usage_actual 一直是 {} (终态才写),
    所以这里现场聚合 token_usage(by user_id + metadata.script_id +
    created_at ≥ started_at)拼回 usage_actual,让 SSE 推到前端的进度
    每秒都有真实 token/cost 数。终态保持 DB 里写好的快照不动。
    """
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
        job = expose(row) or {}
        # SEC(M-11): 不把 Python 堆栈回传用户(泄露绝对路径/模块/库版本)。只留首行友好摘要,
        # 完整 traceback 仍在 server log。错误是写库时拼成 "msg\n<traceback>",截到首行即可。
        def _strip_trace(v):
            if isinstance(v, str):
                return v.split("\n", 1)[0][:300]
            if isinstance(v, list):
                return [_strip_trace(x) for x in v]
            return v
        if job.get("error"):
            job["error"] = _strip_trace(job["error"])
        if job.get("warnings"):
            job["warnings"] = _strip_trace(job["warnings"])
        status = (job.get("status") or "").strip()
        if status == "queued":
            # 计算排队位次：本 job 之前还有多少个 queued/pending/running 的导入任务
            # (id 更小的，即更早入队的)
            cur_id = job.get("id") or 0
            ahead_row = db.execute(
                "select count(*) as n from import_jobs "
                "where status in ('queued', 'pending', 'running') and id < %s",
                (cur_id,),
            ).fetchone()
            job["queue_position"] = int((ahead_row["n"] if ahead_row else 0))
        elif status in ("pending", "running") and job.get("script_id"):
            # 现算 token_usage 累计 — 终态不动(防覆盖正式快照)
            live = db.execute(
                """
                select coalesce(sum(cost_usd),0) as usd,
                       coalesce(sum(input_tokens),0) as in_tok,
                       coalesce(sum(output_tokens),0) as out_tok,
                       count(*) as calls
                from token_usage
                where user_id = %s
                  and (metadata->>'script_id')::bigint = %s
                  and created_at >= coalesce(%s, now() - interval '1 hour')
                """,
                (user_id, int(job["script_id"]), job.get("started_at")),
            ).fetchone()
            if live and (live["calls"] or 0) > 0:
                job["usage_actual"] = {
                    "usd": round(float(live["usd"] or 0), 4),
                    "input_tokens": int(live["in_tok"] or 0),
                    "output_tokens": int(live["out_tok"] or 0),
                    "llm_calls": int(live["calls"] or 0),
                    # 标记给前端:这是 in-flight 现算的,不是 job 结束的最终账
                    "live": True,
                }
    return {"ok": True, "found": True, "job": job}


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
    global _QUEUE_DEPTH

    # ── 全局并发限制：acquire semaphore（排队期间 blocking，但在 daemon thread 里，不卡 event loop）
    with _QUEUE_LOCK:
        _QUEUE_DEPTH += 1
    # 标记为 queued（如果还没标过）并写入当前排队深度
    try:
        init_db()
        with connect() as db:
            with _QUEUE_LOCK:
                pos = _QUEUE_DEPTH - 1  # 本任务自己占了最后一个槽，前面还有 pos 个
            db.execute(
                "update import_jobs set status='queued', updated_at=now() "
                "where job_id=%s and status not in ('running','done','done_with_errors','failed','cancelled')",
                (job_id,),
            )
    except Exception:
        pass

    _IMPORT_GLOBAL_SEM.acquire()  # 阻塞直到拿到槽（最多 2 个同时跑）

    with _QUEUE_LOCK:
        _QUEUE_DEPTH -= 1

    # 多 worker 部署：advisory lock 防止同 job 被多 worker 同时跑
    try:
        from .cluster import release_job_lock, try_acquire_job_lock
        if not try_acquire_job_lock(f"import_job:{job_id}"):
            # 已被别的 worker 占了，直接退出（那个 worker 会处理）
            _IMPORT_GLOBAL_SEM.release()
            return
    except Exception:
        try_acquire_job_lock = None  # type: ignore[assignment]
        release_job_lock = None  # type: ignore[assignment]

    # try 上提到 acquire() 之后第一句:JobController/init_db/connect 任一抛异常
    # 都必须经 finally 释放 semaphore,否则信号量永久 -1,两次后所有导入死锁在 acquire()。
    stages_progress = []
    try:
        ctl = JobController(job_id)
        ctl.update(status="running", stages=[{"id": s[0], "label": s[1], "status": "pending"} for s in STAGES])
        init_db()
        with connect() as db:
            db.execute("update import_jobs set started_at = now() where job_id = %s", (job_id,))

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

        # ── 阶段 2.6: phase_digests 聚合(把 chapter_facts 按 story_phase 合并)──
        # worldbook_agent.consult 强依赖 phase_digests 表来 resolve anchor + 算 confidence。
        # 之前这步只在 rpg/scripts/aggregate_phase_digests.py 手动跑,新 import 的 script
        # phase_digests 永远是空表 → 任何一轮 GM 翻阅都 confidence=0 报"未找到精确锚点"。
        if not ctl.is_cancelled():
            try:
                n_phases = _stage_phase_digests(script_id)
                import logging as _log
                _log.getLogger(__name__).info(
                    "[phase_digests] script_id=%s aggregated %d phases", script_id, n_phases,
                )
            except Exception as exc:
                import logging as _log
                _log.getLogger(__name__).warning(
                    "[phase_digests] aggregation failed: %s", exc, exc_info=True,
                )
                try:
                    ctl.update(warnings={
                        "stage": "phase_digests",
                        "exception": type(exc).__name__,
                        "message": str(exc)[:300],
                    })
                except Exception:
                    pass

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
            # phase_backend: 失败比例 >50% 标 error,主流程返 done_with_errors
            cards_failures = getattr(_stage_cards, "_last_llm_failures", 0)
            cards_targets = getattr(_stage_cards, "_last_targets", 0)
            cards_status = "done"
            if cards_targets and cards_failures > cards_targets // 2:
                cards_status = "error"
            stages_progress.append({
                "id": "cards", "status": cards_status, "count": cards_n,
                "failures": cards_failures, "targets": cards_targets,
            })
        else:
            stages_progress.append({"id": "cards", "status": "skipped"})
        ctl.update(stages=stages_progress, overall_progress=4)

        # ── 阶段 5: worldbook（LLM, 可关）─────────────────
        if options.get("enable_worldbook", True):
            if ctl.is_cancelled():
                return _finalize_cancelled(ctl)
            ctl.update(stage="worldbook")
            wb_n = _stage_worldbook(ctl, user_id, script_id)
            # phase_backend: worldbook 全部失败 (count=0) → 标 error
            wb_status = "done" if wb_n > 0 else "error"
            stages_progress.append({"id": "worldbook", "status": wb_status, "count": wb_n})
        else:
            stages_progress.append({"id": "worldbook", "status": "skipped"})
        ctl.update(stages=stages_progress, overall_progress=5)

        # ── 阶段 6: canon_extract（LLM,弧段抽规范实体 + 时间线 + canon-based worldbook）──
        # v29 一站完成: 不再要求用户跑两遍 wizard。wizard 末尾直接 chain arc_pipeline
        # 把 kb_canon_entities / script_timeline_anchors / canon-based worldbook /
        # canon embeddings 都跑完。任何 stage error 不让后续 stage 跪。
        if ctl.is_cancelled():
            return _finalize_cancelled(ctl)
        ctl.update(stage="canon_extract")
        canon_n, anchors_n, canon_stage_status, anchors_stage_status = _stage_canon_extract(
            ctl, user_id, script_id,
        )
        stages_progress.append({
            "id": "canon_extract", "status": canon_stage_status, "count": canon_n,
        })
        ctl.update(stages=stages_progress, overall_progress=6)

        # ── 阶段 7: anchors（canon_extract 已写,这里只报告 + verify)─────
        # canon_extract 失败 → anchors 跟着标 error;此阶段不发起新 LLM 调用。
        stages_progress.append({
            "id": "anchors", "status": anchors_stage_status, "count": anchors_n,
        })
        ctl.update(stages=stages_progress, overall_progress=7)

        # ── 阶段 8: embeddings（chunks/cards/worldbook 向量化,fire-and-forget)──
        if ctl.is_cancelled():
            return _finalize_cancelled(ctl)
        ctl.update(stage="embeddings")
        emb_status, emb_count = _stage_embeddings(ctl, user_id, script_id)
        stages_progress.append({
            "id": "embeddings", "status": emb_status, "count": emb_count,
        })
        ctl.update(stages=stages_progress, overall_progress=8)

        # 完成 — phase_backend: 任一 stage 标 error 时 status='done_with_errors'
        final_status = _final_stage_status(stages_progress)
        with connect() as db:
            db.execute(
                "update import_jobs set status=%s, stage='done', finished_at=now() where job_id=%s",
                (final_status, job_id),
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
        # 兜底:无论上面走哪条路径(正常 / 早退 / 异常 / 被吞的取消),都确保不留
        # status='running' 的僵尸行。已收尾的行 finalize 是 no-op(幂等)。
        finalize_job_if_unterminated(job_id)
        _RUNNING.pop(job_id, None)
        # 释放全局并发 semaphore，让下一个排队任务得以推进
        _IMPORT_GLOBAL_SEM.release()
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


# 终态:已收尾,无需再兜底。非终态(pending/queued/running)= worker 还应在跑或排队。
_TERMINAL_STATUSES = ("done", "done_with_errors", "failed", "cancelled")


def finalize_job_if_unterminated(job_id: str) -> str | None:
    """确定性收尾兜底 —— 放在 worker 的 finally 块里调用。

    worker 线程退出时(无论正常返回、早退 return、还是异常路径漏标),若 import_jobs
    行仍停在非终态,强制落终态 + finished_at,杜绝 status='running' 的僵尸行
    (前端"导入中"卡死 + 重启前"有无活跃导入"检查被误导)。

    判定:
      - cancel_requested=true → 'cancelled'(用户取消)
      - 否则 → 'failed'(线程已结束却没正常收尾:被吞的异常 / 早退漏标等)

    where 子句二次校验非终态,与正常收尾路径幂等、无竞态(谁先落终态谁赢,不互相覆盖)。
    本函数吞掉自身异常 —— 它是 finally 兜底,绝不能反过来 mask 掉原始异常。

    返回最终落的 status;已是终态(无需动)或行不存在返回 None。
    """
    try:
        init_db()
        with connect() as db:
            row = db.execute(
                "select status, cancel_requested from import_jobs where job_id = %s",
                (job_id,),
            ).fetchone()
            if not row:
                return None
            status = (row.get("status") or "").strip()
            if status in _TERMINAL_STATUSES:
                return None
            final = "cancelled" if row.get("cancel_requested") else "failed"
            note = "cancelled" if final == "cancelled" else "worker exited without finalizing"
            db.execute(
                "update import_jobs "
                "   set status = %s, "
                "       finished_at = coalesce(finished_at, now()), "
                "       error = case when coalesce(error, '') = '' then %s else error end, "
                "       updated_at = now() "
                " where job_id = %s "
                "   and status not in ('done', 'done_with_errors', 'failed', 'cancelled')",
                (final, note, job_id),
            )
        return final
    except Exception:
        import logging as _log
        _log.getLogger(__name__).warning(
            "[finalize_job_if_unterminated] failed for job_id=%s", job_id, exc_info=True,
        )
        return None


def reap_zombie_import_jobs(stale_hours: float | None = None) -> dict[str, Any]:
    """僵尸回收(startup self-heal)—— 把卡死的 running 行标 failed。

    场景:worker 线程被卡死(LLM 网络挂起 / 超时未触发 / 进程被 kill)→ finally 永不
    执行,job 永久停在 status='running'。本次部署就因 llm_63 取消后仍 running 卡了很久,
    只能靠 token_usage 活动旁证判活。startup 调一次兜底(本进程刚起,这些行绝无 worker
    真在跑),把"既无进度更新、又无 LLM 活动"的 running 行标 failed + finished_at。

    判定(需全部满足,避免误杀正在跑的长任务):
      - status = 'running'
      - kind <> 'knowledge_sync'(那类由 recover_pending_sync_jobs 走 resubmit 恢复,别抢)
      - 自身进度信号 coalesce(heartbeat_at, updated_at, started_at, created_at) 早于 N 小时前
      - 该 (user_id, script_id) 近 N 小时无 token_usage(LLM 仍在干活的旁证;
        script_id 为空的行不受此条约束,纯按时间判定)

    stale_hours: 默认读 env IMPORT_ZOMBIE_STALE_HOURS,缺省 6 小时。
    返回 {ok, reaped, jobs:[{job_id, kind, script_id}...]}。
    """
    import os
    if stale_hours is None:
        try:
            stale_hours = float(os.environ.get("IMPORT_ZOMBIE_STALE_HOURS", "6"))
        except (TypeError, ValueError):
            stale_hours = 6.0
    init_db()
    secs = float(stale_hours) * 3600.0
    with connect() as db:
        rows = db.execute(
            """
            update import_jobs j
               set status = 'failed',
                   finished_at = coalesce(j.finished_at, now()),
                   error = case when coalesce(j.error, '') = ''
                                then 'reaped_zombie_stale_running' else j.error end,
                   updated_at = now()
             where j.status = 'running'
               and j.kind <> 'knowledge_sync'
               and coalesce(j.heartbeat_at, j.updated_at, j.started_at, j.created_at)
                   < now() - make_interval(secs => %s)
               and not exists (
                   select 1 from token_usage tu
                    where tu.user_id = j.user_id
                      and j.script_id is not null
                      and (tu.metadata->>'script_id') = j.script_id::text
                      and tu.created_at > now() - make_interval(secs => %s)
               )
            returning j.job_id, j.kind, j.script_id
            """,
            (secs, secs),
        ).fetchall()
    reaped = [
        {"job_id": r["job_id"], "kind": r.get("kind"), "script_id": r.get("script_id")}
        for r in rows
    ]
    if reaped:
        import logging as _log
        _log.getLogger(__name__).warning(
            "[reap_zombie_import_jobs] reaped %d stale running job(s): %s",
            len(reaped), [r["job_id"] for r in reaped],
        )
    return {"ok": True, "reaped": len(reaped), "jobs": reaped}


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
        "请把章节范围划分到这 5 个阶段:开端 / 发展前期 / 发展中期 / 发展后期 / 结局。"
        "不需要每个阶段都出现 — 只列实际存在的。这 5 个 phase 标签是底座固定枚举,"
        "saves 出生点 wizard 和 GM 翻阅都依赖这套命名,**不要使用其他标签(如 高潮/番外/序章 等)**。\n\n"
        "返回严格 JSON 数组，每段一项,无任何前后文字:\n"
        '[{"phase":"开端","start":1,"end":N},{"phase":"发展前期","start":N+1,"end":M},...]\n\n'
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

        # 5 段固定枚举,与 saves wizard birthpoints fallback 完全一致 —
        # phase_label 是跨层共享 key (chapter_facts.story_phase / phase_digests.phase_label
        # / state.world.timeline.current_phase / worldbook_agent._resolve_anchor)。
        valid = {"开端", "发展前期", "发展中期", "发展后期", "结局"}
        ranges = _parse_json(raw)
        # LLM 返非 array (dict / None / 解析失败) 时退化为**5 段均分**,
        # 而不是塞全书单一 "发展" — 单 phase 会让 worldbook_agent.consult 在任何
        # current_phase 输入下都 fallback 到那同一段,phase_digests 索引等于失效。
        # 5 段均分至少保证 birthpoints / phase_digests / GM 翻阅各层 phase 一致。
        if not isinstance(ranges, list) or not ranges:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "[story_phase] LLM returned non-array %r, falling back to 5-bucket even split",
                type(ranges).__name__,
            )
            try:
                ctl.update(warnings={
                    "stage": "story_phase_llm",
                    "exception": "InvalidResponse",
                    "message": f"LLM 返回非数组(type={type(ranges).__name__}),已退化为 5 段均分",
                })
            except Exception:
                pass
            ranges = _even_split_phases(total)

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
            # 剩余没匹配到的章 → 走 5 段均分兜底而不是塞 "未明"
            # ("未明" 标签不在 valid 集合里,会让 phase_digests 聚合出无意义的
            # "未明" phase entry,worldbook_agent 命中后给 GM 注入空摘要)
            _backfill_unphased_with_even_split(db, script_id, total)
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[story_phase] LLM call failed, falling back to 5-bucket even split: %s",
            exc, exc_info=True,
        )
        try:
            with connect() as db:
                _backfill_unphased_with_even_split(db, script_id, total)
        except Exception as exc2:
            _logging.getLogger(__name__).warning(
                "[story_phase] fallback update failed: %s", exc2, exc_info=True,
            )
        try:
            ctl.update(warnings={
                "stage": "story_phase_llm",
                "exception": type(exc).__name__,
                "message": str(exc)[:300],
            })
        except Exception:
            pass


def _backfill_unphased_with_even_split(db, script_id: int, total: int) -> None:
    """LLM 推断失败/部分缺失时,把 story_phase 仍为空的章节按 5 段均分填回。
    避免 "未明" 标签污染 phase_digests。"""
    if total <= 0:
        return
    for item in _even_split_phases(total):
        db.execute(
            "update chapter_facts set story_phase = %s "
            "where script_id = %s and chapter between %s and %s "
            "and (story_phase = '' or story_phase is null)",
            (item["phase"], script_id, item["start"], item["end"]),
        )


def _even_split_phases(total: int) -> list[dict[str, Any]]:
    """把 total 章按 5 段均分:开端 / 发展前期 / 发展中期 / 发展后期 / 结局。

    与 saves wizard birthpoints fallback 用同一组 phase 标签,避免存档侧
    current_phase 和剧本侧 phase_digests 对不上号(常见症状:存档 timeline
    显示『开端』但 worldbook_agent 永远 fallback 到第一个 phase)。
    """
    if total <= 0:
        return []
    labels = ["开端", "发展前期", "发展中期", "发展后期", "结局"]
    # 章节 ≤ 5 时直接一一对应 + 截短
    if total <= 5:
        return [{"phase": labels[i], "start": i + 1, "end": i + 1} for i in range(total)]
    bucket = total // 5
    out = []
    for i, lab in enumerate(labels):
        s = i * bucket + 1
        e = (i + 1) * bucket if i < 4 else total  # 末段吃余数
        out.append({"phase": lab, "start": s, "end": e})
    return out


def _stage_phase_digests(script_id: int) -> int:
    """task 86: 把 chapter_facts 按 story_phase 聚合到 phase_digests。

    必须在 _stage_story_phase_llm 之后跑 — 该函数把 story_phase 字段填好。
    生成的 phase_digests 行供 worldbook_agent.consult 的 _resolve_anchor 使用,
    没有这步,新 import 的 script 永远 "未找到精确锚点"。

    实现源自 scripts/aggregate_phase_digests.py:aggregate_for_script,搬进 platform_app
    避免 import pipeline 依赖 scripts/ CLI 路径。
    """
    with connect() as db:
        rows = db.execute(
            """select chapter, story_phase, story_time_label, summary,
                   events, locations, characters
               from chapter_facts where script_id=%s order by chapter""",
            (script_id,),
        ).fetchall()
        if not rows:
            return 0

        by_phase: dict[str, list[dict]] = {}
        for r in rows:
            phase = (r["story_phase"] or "").strip() or "未分组"
            by_phase.setdefault(phase, []).append(dict(r))

        # 重跑前清表(避免重复 import 时残留旧 phase_label)
        db.execute("delete from phase_digests where script_id=%s", (script_id,))

        n = 0
        for phase, chapters in by_phase.items():
            chs = [c["chapter"] for c in chapters]
            cmin, cmax = min(chs), max(chs)
            summary_parts = []
            for c in chapters[:50]:
                s = (c.get("summary") or "").strip()
                if s:
                    summary_parts.append(f"第{c['chapter']}章 · {s[:120]}")
            summary = "\n".join(summary_parts)[:3000]
            tls = [c.get("story_time_label") or "" for c in chapters if c.get("story_time_label")]
            tl_start = tls[0] if tls else ""
            tl_end = tls[-1] if tls else ""
            ev_seen: set[str] = set()
            ev_entries: list[dict] = []
            for c in chapters:
                for ev in (c.get("events") or [])[:5]:
                    if isinstance(ev, dict):
                        text = str(ev.get("event") or "").strip()
                        if text and text not in ev_seen:
                            ev_seen.add(text)
                            ev_entries.append({"chapter": c["chapter"], "event": text})
            key_events = ev_entries[:30]
            loc_counter: Counter = Counter()
            for c in chapters:
                for loc in (c.get("locations") or []):
                    name = loc.get("name") if isinstance(loc, dict) else str(loc)
                    if name:
                        loc_counter[name] += loc.get("count", 1) if isinstance(loc, dict) else 1
            key_locations = [{"name": n_, "freq": cnt} for n_, cnt in loc_counter.most_common(15)]
            char_counter: Counter = Counter()
            for c in chapters:
                for ch in (c.get("characters") or []):
                    name = ch.get("name") if isinstance(ch, dict) else str(ch)
                    if name:
                        char_counter[name] += ch.get("count", 1) if isinstance(ch, dict) else 1
            key_characters = [{"name": n_, "freq": cnt} for n_, cnt in char_counter.most_common(15)]

            db.execute(
                """insert into phase_digests(
                  script_id, phase_label, chapter_min, chapter_max, summary,
                  key_events, key_locations, key_characters,
                  story_time_label_start, story_time_label_end, chapter_count
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (script_id, phase, cmin, cmax, summary,
                 Jsonb(key_events), Jsonb(key_locations), Jsonb(key_characters),
                 tl_start, tl_end, len(chapters)),
            )
            n += 1
        return n


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


def _final_stage_status(stages_progress: list[dict[str, Any]]) -> str:
    """phase_backend: 根据各 stage 是否有 error 决定 job 终态。
    返回 'done' / 'done_with_errors'。任何 stage 标 error → done_with_errors。
    """
    for s in stages_progress:
        if s.get("status") == "error":
            return "done_with_errors"
    return "done"


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

    # #5 角色卡去重: 预载该 script 现有 NPC 卡的 name/full_name/aliases(归一化)。
    # extract 时若候选角色(名或别名)已存在 → 跳过,避免"短名/全名"等变体产生重复卡。
    # 只跳过不覆盖 → 不会 clobber 用户手编卡;不改唯一索引/不迁移,零数据风险。
    def _norm_name(s: Any) -> str:
        return str(s or "").strip().casefold().replace(" ", "").replace("·", "").replace("・", "")
    existing_keys: set[str] = set()
    try:
        with connect() as db:
            for cr in db.execute(
                "select name, full_name, aliases from character_cards "
                "where script_id=%s and card_type='npc'",
                (script_id,),
            ).fetchall():
                existing_keys.add(_norm_name(cr.get("name")))
                if cr.get("full_name"):
                    existing_keys.add(_norm_name(cr.get("full_name")))
                for _a in (cr.get("aliases") or []):
                    existing_keys.add(_norm_name(_a))
        existing_keys.discard("")
    except Exception:
        existing_keys = set()

    generated = 0
    llm_failures = 0  # phase_backend: 累计 LLM 调用失败次数,>50% 标 partial
    for i, entity in enumerate(targets):
        if ctl.is_cancelled():
            raise RuntimeError("cancelled")
        name = entity["name"]

        # #5 去重(pre-LLM): 候选名已等于某现有卡的 name/别名 → 跳过,省 LLM 调用 + 不重复建卡。
        if _norm_name(name) in existing_keys:
            ctl.update(stage_progress=i + 1)
            continue

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
                "你是角色卡提取器,严格判断 name 是否为真实角色人名。只输出 JSON。"
                "【虚构铁律】本作是虚构小说,即使角色与真实历史人物同名,所有字段也"
                "**只能依据给定的章节片段/摘要**,严禁引入你自己的真实史实/生平/百科知识"
                "(如『活捉单于』『封冠军侯』这类片段里没有的内容),给不出片段依据就留空。",
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
                # #5 去重(post-LLM): 候选名或其别名已存在 → 跳过,不创建短名/全名变体重复卡。
                _cand_keys = {_norm_name(name)} | {_norm_name(a) for a in (data.get("aliases") or [])}
                _cand_keys.discard("")
                if _cand_keys & existing_keys:
                    ctl.update(stage_progress=i + 1)
                    continue
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
                existing_keys |= _cand_keys  # 防同一次 run 内后续变体重复建卡
        except Exception as exc:
            # phase_backend: 不再 silent swallow,记 warning(exc_info=True)
            # 同时累计 LLM 失败,>50% targets 全失败时主 worker 标 partial
            llm_failures += 1
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "[cards] LLM card for %r failed: %s", name, exc, exc_info=True,
            )
        ctl.update(stage_progress=i + 1)
    # 失败比例 >50% → 写 warnings 到 import_jobs,让 _run_pipeline 标 partial
    if targets and llm_failures > len(targets) // 2:
        try:
            ctl.update(
                warnings={
                    "stage": "cards",
                    "llm_failures": llm_failures,
                    "targets": len(targets),
                    "generated": generated,
                },
            )
        except Exception:
            pass
    # 返 (generated, llm_failures) 让 _run_pipeline 决定是否标 partial
    setattr(_stage_cards, "_last_llm_failures", llm_failures)
    setattr(_stage_cards, "_last_targets", len(targets))
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
        # phase_backend: 标记 worldbook 阶段写了多少条 — 0 当作 partial 让上层标 done_with_errors
        setattr(_stage_worldbook, "_last_count", count)
        return count
    except Exception as exc:
        # phase_backend: 不 silent swallow,把 LLM 失败写到 import_jobs.error + warnings
        import logging as _logging
        import traceback as _tb
        _logging.getLogger(__name__).warning(
            "[worldbook] LLM extract failed: %s", exc, exc_info=True,
        )
        try:
            ctl.update(
                stage_progress=1,
                error=f"_stage_worldbook: {type(exc).__name__}: {str(exc)[:300]}",
                warnings={
                    "stage": "worldbook",
                    "exception": type(exc).__name__,
                    "message": str(exc)[:500],
                    "traceback": _tb.format_exc()[:800],
                },
            )
        except Exception:
            pass
        setattr(_stage_worldbook, "_last_count", 0)
        return 0


def _stage_canon_extract(
    ctl: JobController, user_id: int, script_id: int,
) -> tuple[int, int, str, str]:
    """v29 (一站完成): 在 wizard 末尾 chain LLM 弧段提取。

    跑 extract.arc_pipeline.run_arc_extraction:
      - resolve_and_write → kb_canon_entities
      - build_timeline → script_timeline_anchors
      - build_constant_worldbook → canon-based worldbook_entries
      - embed_canon_entities → canon entity 向量

    返回 (canon_count, anchors_count, canon_status, anchors_status)。
    任何失败:写 warnings,把对应 stage 标 error,**不抛**给 _run_pipeline。
    """
    import logging as _logging
    import traceback as _tb
    _log = _logging.getLogger(__name__)
    api_id, model = _resolve_extractor_llm(user_id)

    # 读 book_id(canon_extract 必须有)
    with connect() as db:
        book_row = db.execute(
            "select b.id as book_id from books b "
            "where b.script_id = %s order by b.id limit 1",
            (script_id,),
        ).fetchone()
        if not book_row:
            _log.warning("[canon_extract] no book row for script %s", script_id)
            try:
                ctl.update(warnings={
                    "stage": "canon_extract",
                    "exception": "MissingBook",
                    "message": "无 book 记录,canon/anchors/worldbook 跳过",
                })
            except Exception:
                pass
            return 0, 0, "error", "error"
        book_id = int(book_row["book_id"])

    # 进度回调 — 把 arc_pipeline 的 stage 转成 stage_progress
    def _progress(stage: str, info: dict) -> None:
        try:
            if stage == "arc_extract" and "done" in info and "total" in info:
                ctl.update(
                    stage_progress=int(info.get("done") or 0),
                    stage_total=int(info.get("total") or 1),
                )
        except Exception:
            pass

    ctl.update(stage_progress=0, stage_total=1)
    try:
        from extract.arc_pipeline import run_arc_extraction
        result = run_arc_extraction(
            script_id, book_id,
            user_id=user_id,
            model=model, api_id=api_id,
            progress_cb=_progress,
        )
    except Exception as exc:
        _log.warning("[canon_extract] run_arc_extraction raised: %s", exc, exc_info=True)
        try:
            ctl.update(warnings={
                "stage": "canon_extract",
                "exception": type(exc).__name__,
                "message": str(exc)[:300],
                "traceback": _tb.format_exc()[:600],
            })
        except Exception:
            pass
        return 0, 0, "error", "error"

    if not result.get("ok"):
        err = str(result.get("error") or "unknown")
        _log.warning("[canon_extract] arc_pipeline returned !ok: %s", err)
        try:
            ctl.update(warnings={
                "stage": "canon_extract",
                "exception": "ArcPipelineFailed",
                "message": err[:300],
            })
        except Exception:
            pass
        # 部分写入(seed/部分 arc)可能有,从 DB 实际计数
        canon_n, anchors_n = _count_canon_and_anchors(script_id)
        return canon_n, anchors_n, "error", "error"

    canon_n, anchors_n = _count_canon_and_anchors(script_id)
    # 时间线为 0 不算 fatal — canon 写了就 ok,只把 anchors 标 error
    anchors_status = "done" if anchors_n > 0 else "error"
    canon_status = "done" if canon_n > 0 else "error"
    # canon 写完后回填 character_cards 的主角标识 + priority 排序
    # (cards stage 跑在 canon_extract 之前,当时 kb_canon_entities 是空,
    # 没法 join 排序,只能等 canon 写完再做)
    _rerank_cards_by_canon_importance(script_id)

    # 数据线接通: kb_canon_entities (LLM 抽过) → chapter_facts.events (启发式抽不出)。
    # 用户出生点选了 ch1 → harness 在 retrieval.py 已经会把 save_anchor_states 的
    # pending 锚点喂给 GM 做"命运式手段拉回剧情"。但 save_anchor_states 是从
    # chapter_facts.events 抽的,启发式 _extract_fact 在新剧本(没 known_names seed)
    # 时 events 全空 → ch1 永远没 anchor → GM 完全不知道该让卡切尔登场。
    # 这里用现成的 LLM 产物把 events 接回来,链路自然修通。
    try:
        _backfilled = _backfill_chapter_facts_events_from_canon(script_id)
        import logging as _log
        _log.getLogger(__name__).info(
            "[canon→facts] script_id=%s backfilled events for %d chapters",
            script_id, _backfilled,
        )
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).warning(
            "[canon→facts] backfill failed: %s", exc, exc_info=True,
        )
    ctl.update(stage_progress=1, stage_total=1)
    return canon_n, anchors_n, canon_status, anchors_status


def _stage_npc_voices(user_id: int, script_id: int, *, max_npc: int = 20, only_empty: bool = True) -> int:
    """LLM 抽 NPC 的 personality / speech_style / sample_dialogue 结构化字段,写回 character_cards。

    设计原则(harness):
      · 数据底座产出**结构化字段**,而不是注入原文片段让 GM 自学
      · GM 看 character_card 直接拿到 "性格: 平静、镇定、戏谑信息密度高"
        + "说话风格: 称玩家'被选中者',尾音常用'呢/呐'" + "台词示例: [...]"
      · 不依赖 GM 遵守 "学风格不复述" 这种 prompt 指令
      · LLM 抽的是高密度结构化数据,代价合理(每 NPC 一次 LLM,典型 30-100 NPC)

    数据源(给 LLM 的上下文):
      · character_card.identity + background + aliases (已抽过的结构化)
      · documents 反查 name + aliases 命中段 ×3,每段 ±400 字符(上下文足够丰富)

    LLM 输出 schema:
      {
        "personality": "≤80字性格特点,具体形容词+行为倾向",
        "speech_style": "≤80字说话风格,语气+常用词+句式特征",
        "sample_dialogue": ["原文里此人最有代表性的 2-3 句台词,逐字摘"]
      }

    args:
      script_id: 目标剧本
      max_npc: 单次最多处理几个 NPC (按 importance desc),避免一次跑爆
      only_empty: True 时只补 personality/speech_style 全空的卡,False 时全部覆盖重抽

    返回 backfilled count。
    """
    api_id, model = _resolve_extractor_llm(user_id)
    import logging as _log
    log = _log.getLogger(__name__)
    with connect() as db:
        where = "script_id=%s and card_type='npc'"
        params: list = [script_id]
        if only_empty:
            where += " and (coalesce(personality,'')='' or coalesce(speech_style,'')='')"
        rows = db.execute(
            f"select id, name, aliases, identity, background, "
            f"       first_revealed_chapter, importance "
            f"from character_cards where {where} "
            f"order by importance desc nulls last, priority desc nulls last "
            f"limit %s",
            (*params, int(max_npc)),
        ).fetchall() or []
    if not rows:
        return 0

    backfilled = 0
    for r in rows:
        name = r["name"]
        first_ch = int(r["first_revealed_chapter"] or 1)
        # 拉原文片段(用 lookup_entity 的 helper 逻辑,这里就地实现)
        try:
            with connect() as db:
                doc_rows = db.execute(
                    "select sc.chapter_index, d.content from documents d "
                    "join script_chapters sc on sc.id = d.chapter_id "
                    "where d.script_id=%s and sc.chapter_index between %s and %s "
                    "order by sc.chapter_index asc",
                    (script_id, first_ch, first_ch + 2),
                ).fetchall() or []
            aliases = r["aliases"] or []
            if isinstance(aliases, str):
                aliases = [a.strip() for a in aliases.split(",") if a.strip()]
            terms = [name] + [a for a in aliases if isinstance(a, str) and a]
            excerpts: list[str] = []
            for dr in doc_rows:
                content = dr["content"] or ""
                for term in terms:
                    idx = content.find(term)
                    if idx < 0:
                        continue
                    start = max(0, idx - 400)
                    end = min(len(content), idx + len(term) + 400)
                    excerpts.append(content[start:end].strip())
                    break
                if len(excerpts) >= 3:
                    break
            if not excerpts:
                log.info(f"[npc_voice] skip {name}: no source excerpts found")
                continue
        except Exception as exc:
            log.warning(f"[npc_voice] skip {name}: excerpt fetch failed: {exc}")
            continue

        prompt = (
            f"分析下述 NPC「{name}」在原文中的人物特征,产出**结构化**性格与说话风格,"
            f"用于 RPG 引擎让 GM 拿到结构化数据后准确扮演,**不要长篇大论解读**。\n\n"
            f"已知身份: {r['identity'] or '(空)'}\n"
            f"已知背景: {r['background'] or '(空)'}\n\n"
            f"原文片段(此 NPC 出场场景,可能不完整):\n"
            + "\n---\n".join(excerpts) + "\n\n"
            f"严格输出 JSON(无前后文字),字段必填:\n"
            f"{{\n"
            f'  "personality": "≤80字性格,用具体形容词+行为倾向(例:平静、戏谑、信息密度高、不轻易动情绪)",\n'
            f'  "speech_style": "≤80字说话风格,语气+常用词+句式特征(例:称对方为被选中者,尾音常带呢/呐,'
            f'冷峻陈述穿插戏谑反问)",\n'
            f'  "sample_dialogue": ["逐字摘原文 2-3 句最有代表性的台词,保留引号"]\n'
            f"}}"
        )
        try:
            from agents._harness import call_agent_json
            raw, last = call_agent_json(
                api_id, model,
                "你是 RPG 角色档案抽取器,只输出结构化 JSON,不解释。"
                "【虚构铁律】本作是虚构小说,即使角色与真实历史人物同名,也**只能依据上面给的原文片段**"
                "总结其性格/说话风格,严禁掺入你自己知道的真实史实/生平/百科;片段不足就写概括性短语,不要脑补。",
                prompt, user_id, max_tokens=500,
                agent_kind="import_pipeline",
            )
            data = _parse_json(raw)
            if not isinstance(data, dict):
                log.warning(f"[npc_voice] {name}: LLM returned non-dict {type(data).__name__}")
                continue
            personality = str(data.get("personality") or "").strip()[:200]
            speech_style = str(data.get("speech_style") or "").strip()[:200]
            sample = data.get("sample_dialogue") or []
            if not isinstance(sample, list):
                sample = []
            sample = [str(x)[:200] for x in sample[:5] if x]
            if not personality and not speech_style:
                log.info(f"[npc_voice] {name}: LLM 返回空 personality+speech_style, skip update")
                continue
            with connect() as db:
                db.execute(
                    "update character_cards set "
                    "  personality = case when length(%s) > 0 then %s else personality end, "
                    "  speech_style = case when length(%s) > 0 then %s else speech_style end, "
                    "  sample_dialogue = case when array_length(%s::text[], 1) > 0 then %s::jsonb else sample_dialogue end, "
                    "  updated_at = now() "
                    "where id = %s",
                    (personality, personality, speech_style, speech_style,
                     sample, json.dumps(sample, ensure_ascii=False), r["id"]),
                )
            backfilled += 1
            log.info(f"[npc_voice] {name}: 写回 personality={personality[:30]}... speech_style={speech_style[:30]}...")
        except Exception as exc:
            log.warning(f"[npc_voice] {name}: LLM call failed: {exc}")
            continue
    return backfilled


def _backfill_chapter_facts_events_from_canon(script_id: int) -> int:
    """把 kb_canon_entities 反向回填到 chapter_facts.events,把数据线接通。

    chapter_facts.events 由启发式 _extract_fact 产出 — 依赖 known_names seed,
    新剧本(无 seed)时 events 几乎全空 → save_anchor_states 漏抽 → harness
    pending_anchors 注入 GM 时缺关键钩子(如 ch1 卡切尔登场)。

    kb_canon_entities 是 LLM 抽过的:每个实体有 first_revealed_chapter + summary
    + importance + type。这里按 first_revealed_chapter 分组,每章把当章首次
    登场的实体合成 events 数组项,merge 进 chapter_facts.events。

    merge 策略:不覆盖已有 events 项(启发式抽出来的保留),只追加 canon 派生
    的 "实体登场" 事件,避免重复。
    """
    n_chapters = 0
    with connect() as db:
        # 1. 拉 canon entities 按章节分组 - 多拉 identity / background / aliases 给 anchor 注入
        # 用更多 hint。D20 等 item 类 summary 通常空,但 identity / aliases 一般有,
        # 拼进 anchor 文本让 GM 不会把"D20"按 d&d 训练偏见写成二十面骰子。
        ent_rows = db.execute(
            """select name, type, importance, first_revealed_chapter, summary,
                      identity, background, aliases
               from kb_canon_entities
               where script_id=%s and first_revealed_chapter is not null
                 and coalesce(importance, 0) >= 3
               order by first_revealed_chapter asc, importance desc nulls last""",
            (script_id,),
        ).fetchall() or []
        by_chapter: dict[int, list[dict[str, Any]]] = {}
        for r in ent_rows:
            ch = int(r["first_revealed_chapter"])
            by_chapter.setdefault(ch, []).append(dict(r))

        for chapter_num, ents in by_chapter.items():
            # 2. 拉本章现有 events,合并去重
            cf = db.execute(
                "select events from chapter_facts where script_id=%s and chapter=%s",
                (script_id, chapter_num),
            ).fetchone()
            if not cf:
                continue
            existing = cf["events"] or []
            if not isinstance(existing, list):
                existing = []
            # 已有 event.text 集合,canon 派生事件不重复添加
            seen_texts: set[str] = set()
            for e in existing:
                if isinstance(e, dict):
                    t = str(e.get("event") or "").strip()
                    if t:
                        seen_texts.add(t)
            # 3. 把 canon entities 合成 events (一个 entity = 一个 "X 在此章首次登场" 事件)
            added = 0
            new_events = list(existing)
            for ent in ents:
                name = (ent.get("name") or "").strip()
                if not name:
                    continue
                etype = ent.get("type") or "entity"
                summary = (ent.get("summary") or "").strip()
                identity = (ent.get("identity") or "").strip()
                background = (ent.get("background") or "").strip()
                aliases_raw = ent.get("aliases") or []
                if isinstance(aliases_raw, str):
                    aliases = [a.strip() for a in aliases_raw.split(",") if a.strip()]
                else:
                    aliases = [a for a in aliases_raw if isinstance(a, str) and a and a != name]
                imp = int(ent.get("importance") or 0)
                # 拼 entity hint: 优先 summary, 否则 identity, 都空 → background, 都空 → 无 hint
                # 再附 aliases (前 3 个 != name 的) 防 GM 按裸名脑补
                hint_parts: list[str] = []
                if summary:
                    hint_parts.append(summary[:120])
                elif identity:
                    hint_parts.append(identity[:120])
                elif background:
                    hint_parts.append(background[:120])
                if aliases[:3]:
                    hint_parts.append(f"别名: {', '.join(aliases[:3])}")
                hint = " / ".join(hint_parts)

                # 事件文本:type 不同模板不同
                if etype == "character":
                    ev_text = f"{name}({etype})首次登场"
                elif etype == "location":
                    ev_text = f"场景{name}首次出现"
                elif etype in ("concept", "item", "faction"):
                    ev_text = f"{etype}「{name}」首次引入"
                else:
                    ev_text = f"{name} 首次出现"
                if hint:
                    ev_text += f": {hint}"
                if ev_text in seen_texts:
                    continue
                seen_texts.add(ev_text)
                # importance 转 high/medium/low (anchor_seed _compute_importance 读这个)
                if imp >= 20:
                    sev = "high"
                elif imp >= 8:
                    sev = "medium"
                else:
                    sev = "low"
                new_events.append({
                    "scene_index": 0,
                    "event": ev_text[:300],
                    "participants": [name] if etype == "character" else [],
                    "locations": [name] if etype == "location" else [],
                    "concepts": [name] if etype in ("concept", "item", "faction") else [],
                    "importance": sev,
                    "evidence": summary[:180] if summary else "",
                    "_source": "canon_backfill",
                    "_canon_importance": imp,
                })
                added += 1
            if added > 0:
                db.execute(
                    "update chapter_facts set events = %s, updated_at = now() "
                    "where script_id=%s and chapter=%s",
                    (Jsonb(new_events), script_id, chapter_num),
                )
                n_chapters += 1
    return n_chapters


def _rerank_cards_by_canon_importance(script_id: int) -> None:
    """canon_extract 完成后,按 kb_canon_entities.importance 重排 character_cards.priority。

    - importance 最高的 character → 主角(priority=110, metadata.is_protagonist=true)
    - 其他配角 priority = max(50, 110 - canon_rank),按 importance desc 递减
    - metadata 写 canon_rank / canon_importance,前端可显示"主角 / 重要配角"等
    - cards 表里没在 canon 里的(LLM 没识别成 character entity)保持原 priority=100
    """
    try:
        with connect() as db:
            # 人工锁定的主角(metadata.protagonist_locked=true)优先于 canon importance:
            # 用户手动纠正过主角后,重新提取不能再按 LLM importance 把它覆盖回去
            # (见 character_cards.set_character_card_protagonist)。有锁时:① 锁定卡完全
            # 不动(WHERE 排除),② 其它 canon 卡一律 is_protagonist=false 且不抢 110 位。
            has_lock = bool(db.execute(
                "select 1 from character_cards "
                "where script_id=%s and card_type='npc' "
                "and coalesce((metadata->>'protagonist_locked')::boolean, false) limit 1",
                (script_id,),
            ).fetchone())
            db.execute(
                """
                with imp as (
                  select name, importance,
                         row_number() over (order by importance desc) as rk
                  from kb_canon_entities
                  where script_id=%(sid)s and type='character'
                )
                update character_cards cc
                set priority = case when %(has_lock)s then greatest(50, 110 - imp.rk)
                                    when imp.rk = 1 then 110
                                    else greatest(50, 110 - imp.rk) end,
                    metadata = cc.metadata || jsonb_build_object(
                        'is_protagonist', case when %(has_lock)s then false else imp.rk = 1 end,
                        'canon_importance', imp.importance,
                        'canon_rank', imp.rk
                    )
                from imp
                where cc.script_id=%(sid)s and cc.name = imp.name
                  and coalesce((cc.metadata->>'protagonist_locked')::boolean, false) = false
                """,
                {"sid": script_id, "has_lock": has_lock},
            )
    except Exception as exc:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "[cards] _rerank_cards_by_canon_importance failed for script %s: %s",
            script_id, exc,
        )


def _count_canon_and_anchors(script_id: int) -> tuple[int, int]:
    """读 DB 拿 (kb_canon_entities count, script_timeline_anchors count)。"""
    try:
        with connect() as db:
            c_row = db.execute(
                "select count(*) as c from kb_canon_entities where script_id = %s",
                (script_id,),
            ).fetchone()
            a_row = db.execute(
                "select count(*) as c from script_timeline_anchors where script_id = %s",
                (script_id,),
            ).fetchone()
            return int(c_row["c"]) if c_row else 0, int(a_row["c"]) if a_row else 0
    except Exception:
        return 0, 0


def _stage_embeddings(
    ctl: JobController, user_id: int, script_id: int,
) -> tuple[str, int]:
    """v29 一站完成: 触发 chunks / cards / worldbook 向量化。
    canon embedding 在 canon_extract 已做。embed_script 是 fire-and-forget,
    本 stage 等几秒看 chunks 进度,完成或部分完成都返 done — 后台线程继续跑。

    返回 (status, done_count)。partial 状态归 'done'(后台会继续)。
    """
    import logging as _logging
    import time as _time
    _log = _logging.getLogger(__name__)

    # 验证 embedding provider 可用
    try:
        from .knowledge.embedding import embed_script, embed_status
        result = embed_script(user_id, script_id)
    except Exception as exc:
        _log.warning("[embeddings] embed_script raised: %s", exc, exc_info=True)
        try:
            ctl.update(warnings={
                "stage": "embeddings",
                "exception": type(exc).__name__,
                "message": str(exc)[:300],
            })
        except Exception:
            pass
        return "error", 0

    if not result.get("ok"):
        err = str(result.get("error") or "embedding provider unavailable")
        _log.warning("[embeddings] embed_script !ok: %s", err)
        try:
            ctl.update(warnings={
                "stage": "embeddings",
                "exception": "EmbeddingProviderUnavailable",
                "message": err[:300],
            })
        except Exception:
            pass
        return "error", 0

    # 后台线程已在跑;轮询 ~30s 报进度,但不阻塞到全部完成(大书可能要分钟)
    try:
        status = embed_status(script_id) or {}
        chunks_total = int(((status.get("chunks") or {}).get("total")) or 0)
        ctl.update(stage_progress=0, stage_total=max(1, chunks_total))
        for _ in range(30):  # 最多等 30s
            if ctl.is_cancelled():
                return "done", 0
            status = embed_status(script_id) or {}
            chunks = status.get("chunks") or {}
            done = int(chunks.get("done") or 0)
            total = int(chunks.get("total") or 0)
            ctl.update(stage_progress=done, stage_total=max(1, total))
            running = bool(status.get("running"))
            if not running:
                # 后台已跑完(可能很快/小书)
                return "done", done
            if total > 0 and done >= total:
                return "done", done
            _time.sleep(1.0)
        # 30s 后后台仍在跑 — wizard 标 done,后台继续
        status = embed_status(script_id) or {}
        chunks = status.get("chunks") or {}
        return "done", int(chunks.get("done") or 0)
    except Exception as exc:
        _log.warning("[embeddings] polling failed: %s", exc, exc_info=True)
        return "done", 0


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


# ══════════════════════════════════════════════════════════════════════
#  phase_backend: 单模块 rebuild 函数(被 /rebuild/{module} 路由调用)
#  各 rebuild 返 {ok, before_count, after_count, partial_failures, source}
# ══════════════════════════════════════════════════════════════════════
def rebuild_chunks_from_db(user_id: int, script_id: int) -> dict[str, Any]:
    """零 LLM:重新切 document_chunks。从 script_chapters 读,清旧 chunks 写新。"""
    from . import knowledge
    partial_failures: list[dict[str, Any]] = []
    with connect() as db:
        before = db.execute(
            "select count(*) as c from document_chunks where script_id = %s",
            (script_id,),
        ).fetchone()
        before_count = int(before["c"]) if before else 0
        script = db.execute(
            "select * from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not script:
            return {"ok": False, "error": "无权访问该剧本"}
        book = knowledge._ensure_book(db, script)
        chapters = db.execute(
            "select * from script_chapters where script_id = %s order by chapter_index",
            (script_id,),
        ).fetchall()
        # 清旧
        db.execute("delete from document_chunks where script_id = %s", (script_id,))
        total = 0
        for chapter in chapters:
            try:
                doc = knowledge._upsert_document(db, book, script, chapter)
                for ci, content in enumerate(knowledge._chunk_text(chapter["content"])):
                    knowledge._insert_chunk(db, book, script, chapter, doc, ci, content)
                    total += 1
            except Exception as exc:
                partial_failures.append({
                    "chapter": chapter.get("chapter_index"),
                    "error": str(exc),
                })
    return {
        "ok": True, "source": "script_chapters",
        "before_count": before_count, "after_count": total,
        "partial_failures": partial_failures,
    }


def rebuild_facts_from_db(user_id: int, script_id: int) -> dict[str, Any]:
    """零 LLM:从 script_chapters 重抽 chapter_facts(规则匹配,不调 LLM)。"""
    from . import knowledge
    partial_failures: list[dict[str, Any]] = []
    with connect() as db:
        before = db.execute(
            "select count(*) as c from chapter_facts where script_id = %s",
            (script_id,),
        ).fetchone()
        before_count = int(before["c"]) if before else 0
        script = db.execute(
            "select * from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not script:
            return {"ok": False, "error": "无权访问该剧本"}
        book = knowledge._ensure_book(db, script)
        chapters = db.execute(
            "select * from script_chapters where script_id = %s order by chapter_index",
            (script_id,),
        ).fetchall()
        chars = knowledge._load_characters(script_id=script_id) or {}
        world = knowledge._load_world(script_id=script_id) or {}
        summaries = knowledge._load_summaries()
        known_names = knowledge._known_names(chars)
        known_locations = knowledge._known_locations(world)
        known_concepts = knowledge._known_concepts(world)
        total = 0
        for chapter in chapters:
            try:
                doc = db.execute(
                    "select * from documents where script_id = %s and chapter_id = %s",
                    (script_id, chapter["id"]),
                ).fetchone()
                if not doc:
                    doc = knowledge._upsert_document(db, book, script, chapter)
                fact = knowledge._fact_from_chapter(
                    chapter, summaries, known_names, known_locations, known_concepts,
                )
                knowledge._upsert_chapter_fact(db, book, script, chapter, doc, fact)
                total += 1
            except Exception as exc:
                partial_failures.append({
                    "chapter": chapter.get("chapter_index"),
                    "error": str(exc),
                })
    return {
        "ok": True, "source": "script_chapters",
        "before_count": before_count, "after_count": total,
        "partial_failures": partial_failures,
    }


def rebuild_cards_from_canon(user_id: int, script_id: int) -> dict[str, Any]:
    """LLM(可零 LLM 路径):从 kb_canon_entities 的 character 类回填 character_cards。
    无 canon 数据时退化为 _aggregate_characters_from_facts(零 LLM 词频)。
    """
    from . import knowledge
    from .knowledge.session import _aggregate_characters_from_facts
    partial_failures: list[dict[str, Any]] = []
    with connect() as db:
        before = db.execute(
            "select count(*) as c from character_cards "
            "where script_id = %s and card_type = 'npc'",
            (script_id,),
        ).fetchone()
        before_count = int(before["c"]) if before else 0
        script = db.execute(
            "select * from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not script:
            return {"ok": False, "error": "无权访问该剧本"}
        book = knowledge._ensure_book(db, script)
        # 优先用 canon entity (LLM extract 已有);否则用 facts 聚合
        canon_rows = db.execute(
            "select name, aliases, summary, importance from kb_canon_entities "
            "where script_id = %s and type = 'character' "
            "order by importance desc nulls last",
            (script_id,),
        ).fetchall()
        source = "canon"
        if canon_rows:
            chars: dict[str, Any] = {}
            for r in canon_rows:
                nm = (r.get("name") or "").strip()
                if not nm:
                    continue
                chars[nm] = {
                    "name": nm,
                    "identity": (r.get("summary") or "")[:200],
                    "appearance": "",
                    "personality": "",
                    "speech_style": "",
                    "current_status": "",
                    "secrets": "",
                    "sample_dialogue": [],
                    "priority": int(r.get("importance") or 0),
                    "aliases": list(r.get("aliases") or []),
                }
        else:
            source = "chapter_facts"
            try:
                chars = _aggregate_characters_from_facts(script_id)
            except Exception as exc:
                partial_failures.append({"stage": "aggregate", "error": str(exc)})
                chars = {}
        from .knowledge._sync import _sync_character_cards
        try:
            after_count = _sync_character_cards(db, book, script, chars)
        except Exception as exc:
            partial_failures.append({"stage": "_sync_character_cards", "error": str(exc)})
            after_count = 0
    return {
        "ok": True, "source": source,
        "before_count": before_count, "after_count": after_count,
        "partial_failures": partial_failures,
    }


def rebuild_worldbook_with_llm(user_id: int, script_id: int, *,
                                source: str = "canon") -> dict[str, Any]:
    """worldbook 重建。source='canon' 零 LLM(rebuild_worldbook_from_db),
    'llm' 走 _stage_worldbook 一次 LLM 调用 + 写库。"""
    partial_failures: list[dict[str, Any]] = []
    with connect() as db:
        before = db.execute(
            "select count(*) as c from worldbook_entries where script_id = %s",
            (script_id,),
        ).fetchone()
        before_count = int(before["c"]) if before else 0
        owned = db.execute(
            "select 1 from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not owned:
            return {"ok": False, "error": "无权访问该剧本"}
    if source == "canon":
        with connect() as db:
            from extract.rebuild import rebuild_worldbook_from_db
            res = rebuild_worldbook_from_db(db, script_id)
        if not res.get("ok"):
            return {
                "ok": False, "source": "canon",
                "before_count": before_count, "after_count": before_count,
                "error": res.get("error"),
                "partial_failures": partial_failures,
            }
        with connect() as db:
            after_row = db.execute(
                "select count(*) as c from worldbook_entries where script_id = %s",
                (script_id,),
            ).fetchone()
        return {
            "ok": True, "source": "canon",
            "before_count": before_count,
            "after_count": int(after_row["c"]) if after_row else 0,
            "partial_failures": partial_failures,
        }
    # source == 'llm' — 走 _stage_worldbook (一次 LLM)。job_id 由调用方传入 ctl
    raise NotImplementedError(
        "rebuild_worldbook_with_llm(source='llm') 必须从 rebuild job runner 调用,"
        "需要 JobController 上下文以记 usage_actual"
    )


# ══════════════════════════════════════════════════════════════════════
#  phase_backend: rebuild job 调度器 (kind='rebuild_*' 写 import_jobs)
# ══════════════════════════════════════════════════════════════════════
REBUILD_MODULES = {
    "chunks":        ("rebuild_chunks",       "切块重建",     False),
    "chapter-facts": ("rebuild_facts",        "章节事实重建", False),
    "canon":         ("rebuild_canon",        "规范实体重建", True),
    "cards":         ("rebuild_cards",        "角色卡重建",   True),
    "worldbook":     ("rebuild_worldbook",    "世界书重建",   True),  # may be True or False depending on source
    "anchors":       ("rebuild_anchors",      "时间线重建",   False),
    "embeddings":    ("rebuild_embeddings",   "向量重嵌入",   False),
}


def normalize_rebuild_module(module: str) -> str:
    value = str(module or "").strip()
    if value == "chapter_facts":
        return "chapter-facts"
    if value == "full_pipeline":
        return "full-pipeline"
    return value


def _embedding_preflight_or_raise(user_id: int) -> dict[str, Any]:
    from .knowledge.embedding import embedding_preflight

    payload = embedding_preflight(user_id)
    if not payload.get("ok"):
        raise MissingEmbeddingCredentialError(payload)
    return payload


def _embedding_prereq(user_id: int) -> dict[str, Any]:
    from .knowledge.embedding import embedding_preflight

    payload = embedding_preflight(user_id)
    return {
        "key": "embedding_credentials",
        "label": "向量嵌入凭证",
        "ok": bool(payload.get("ok")),
        "hint": payload.get("hint") or payload.get("error") or "",
        "api_id": payload.get("api_id"),
        "model": payload.get("model"),
        "credential_api_id": payload.get("credential_api_id"),
        "needs_credentials": bool(payload.get("needs_credentials")),
    }


def estimate_module_rebuild(
    user_id: int, script_id: int, module: str, *, body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Estimate a module rebuild and expose blocking prereqs for the UI modal."""
    init_db()
    module = normalize_rebuild_module(module)
    body = dict(body or {})
    if module not in REBUILD_MODULES:
        raise ValueError(f"unknown module: {module}")

    def _scalar(db, sql: str) -> int:
        row = db.execute(sql, (script_id,)).fetchone()
        return int(row["c"]) if row else 0

    with connect() as db:
        script = db.execute(
            "select id, chapter_count from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not script:
            raise ValueError("无权访问该剧本")

        chapter_count = int(script.get("chapter_count") or 0)
        chunks_total = _scalar(db, "select count(*) as c from document_chunks where script_id = %s")
        chunks_done = _scalar(
            db,
            "select count(*) as c from document_chunks "
            "where script_id = %s and embedding_vec is not null",
        )
        canon_total = _scalar(db, "select count(*) as c from kb_canon_entities where script_id = %s")
        canon_done = _scalar(
            db,
            "select count(*) as c from kb_canon_entities "
            "where script_id = %s and embedding is not null",
        )
        cards_total = _scalar(
            db,
            "select count(*) as c from character_cards "
            "where script_id = %s and card_type='npc'",
        )
        cards_done = _scalar(
            db,
            "select count(*) as c from character_cards "
            "where script_id = %s and card_type='npc' and embedding_vec is not null",
        )
        wb_total = _scalar(db, "select count(*) as c from worldbook_entries where script_id = %s")
        wb_done = _scalar(
            db,
            "select count(*) as c from worldbook_entries "
            "where script_id = %s and embedding_vec is not null",
        )

    prereqs: list[dict[str, Any]] = []
    affects: list[str] = []
    note = ""
    model: str | None = None

    kind, _label, needs_llm = REBUILD_MODULES[module]
    source_pref = str(body.get("source") or body.get("mode") or "").lower()
    # worldbook 默认 canon(零 LLM),仅 source=='llm' 才走 LLM —— 与 runner 对齐。之前只在显式
    # source=='canon' 才置 False,默认(无 source)被误判需 LLM → 估算显示模型 + 误加 LLM 凭证
    # prereq,且漏掉「canon 为空」阻断,用户确认后 runner 用 canon 跑必 failed。
    if module == "worldbook":
        needs_llm = (source_pref == "llm")
    if module == "canon" and source_pref == "resolve_only":
        needs_llm = False

    if module == "embeddings":
        includes = [str(x) for x in (body.get("include") or ["chunks", "cards", "worldbook", "canon"])]
        pre = _embedding_prereq(user_id)
        prereqs.append(pre)
        model = str(pre.get("model") or "")
        target_pairs = {
            "chunks": (chunks_done, chunks_total, "document_chunks.embedding_vec"),
            "cards": (cards_done, cards_total, "character_cards.embedding_vec"),
            "worldbook": (wb_done, wb_total, "worldbook_entries.embedding_vec"),
            "canon": (canon_done, canon_total, "kb_canon_entities.embedding"),
        }
        target_total = 0
        target_done = 0
        for name in includes:
            done, total, table = target_pairs.get(name, (0, 0, name))
            target_done += done
            target_total += total
            affects.append(table)
        if "chunks" in includes and chunks_total == 0:
            prereqs.append({
                "key": "chunks",
                "label": "章节切块",
                "ok": False,
                "hint": "当前剧本还没有章节切块,请先重做「章节切片」。",
                "count": 0,
                "total": max(chapter_count, 1),
            })
        note = f"将检查 {target_total} 条向量目标,当前已完成 {target_done} 条。"
    else:
        affects = {
            "chunks": ["document_chunks"],
            "chapter-facts": ["chapter_facts"],
            "canon": ["kb_canon_entities"],
            "cards": ["character_cards"],
            "worldbook": ["worldbook_entries"],
            "anchors": ["script_timeline_anchors"],
        }.get(module, [kind])
        if module in {"chapter-facts", "anchors"} and chunks_total == 0:
            prereqs.append({
                "key": "chunks",
                "label": "章节切块",
                "ok": False,
                "hint": "当前剧本还没有章节切块,请先重做「章节切片」。",
                "count": chunks_total,
                "total": max(chapter_count, 1),
            })
        # cards 永远从 canon 重建;worldbook 默认 canon(仅 source=='llm' 走 LLM)。只要「有效
        # source」是 canon 且 canon 为空,就给**阻断** prereq —— 否则用户确认后 runner 用 canon
        # 跑必 failed「kb_canon_entities 为空」,前端表现为「点了没反应」(群反馈行者无疆)。
        _uses_canon = (module == "cards") or (module == "worldbook" and source_pref != "llm")
        if _uses_canon and canon_total == 0:
            prereqs.append({
                "key": "canon",
                "label": "规范实体",
                "ok": False,
                "hint": "当前没有规范实体(知识库人物为空),请先重做「知识库人物」"
                        + ("(或将世界书来源改为 LLM 生成)。" if module == "worldbook" else "。"),
            })
        if needs_llm:
            api_id, llm_model = _resolve_extractor_llm(user_id)
            model = llm_model
            if not _has_user_llm_credential(user_id, api_id):
                prereqs.append({
                    "key": "llm_credentials",
                    "label": "LLM API Key",
                    "ok": False,
                    "hint": "请先在「设置 → API 设置」配置知识提取模型的 API Key。",
                    "api_id": api_id,
                    "model": llm_model,
                    "credential_api_id": _credential_api_id_for(api_id),
                    "needs_credentials": True,
                })
        note = "该模块将作为后台任务运行,可关闭页面后回来查看进度。"

    return {
        "ok": True,
        "script_id": script_id,
        "module": module,
        "kind": kind,
        "tokens_est": 0,
        "cost_est": 0.0,
        "model": model,
        "affects": affects,
        "prereqs": prereqs,
        "note": note,
    }


def schedule_module_rebuild(
    user_id: int, script_id: int, module: str,
    *, body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """异步调度单模块重建。返 {ok, job_id}。"""
    init_db()
    module = normalize_rebuild_module(module)
    body = dict(body or {})
    if module not in REBUILD_MODULES:
        raise ValueError(f"unknown module: {module}")
    kind, action_label, needs_llm = REBUILD_MODULES[module]
    if needs_llm:
        # canon 默认走 LLM;worldbook **默认 canon(零 LLM)**,仅 source=='llm' 才需 LLM ——
        # 必须与 _run_module_rebuild 的 `src = source or "canon"` 对齐。否则默认(无 source)的
        # worldbook 会被误要求 LLM 凭证(没配 key 直接 credentials_required),而 runner 实际走
        # canon → 表现为「点生成世界书没反应/报错」。
        source_pref = str(body.get("source") or body.get("mode") or "").lower()
        if module == "worldbook":
            needs_llm = (source_pref == "llm")
        if module == "canon" and source_pref == "resolve_only":
            needs_llm = False
    if needs_llm:
        require_user_llm_credential(user_id)
    with connect() as db:
        owned = db.execute(
            "select 1 from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not owned:
            raise ValueError("无权访问该剧本")

    if module == "embeddings":
        _embedding_preflight_or_raise(user_id)

    with connect() as db:
        existing = db.execute(
            "select job_id from import_jobs "
            "where user_id = %s and script_id = %s and kind = %s "
            "and status in ('pending', 'running') order by id desc limit 1",
            (user_id, script_id, kind),
        ).fetchone()
        if existing:
            return {"ok": True, "job_id": existing["job_id"], "reused": True}
        job_id = f"rb_{module}_{script_id}_{secrets.token_hex(6)}"
        db.execute(
            """
            insert into import_jobs(
              job_id, user_id, script_id, kind, status, stage,
              module, sub_kind, overall_total, budget_estimate, stages
            ) values (%s, %s, %s, %s, 'pending', 'pending',
                      %s, %s, 1, %s, %s)
            """,
            (
                job_id, user_id, script_id, kind,
                module, kind,
                Jsonb({"options": body, "action": action_label}),
                Jsonb([{"id": module, "label": action_label, "status": "pending"}]),
            ),
        )
    th = threading.Thread(
        target=_run_module_rebuild,
        args=(job_id, user_id, script_id, module, body),
        daemon=True,
    )
    th.start()
    return {"ok": True, "job_id": job_id, "reused": False, "module": module, "kind": kind}


def _run_module_rebuild(
    job_id: str, user_id: int, script_id: int, module: str, body: dict[str, Any],
) -> None:
    """rebuild worker。统一 import_jobs + SSE,失败标 failed,
    partial 失败标 done_with_errors,写 before/after_count + warnings。"""
    ctl = JobController(job_id)
    ctl.update(status="running", stage=module, overall_progress=0)
    with connect() as db:
        db.execute("update import_jobs set started_at = now() where job_id = %s", (job_id,))
    try:
        source = str(body.get("source") or body.get("mode") or "")
        if module == "chunks":
            result = rebuild_chunks_from_db(user_id, script_id)
        elif module == "chapter-facts":
            result = rebuild_facts_from_db(user_id, script_id)
        elif module == "cards":
            result = rebuild_cards_from_canon(user_id, script_id)
        elif module == "canon":
            # full = 重抽 LLM;resolve_only = 从 chapter_extracts 重 cluster (零 LLM)
            from extract.rebuild import rebuild_canon_resolve_from_facts
            if source == "resolve_only":
                with connect() as db:
                    result = rebuild_canon_resolve_from_facts(db, script_id)
            else:
                # full LLM: 走 schedule_llm_extraction 同款 (但这里直接调底层)
                from platform_app.knowledge.llm_extract import run_llm_extraction
                before = _count(db, "kb_canon_entities", script_id)
                r = run_llm_extraction(
                    user_id, script_id,
                    algorithm=str(body.get("algorithm") or "arc"),
                    model=str(body.get("model") or "deepseek-v4-flash"),
                    api_id=str(body.get("api_id") or "deepseek"),
                    confirmed=True,
                )
                after = _count(db, "kb_canon_entities", script_id) if r.get("ok") else before
                result = {
                    "ok": bool(r.get("ok")),
                    "source": "llm_extract",
                    "before_count": before,
                    "after_count": after,
                    "partial_failures": [],
                    "error": r.get("error") if not r.get("ok") else "",
                }
        elif module == "worldbook":
            src = source or "canon"
            if src == "llm":
                # 走 import pipeline 的 _stage_worldbook(单次 LLM)
                with connect() as db:
                    before = db.execute(
                        "select count(*) as c from worldbook_entries where script_id = %s",
                        (script_id,),
                    ).fetchone()
                    before_count = int(before["c"]) if before else 0
                count = _stage_worldbook(ctl, user_id, script_id)
                result = {
                    "ok": count > 0, "source": "llm",
                    "before_count": before_count, "after_count": count,
                    "partial_failures": [] if count > 0 else [
                        {"stage": "worldbook", "error": "LLM returned 0 entries"}
                    ],
                }
            else:
                result = rebuild_worldbook_with_llm(user_id, script_id, source="canon")
        elif module == "anchors":
            with connect() as db:
                before = db.execute(
                    "select count(*) as c from script_timeline_anchors where script_id = %s",
                    (script_id,),
                ).fetchone()
                before_count = int(before["c"]) if before else 0
                from extract.rebuild import rebuild_timeline_from_db
                r = rebuild_timeline_from_db(db, script_id)
                after = db.execute(
                    "select count(*) as c from script_timeline_anchors where script_id = %s",
                    (script_id,),
                ).fetchone()
                after_count = int(after["c"]) if after else 0
                result = {
                    "ok": bool(r.get("ok")),
                    "source": "chapter_facts",
                    "before_count": before_count,
                    "after_count": after_count,
                    "partial_failures": [],
                    "error": r.get("error") if not r.get("ok") else "",
                }
        elif module == "embeddings":
            includes = list(body.get("include") or ["chunks", "cards", "worldbook", "canon"])
            from .knowledge import embedding as _embed
            from extract.embed import embed_canon_entities
            counts = {}
            partial_failures = []
            with connect() as db:
                if "chunks" in includes or "cards" in includes or "worldbook" in includes:
                    try:
                        # embed_script fire-and-forget;这里改成同步等(rebuild 等任务跑完)
                        # 但为简洁同复用现有线程模型,直接调 sub-rountines
                        # 不直接调:embed_script 已是异步 dispatch,只确认凭证有效
                        _ = _embed.embed_status(script_id)
                    except Exception as exc:
                        partial_failures.append({"stage": "embed_check", "error": str(exc)})
                if "canon" in includes:
                    try:
                        emb = embed_canon_entities(db, script_id, user_id=user_id)
                        counts["canon"] = emb
                    except Exception as exc:
                        partial_failures.append({"stage": "embed_canon", "error": str(exc)})
            # 触发后台 embed_script(chunks/cards/worldbook)
            try:
                from .knowledge import embedding as _embed2
                _embed2.embed_script(user_id, script_id)
            except Exception as exc:
                partial_failures.append({"stage": "embed_script", "error": str(exc)})
            with connect() as db:
                done = db.execute(
                    "select count(*) as c from document_chunks "
                    "where script_id = %s and embedding_vec is not null",
                    (script_id,),
                ).fetchone()
                total = db.execute(
                    "select count(*) as c from document_chunks where script_id = %s",
                    (script_id,),
                ).fetchone()
            result = {
                "ok": True, "source": "pgvector",
                "before_count": int(done["c"]) if done else 0,
                "after_count": int(total["c"]) if total else 0,
                "partial_failures": partial_failures,
                "extra": counts,
            }
        else:
            result = {"ok": False, "error": f"unhandled module: {module}"}
        # 写终态 — 任何 partial_failures 标 done_with_errors
        partial_failures = list(result.get("partial_failures") or [])
        ok = bool(result.get("ok"))
        final_status = "done" if ok and not partial_failures else (
            "failed" if not ok else "done_with_errors"
        )
        ctl.update(
            status=final_status,
            stage="done",
            overall_progress=1,
            stage_progress=1,
            stage_total=1,
            source=str(result.get("source") or ""),
            before_count=int(result.get("before_count") or 0),
            after_count=int(result.get("after_count") or 0),
            warnings=partial_failures,
            error=str(result.get("error") or "")[:500],
            stages=[{
                "id": module,
                "label": REBUILD_MODULES[module][1],
                "status": "error" if final_status == "failed" else "done",
                "before_count": int(result.get("before_count") or 0),
                "after_count": int(result.get("after_count") or 0),
            }],
        )
        with connect() as db:
            db.execute(
                "update import_jobs set finished_at=now() where job_id=%s", (job_id,),
            )
    except Exception as exc:
        import traceback as _tb
        import logging as _logging
        _logging.getLogger(__name__).exception(
            "_run_module_rebuild %s failed: %s", job_id, exc,
        )
        ctl.update(
            status="failed",
            error=f"{type(exc).__name__}: {str(exc)[:400]}",
            warnings={
                "stage": module,
                "exception": type(exc).__name__,
                "traceback": _tb.format_exc()[:800],
            },
        )
        with connect() as db:
            db.execute(
                "update import_jobs set finished_at=now() where job_id=%s", (job_id,),
            )


def _count(db, table: str, script_id: int) -> int:
    row = db.execute(
        f"select count(*) as c from {table} where script_id = %s", (script_id,),
    ).fetchone()
    return int(row["c"]) if row else 0
