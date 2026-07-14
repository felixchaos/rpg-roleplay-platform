"""import_pipeline.runner — 阶段定义/全局并发信号量/公共入口/完整流水线 worker/收尾兜底

来源: 原 rpg/platform_app/import_pipeline.py STAGES + semaphore 状态 + schedule_full_import…list_jobs + _run_pipeline + finalize/reap(原 L32-113, 297-847) 区段,纯机械搬家(函数体逐字未动),零行为变化。
"""
from __future__ import annotations

import secrets
import threading
from typing import Any

from psycopg.types.json import Jsonb

from ..db import connect, expose, init_db
from .control import JobController
from .stages_core import (
    _final_stage_status,
    _stage_canon_extract,
    _stage_chunks,
    _stage_embeddings,
    _stage_entities,
    _stage_facts,
    _stage_phase_digests,
)
from .stages_llm import (
    _stage_cards,
    _stage_story_phase_llm,
    _stage_worldbook,
    require_user_llm_credential,
)


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
# 优先使用 Redis 跨进程信号量（多 worker 场景下总并发不超限）；Redis 不可用时
# 回退到进程内 threading.Semaphore（单 worker / 本地开发仍正确）。
_IMPORT_SEM_CAPACITY = 2
_IMPORT_SEM_NAME = "import_pipeline"
_IMPORT_GLOBAL_SEM = threading.Semaphore(_IMPORT_SEM_CAPACITY)  # 回退用
# 当前正在等待 semaphore 的任务数（排队深度）。原子 +1/-1 用 _QUEUE_LOCK。
_QUEUE_DEPTH: int = 0
_QUEUE_LOCK = threading.Lock()


def _redis_sem_init() -> bool:
    """尝试初始化 Redis 信号量令牌池（幂等）。返回 True=Redis 可用，False=回退进程内。"""
    try:
        from redis_bus import sem_init
        return sem_init(_IMPORT_SEM_NAME, _IMPORT_SEM_CAPACITY)
    except Exception:
        return False


def _redis_sem_acquire(timeout_sec: int = 1800) -> tuple[bool, str | None]:
    """
    尝试从 Redis 取令牌。
    返回 (used_redis, token)：used_redis=True 表示用了 Redis，token 为令牌字符串（release 时用）。
    Redis 不可用时回退到进程内 Semaphore.acquire()。
    """
    try:
        from redis_bus import sem_acquire
        token = sem_acquire(_IMPORT_SEM_NAME, timeout_sec=timeout_sec)
        if token is not None:
            return True, token
        # sem_acquire 返回 None：超时或 Redis 不可用
    except Exception:
        pass
    # 回退：进程内阻塞
    _IMPORT_GLOBAL_SEM.acquire()
    return False, None


def _redis_sem_release(used_redis: bool, token: str | None) -> None:
    """归还令牌（与 _redis_sem_acquire 对称）。

    [round-4-P2] 释放必须与 acquire 走的同一侧严格对称,否则会过度释放:
      - used_redis=True：只还 Redis 令牌。绝不能再 release 进程内 Semaphore——
        acquire 时根本没占进程内槽位,补释放会把计数器顶过容量(并发超限)。
        若此刻 Redis 不可达,令牌暂时无法归还(下次 sem_init 幂等补种时,池空才补;
        极端情况丢 1 个令牌,best-effort 限流可接受,绝不拿过度释放去换)。
      - used_redis=False：acquire 走了进程内 Semaphore,这里对称 release。
    """
    if used_redis:
        if token is not None:
            try:
                from redis_bus import sem_release
                sem_release(_IMPORT_SEM_NAME, token)
            except Exception:
                pass  # Redis 抖动:宁可丢令牌也不过度释放进程内槽位
        return
    _IMPORT_GLOBAL_SEM.release()

# ── 进程内 thread 跟踪表（best-effort）──────────────────────────────
# 多 worker 部署时只对当前 worker 可见，
# 跨 worker 协调依赖 DB advisory lock (cluster.try_acquire_job_lock)。
# daemon thread 在 worker 退出时自动清理 — 不需要手动 cleanup。
_RUNNING: dict[str, threading.Thread] = {}  # job_id → thread

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


