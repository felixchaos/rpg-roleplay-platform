"""platform_app.api.imports — /api/scripts/{id}/knowledge/sync, import-* 路由, /api/me/import-jobs。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from .. import script_import
from ..db import connect
from ._deps import json_response, require_user

router = APIRouter()


@router.post("/api/scripts/{script_id}/knowledge/sync")
async def api_script_knowledge_sync(script_id: int, user=Depends(require_user)):
    """触发后台异步同步。立即返回 job_id；通过 /import-status 轮询进度。"""
    # 校验 owner
    with connect() as db:
        owned = db.execute("select 1 from scripts where id = %s and owner_id = %s", (script_id, user["id"])).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    job_id = script_import._schedule_knowledge_sync(user["id"], script_id)
    return json_response({"ok": True, "knowledge": {"job_id": job_id, "status": "pending", "async": True}})


@router.get("/api/scripts/{script_id}/llm-extract/usage")
async def api_script_llm_extract_usage(script_id: int, days: int = 30, user=Depends(require_user)):
    """查询本剧本累计 LLM 提取用量(从 token_usage 聚合)。

    days: 回溯时长(默认 30 天)。
    返回:
      {
        "ok": true,
        "script_id": ...,
        "total_calls": 42,                # 累计 LLM 调用次数
        "input_tokens": 350000,
        "output_tokens": 140000,
        "cost_usd": 0.092,
        "by_model": [
          {"api_id":"deepseek","model_real_name":"deepseek-v4-flash","calls":40,
           "input_tokens":...,"output_tokens":...,"cost_usd":...}
        ],
        "recent_calls": [
          {"created_at":...,"api_id":...,"model_real_name":...,
           "input_tokens":...,"output_tokens":...,"cost_usd":...,"algorithm":...}
        ]
      }
    """
    days = max(1, min(int(days or 30), 365))
    with connect() as db:
        owned = db.execute("select 1 from scripts where id=%s and owner_id=%s",
                           (script_id, user["id"])).fetchone()
        if not owned:
            return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
        # 汇总
        tot = db.execute(
            "select coalesce(sum(input_tokens),0) in_tok, "
            "coalesce(sum(output_tokens),0) out_tok, "
            "coalesce(sum(cost_usd),0) cost, count(*) calls "
            "from token_usage where user_id=%s "
            "and (metadata->>'script_id')::bigint = %s "
            "and created_at > now() - interval '1 day' * %s",
            (user["id"], script_id, days),
        ).fetchone()
        # 按模型分组
        by_model_rows = db.execute(
            "select api_id, model_real_name, "
            "coalesce(sum(input_tokens),0) in_tok, coalesce(sum(output_tokens),0) out_tok, "
            "coalesce(sum(cost_usd),0) cost, count(*) calls "
            "from token_usage where user_id=%s and (metadata->>'script_id')::bigint = %s "
            "and created_at > now() - interval '1 day' * %s "
            "group by api_id, model_real_name order by cost desc",
            (user["id"], script_id, days),
        ).fetchall()
        # 最近 10 次
        recent = db.execute(
            "select created_at, api_id, model_real_name, input_tokens, output_tokens, "
            "cost_usd, metadata->>'algorithm' as algorithm "
            "from token_usage where user_id=%s and (metadata->>'script_id')::bigint = %s "
            "and created_at > now() - interval '1 day' * %s "
            "order by created_at desc limit 10",
            (user["id"], script_id, days),
        ).fetchall()
    return json_response({
        "ok": True,
        "script_id": script_id,
        "days": int(days),
        "total_calls": int(tot["calls"]) if tot else 0,
        "input_tokens": int(tot["in_tok"]) if tot else 0,
        "output_tokens": int(tot["out_tok"]) if tot else 0,
        "cost_usd": float(tot["cost"]) if tot else 0.0,
        "by_model": [
            {"api_id": r["api_id"], "model_real_name": r["model_real_name"],
             "calls": int(r["calls"]), "input_tokens": int(r["in_tok"]),
             "output_tokens": int(r["out_tok"]), "cost_usd": float(r["cost"])}
            for r in by_model_rows
        ],
        "recent_calls": [
            {"created_at": str(r["created_at"]), "api_id": r["api_id"],
             "model_real_name": r["model_real_name"],
             "input_tokens": int(r["input_tokens"]), "output_tokens": int(r["output_tokens"]),
             "cost_usd": float(r["cost_usd"]), "algorithm": r["algorithm"]}
            for r in recent
        ],
    })


@router.post("/api/scripts/{script_id}/llm-extract/estimate")
async def api_script_llm_extract_estimate(request: Request, script_id: int, user=Depends(require_user)):
    """跑前预算(不触发实际提取)。前端在用户点「跑提取」前预览成本/时间用。

    Body 同 /llm-extract(全可选,只影响估算):
      {algorithm, model, target_arcs, sample_chapters, batch_discount}

    返回:
      {
        "ok": true,
        "algorithm": "arc",
        "model": "deepseek-v4-flash",
        "model_tier": "flash",
        "chapters": 1166,           # 可提取总章
        "arcs": 38,                 # arc 模式下实际弧数(target_arcs 受 5/80 钳后)
        "est_input_tokens": 334400,
        "est_output_tokens": 134400,
        "est_usd": 0.087,
        "note": "约 $0.09(38 弧 × deepseek-v4-flash)。"
      }
    """
    # 校验 owner
    with connect() as db:
        owned = db.execute("select 1 from scripts where id=%s and owner_id=%s",
                           (script_id, user["id"])).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    from extract.budget import estimate
    with connect() as db:
        est = estimate(
            db, script_id,
            model=str(body.get("model") or "deepseek-v4-flash"),
            algorithm=str(body.get("algorithm") or "arc"),
            target_arcs=int(body.get("target_arcs") or 40),
            sample_chapters=body.get("sample_chapters"),
            batch_discount=bool(body.get("batch_discount")),
        )
    return json_response(est, status_code=200 if est.get("ok") else 400)


@router.post("/api/scripts/{script_id}/llm-extract")
async def api_script_llm_extract(request: Request, script_id: int, user=Depends(require_user)):
    """异步调度 LLM 提取。**立即返回 job_id**,真活在后台线程跑。

    复用 import_jobs 表 + 同一个 SSE 流端点(`/api/scripts/import-jobs/{job_id}/stream`)
    与 import_pipeline (kind='full_pipeline')共存,kind='llm_extract' 区分。

    Body(全可选):
      {
        "algorithm": "arc"|"per_chapter",      # 默认 arc
        "model": "deepseek-v4-flash",
        "api_id": "deepseek",
        "target_arcs": 40,
        "concurrency": 15,
        "author_era": "",
        "author_power_system": ["..."],
        "sample_chapters": null,
        "confirmed": true,                     # 调度路径默认 true(同步路径默认 false)
        "max_book_usd": 10.0,
        "sync": false                          # 显式 true 走老的同步阻塞(适合脚本/admin)
      }

    返回(异步,默认):
      {"ok": true, "job_id": "llm_36267_xxx", "reused": false, "async": true}
      前端拿 job_id 用 streamImport(job_id, handlers) 接 SSE 看进度。
    返回(sync=true):
      与之前同步版本一致:{"ok": true, "algorithm": "arc_rag", "arcs": 40, ...} 阻塞 ~2 分钟
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    sync = bool(body.get("sync"))
    try:
        if sync:
            # 老同步路径(脚本/调试用)
            from platform_app.knowledge.llm_extract import run_llm_extraction
            result = run_llm_extraction(
                user["id"], script_id,
                algorithm=str(body.get("algorithm") or "arc"),
                author_era=str(body.get("author_era") or ""),
                author_power_system=body.get("author_power_system") or None,
                model=str(body.get("model") or "deepseek-v4-flash"),
                api_id=str(body.get("api_id") or "deepseek"),
                target_arcs=int(body.get("target_arcs") or 40),
                concurrency=int(body.get("concurrency") or 15),
                sample_chapters=body.get("sample_chapters"),
                chapter_min=body.get("chapter_min"),
                chapter_max=body.get("chapter_max"),
                confirmed=bool(body.get("confirmed")),
                max_book_usd=float(body.get("max_book_usd") or 10.0),
            )
            if result.get("ok"):
                with connect() as db:
                    db.execute(
                        "update scripts set review_status='unreviewed', reviewed_at=null, "
                        "updated_at=now() where id=%s and owner_id=%s",
                        (script_id, user["id"]),
                    )
                result["review_status"] = "unreviewed"
            status = 200 if (result.get("ok") or result.get("needs_confirm")) else 400
            if result.get("error") and "无权" in str(result.get("error")):
                status = 403
            return json_response(result, status_code=status)

        # 默认:异步调度,立刻返回 job_id
        from extract.job_runner import schedule_llm_extraction
        result = schedule_llm_extraction(user["id"], script_id, options=body)
        return json_response({**result, "async": True}, status_code=200)
    except ValueError as exc:
        msg = str(exc)
        status = 403 if "无权" in msg else 409 if "在跑" in msg else 400
        return json_response({"ok": False, "error": msg}, status_code=status)


