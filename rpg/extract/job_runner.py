"""extract/job_runner.py — 把 llm-extract 接进 import_jobs job 体系。

设计:与 import_pipeline.schedule_full_import 同款,**复用同一张 import_jobs 表 +
同一套 SSE 流端点**(GET /api/scripts/import-jobs/{job_id}/stream)。前端无需新建组件。

差异:
- kind='llm_extract'(import_pipeline 是 'full_pipeline')
- 阶段:seed / arc_extract / resolve / embed(stages JSONB)
- options 传给 run_llm_extraction:algorithm/model/api_id/target_arcs/concurrency/...
"""
from __future__ import annotations

import secrets
import threading
from typing import Any

from psycopg.types.json import Jsonb

from platform_app.db import connect, init_db
from platform_app.import_pipeline import JobController

# 阶段定义(stages JSONB 初始化用)。前端按 id 显示 label
_STAGES = [
    {"id": "seed", "label": "种子词表 (Pass 0)", "status": "pending"},
    {"id": "arc_extract", "label": "弧段提取 (Pass 1)", "status": "pending"},
    {"id": "resolve", "label": "实体消歧聚合 (Pass 2)", "status": "pending"},
    {"id": "embed", "label": "嵌入入库 (Pass 3)", "status": "pending"},
]


def schedule_llm_extraction(user_id: int, script_id: int,
                            options: dict[str, Any] | None = None) -> dict[str, Any]:
    """异步调度 LLM 提取。立即返回 {ok, job_id};真活在后台线程跑,进度落 import_jobs 表。

    options(全可选):
      algorithm: 'arc'(默认) | 'per_chapter'
      model / api_id / target_arcs / concurrency / author_era / sample_chapters
      confirmed / max_book_usd / chapter_min / chapter_max
    """
    init_db()
    options = dict(options or {})

    with connect() as db:
        # 去重:同 (user, script, kind=llm_extract) 有 pending/running 则复用
        existing = db.execute(
            "select job_id from import_jobs "
            "where user_id=%s and script_id=%s and kind='llm_extract' "
            "and status in ('pending','running') order by id desc limit 1",
            (user_id, script_id),
        ).fetchone()
        if existing:
            return {"ok": True, "job_id": existing["job_id"], "reused": True}

        # per-user 并发上限(此 kind 内)
        active = db.execute(
            "select count(*) as n from import_jobs where user_id=%s "
            "and kind='llm_extract' and status in ('pending','running')",
            (user_id,),
        ).fetchone()
        if int(active["n"] if active else 0) >= 1:
            raise ValueError("您已有 1 个 LLM 提取任务在跑,请等其完成或取消")

        # 校验 owner(防止越权调度别人剧本)
        owned = db.execute("select 1 from scripts where id=%s and owner_id=%s",
                           (script_id, user_id)).fetchone()
        if not owned:
            raise ValueError("无权访问该剧本")

        job_id = f"llm_{script_id}_{secrets.token_hex(6)}"
        db.execute(
            "insert into import_jobs(job_id, user_id, script_id, kind, status, stage, "
            "overall_total, stages, budget_estimate) "
            "values (%s, %s, %s, 'llm_extract', 'pending', 'pending', %s, %s, %s)",
            (job_id, user_id, script_id, len(_STAGES), Jsonb(_STAGES),
             Jsonb({"options": options})),
        )

    th = threading.Thread(
        target=_run, args=(job_id, user_id, script_id, options), daemon=True,
    )
    th.start()
    return {"ok": True, "job_id": job_id, "reused": False}