def wait_for_import_job(
    user_id: int, job_id: str, *, timeout_s: float = 180.0, poll_s: float = 2.0,
) -> dict[str, Any]:
    """阻塞轮询 import_jobs 直到终态(done/done_with_errors/failed/cancelled)或超时,
    返回 get_job_status 的 job dict(含 status/overall_progress/overall_total/stages/error/warnings)。

    闭环用(用户反馈:导入/重建后 LLM 不知道好没好)。import_attached_script / rebuild_script_module
    在 LLM 自主工具循环里【确定性】等真实结果,把成功/失败回灌循环,而非返回「已入队」回执后停摆。
    job worker 是 in-process daemon thread(rebuild/full_pipeline),DB 轮询跨线程可靠且不依赖
    in-process 句柄。轮询跑在 GM 工作线程(chat_pipeline 的 asyncio.to_thread 桥接),time.sleep
    不阻塞事件循环、SSE 照常存活。超时返回当前(pending/running)快照,调用方据此优雅收尾。

    安全:get_job_status 已按 (job_id, user_id) 过滤,他人 job 查不到 → 返 {found:False}。
    """
    import time

    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {"status": "pending", "found": False}
    while True:
        try:
            st = get_job_status(user_id, job_id=job_id)
            if st.get("found") and isinstance(st.get("job"), dict):
                last = st["job"]
                if (last.get("status") or "").strip() in _TERMINAL_STATUSES:
                    return last
            elif st.get("found") is False:
                # job 不存在(被清理/越权)→ 立即收尾,别空转到超时
                return {"status": "not_found", "found": False}
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).debug("[import] wait_for_import_job poll error: %s", exc)
        if time.monotonic() >= deadline:
            last = dict(last) if isinstance(last, dict) else {"status": "pending"}
            last["timed_out"] = True
            return last
        time.sleep(poll_s)


def summarize_job_result(res: dict[str, Any], action: str) -> str:
    """把 wait_for_import_job 返回的 job 快照压成给 LLM/用户看的一行中文结果。
    闭环工具(import_attached_script / rebuild_script_module)用它回灌真实结果。"""
    res = res or {}
    status = (res.get("status") or "").strip()
    if res.get("timed_out"):
        prog, tot = res.get("overall_progress"), res.get("overall_total")
        prog_s = f"(进度 {prog}/{tot})" if prog is not None and tot else ""
        return (f"{action}仍在后台进行{prog_s}:任务会继续跑完,"
                f"稍后用 get_import_status / list_my_import_jobs 查最终结果。")
    if status in ("done", "done_with_errors"):
        parts = [f"{action}完成"]
        stages = res.get("stages") or []
        cnts = [
            f"{s.get('label') or s.get('id')}:{s.get('count')}"
            for s in stages
            if isinstance(s, dict) and s.get("count") is not None and s.get("status") != "skipped"
        ]
        if cnts:
            parts.append("(" + " / ".join(cnts) + ")")
        if status == "done_with_errors":
            w = res.get("warnings")
            parts.append(f"— 部分阶段有问题:{w}" if w else "— 部分阶段未完全成功")
        return " ".join(parts)
    if status == "failed":
        return f"{action}失败:{res.get('error') or '未知错误'}"
    if status == "cancelled":
        return f"{action}已被取消。"
    if status == "not_found":
        return f"{action}任务未找到(可能已被清理或越权)。"
    return f"{action}当前状态:{status or '未知'}。"


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
    # 优先使用 Redis 跨进程信号量（多 worker 下总并发不超限）；回退到进程内 Semaphore。
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

    # 尝试初始化 Redis 信号量令牌池（幂等；首次调用时填充，后续 no-op）
    _redis_sem_init()
    _used_redis_sem, _sem_token = _redis_sem_acquire(timeout_sec=1800)

    with _QUEUE_LOCK:
        _QUEUE_DEPTH -= 1

    # 多 worker 部署：advisory lock 防止同 job 被多 worker 同时跑
    try:
        from ..cluster import release_job_lock, try_acquire_job_lock
        if not try_acquire_job_lock(f"import_job:{job_id}"):
            # 已被别的 worker 占了，直接退出（那个 worker 会处理）
            _redis_sem_release(_used_redis_sem, _sem_token)
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
        _redis_sem_release(_used_redis_sem, _sem_token)
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