@router.get("/api/scripts/{script_id}/import-status")
async def api_script_import_status(script_id: int, user=Depends(require_user)):
    """查询某剧本最近一次后台同步任务的状态。"""
    return json_response(script_import.get_sync_status(user["id"], script_id))


# ── 拆书流水线（多阶段 + 预算 + 取消 + 持久化进度）─────────────
@router.post("/api/scripts/{script_id}/import-budget")
async def api_script_import_budget(request: Request, script_id: int, user=Depends(require_user)):
    """开始拆书前给出预算（token/cost/时长）。

    Body: {"enable_cards": true, "enable_worldbook": true,
           "model_api_id": "...", "model_real_name": "..."}（全可选）
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    from .. import import_pipeline
    with connect() as db:
        script = db.execute(
            "select chapter_count, word_count from scripts where id = %s and owner_id = %s",
            (script_id, user["id"]),
        ).fetchone()
    if not script:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    return json_response(import_pipeline.estimate_budget(
        chapter_count=int(script["chapter_count"]),
        total_words=int(script["word_count"]),
        enable_cards=bool(body.get("enable_cards", True)),
        enable_worldbook=bool(body.get("enable_worldbook", True)),
        cards_top_n=int(body.get("cards_top_n", 30)),
        model_api_id=body.get("model_api_id") or "vertex_ai",
        model_real_name=body.get("model_real_name") or "gemini-3.5-flash",
    ))


@router.post("/api/scripts/{script_id}/import-pipeline")
async def api_script_import_pipeline(request: Request, script_id: int, user=Depends(require_user)):
    """启动完整拆书流水线，立即返 job_id。前端轮询 /import-job-status 看进度。"""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    from .. import import_pipeline
    try:
        return json_response(import_pipeline.schedule_full_import(
            user["id"], script_id,
            enable_cards=bool(body.get("enable_cards", True)),
            enable_worldbook=bool(body.get("enable_worldbook", True)),
            budget=body.get("budget") or {},
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/scripts/import-jobs/{job_id}")
async def api_import_job_status(job_id: str, user=Depends(require_user)):
    """轮询任务状态：进度、当前阶段、token/cost 累计、错误。"""
    from .. import import_pipeline
    return json_response(import_pipeline.get_job_status(user["id"], job_id=job_id))


@router.get("/api/scripts/import-jobs/{job_id}/stream")
async def api_import_job_stream(request: Request, job_id: str, user=Depends(require_user)):
    """SSE 实时推送 job 进度，前端不再轮询。

    每秒检测一次 DB，状态/阶段/进度变化时推 event；任务结束（done/failed/cancelled）后退出。
    保留 request：SSE endpoint 需要 request.is_disconnected() 检测客户端断开（虽然此处通过任务状态退出）。
    """
    import asyncio as _asyncio
    import json as _json

    from .. import import_pipeline

    async def gen():
        last_snapshot = None
        idle_loops = 0
        while True:
            payload = import_pipeline.get_job_status(user["id"], job_id=job_id)
            job = (payload.get("job") or {}) if payload.get("found") else {}
            if not job:
                yield f"event: error\ndata: {_json.dumps({'error': 'job not found'})}\n\n"
                return
            # 状态指纹：检测变化
            snap = (
                job.get("status"), job.get("stage"),
                job.get("stage_progress"), job.get("overall_progress"),
                _json.dumps(job.get("usage_actual") or {}, sort_keys=True),
            )
            if snap != last_snapshot:
                yield f"event: update\ndata: {_json.dumps(job, default=str, ensure_ascii=False)}\n\n"
                last_snapshot = snap
                idle_loops = 0
            else:
                idle_loops += 1
                if idle_loops % 15 == 0:
                    # 每 15s 推一个心跳，让 nginx/cloudflare 不掐连接
                    yield ": heartbeat\n\n"
            # 任务结束就关
            if job.get("status") in ("done", "failed", "cancelled"):
                yield f"event: done\ndata: {_json.dumps({'status': job.get('status')})}\n\n"
                return
            await _asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/api/scripts/import-jobs/{job_id}/cancel")
async def api_import_job_cancel(job_id: str, user=Depends(require_user)):
    """请求取消。worker 在下一个检查点退出。"""
    from .. import import_pipeline
    try:
        return json_response(import_pipeline.cancel_job(user["id"], job_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=404)


@router.get("/api/me/import-jobs")
async def api_my_import_jobs(limit: int = 20, user=Depends(require_user)):
    """列出本人最近 20 个导入任务（dashboard 用）。"""
    from .. import import_pipeline
    return json_response(import_pipeline.list_jobs(user["id"], limit=limit))