def _run(job_id: str, user_id: int, script_id: int, options: dict[str, Any]) -> None:
    """后台 worker。把 run_llm_extraction 的 progress_cb 映射到 JobController.update。"""
    # 多 worker 部署 advisory lock
    try:
        from platform_app.cluster import release_job_lock, try_acquire_job_lock
        if not try_acquire_job_lock(f"llm_extract_job:{job_id}"):
            return  # 已被别的 worker 占
    except Exception:
        try_acquire_job_lock = release_job_lock = None  # type: ignore[assignment]

    ctl = JobController(job_id)
    ctl.update(status="running", stage="seed", overall_progress=0)
    init_db()
    with connect() as db:
        db.execute("update import_jobs set started_at=now() where job_id=%s", (job_id,))

    # progress_cb 映射 stage 名 → import_jobs 字段。run_llm_extraction 当前发的 stage:
    #   'arc_split' / 'seed' / 'arc_extract' / 'resolve' / 'embed' / 'done' / 'era_fallback'
    #   (per_chapter 模式发: 'seed' / 'per_chapter' / 'resolve' / 'embed' / 'done')
    _stage_index = {"seed": 0, "arc_extract": 1, "per_chapter": 1, "resolve": 2, "embed": 3}
    _stages_state = list(_STAGES)

    def _set_stage_status(stage_id: str, status: str) -> None:
        for s in _stages_state:
            if s["id"] == stage_id:
                s["status"] = status

    def cb(stage: str, info: dict) -> None:
        if ctl.is_cancelled():
            raise InterruptedError("cancelled")
        try:
            if stage == "arc_split":
                # 弧段切完,记入元数据(JobController.update 内部已 Jsonb 包装)
                ctl.update(
                    budget_estimate={"options": options,
                                     "arcs": info.get("arcs"),
                                     "chapters": info.get("chapters")},
                )
            elif stage in ("seed", "arc_extract", "per_chapter", "resolve", "embed"):
                idx = _stage_index.get(stage, 0)
                done = int(info.get("done", 0))
                total = int(info.get("total") or info.get("sample") or info.get("chapters") or 1)
                _set_stage_status(stage, "running")
                # 标完成
                if "succeeded" in info or done >= total:
                    _set_stage_status(stage, "done")
                ctl.update(
                    stage=stage,
                    stage_progress=done,
                    stage_total=total,
                    overall_progress=idx,
                    stages=_stages_state,
                )
            elif stage == "era_fallback":
                # 不阻塞,只更 meta
                pass
            elif stage == "done":
                _set_stage_status("embed", "done")
                ctl.update(stage="done", overall_progress=len(_STAGES), stages=_stages_state)
        except InterruptedError:
            raise
        except Exception:
            pass  # 进度上报失败不阻塞主流程

    try:
        from platform_app.knowledge.llm_extract import run_llm_extraction
        result = run_llm_extraction(
            user_id, script_id,
            algorithm=str(options.get("algorithm") or "arc"),
            author_era=str(options.get("author_era") or ""),
            author_power_system=options.get("author_power_system") or None,
            model=str(options.get("model") or "deepseek-v4-flash"),
            api_id=str(options.get("api_id") or "deepseek"),
            target_arcs=int(options.get("target_arcs") or 40),
            concurrency=int(options.get("concurrency") or 15),
            sample_chapters=options.get("sample_chapters"),
            chapter_min=options.get("chapter_min"),
            chapter_max=options.get("chapter_max"),
            confirmed=bool(options.get("confirmed", True)),  # 调度路径默认确认(否则一直卡在 needs_confirm)
            max_book_usd=float(options.get("max_book_usd") or 10.0),
            progress_cb=cb,
        )

        if result.get("ok"):
            # 累计 actual usage(import_jobs.usage_actual)+ KB 刚改回 unreviewed + 标终态
            act = result.get("actual_usage") or {}
            for s in _stages_state:
                if s["status"] != "done":
                    s["status"] = "done"
            with connect() as db:
                if act:
                    db.execute(
                        "update import_jobs set usage_actual=%s where job_id=%s",
                        (Jsonb(act), job_id),
                    )
                db.execute(
                    "update scripts set review_status='unreviewed', reviewed_at=null, "
                    "updated_at=now() where id=%s and owner_id=%s",
                    (script_id, user_id),
                )
            ctl.update(status="done", stage="done", overall_progress=len(_STAGES),
                       stages=_stages_state)
            with connect() as db:
                db.execute("update import_jobs set finished_at=now() where job_id=%s", (job_id,))
        else:
            # needs_confirm / quota_exceeded / error
            err_msg = str(result.get("message") or result.get("error") or "未知错误")
            ctl.update(status="failed", error=err_msg)
            with connect() as db:
                db.execute("update import_jobs set finished_at=now() where job_id=%s", (job_id,))
    except InterruptedError:
        ctl.update(status="cancelled")
        with connect() as db:
            db.execute("update import_jobs set finished_at=now() where job_id=%s", (job_id,))
    except Exception as exc:
        ctl.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:500]}")
        with connect() as db:
            db.execute("update import_jobs set finished_at=now() where job_id=%s", (job_id,))
    finally:
        try:
            if release_job_lock is not None:
                release_job_lock(f"llm_extract_job:{job_id}")
        except Exception:
            pass
