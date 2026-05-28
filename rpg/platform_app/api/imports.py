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
