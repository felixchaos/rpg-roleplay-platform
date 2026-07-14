"""platform_app.api.me.tasks —— 全局后台任务浮窗数据源端点。

聚合本人所有"进行中/刚结束"的后台任务(import_jobs + ai_images)。含 kind 标签映射与状态归一。
纯机械搬家,行为零变化。
"""
from __future__ import annotations

from fastapi import Depends

from ...db import connect
from .._deps import json_response, require_user
from ._shared import router


# ── 全局后台任务面板:聚合本人所有"进行中/刚结束"的后台任务 ───────────────
# 给右下角全局浮窗用。只读聚合,UNION import_jobs(导入+各模块重建)+ ai_images(生图),
# 不做 schema 迁移。如实状态:import 类有真实 overall_progress 进度,生图类只有阶段+耗时。
_TASK_IMPORT_KIND_LABELS = {
    "full_pipeline": "导入流水线",
    "llm_extract": "LLM 二次提取",
    "knowledge_sync": "知识库同步",
    "account_import": "账号迁移导入",
    "rebuild_chunks": "切块重建",
    "rebuild_facts": "章节事实重建",
    "rebuild_canon": "规范实体重建",
    "rebuild_cards": "角色卡重建",
    "rebuild_worldbook": "世界书重建",
    "rebuild_anchors": "时间线重建",
    "rebuild_embeddings": "向量重嵌入",
    "cards_audit": "AI 复核角色卡",
}
_TASK_IMAGE_KIND_LABELS = {
    "chat": "聊天生图", "cover": "封面生图", "avatar": "头像生图",
    "game": "场景生图", "persona": "人设图生成",
}


def _task_norm_status(raw: str) -> str:
    r = (raw or "").strip().lower()
    if r in ("pending", "queued"):
        return "queued"
    if r in ("running", "staging", "processing", "generating"):
        return "running"
    if r == "done":
        return "done"
    if r == "done_with_errors":
        return "done_with_errors"
    if r in ("failed", "error"):
        return "failed"
    if r in ("cancelled", "canceled"):
        return "cancelled"
    return r or "running"


@router.get("/api/me/tasks/active")
async def api_active_tasks(user=Depends(require_user)):
    """全局后台任务浮窗数据源。返回本人:
      · 进行中任务(import_jobs 排队/运行 + ai_images 排队/生成中);
      · 最近 90s 内刚结束的任务(done/failed/cancelled),供前端弹"完成/失败"提示。
    僵尸防护:心跳/更新超时的活跃行不计入(避免久挂的死任务长期占屏)。
    """
    import logging as _lg
    _log = _lg.getLogger(__name__)
    uid = int(user["id"])
    tasks: list[dict] = []

    # ── import_jobs:导入 + 全部模块重建 ──(独立连接 + try,单表故障不拖垮整端点)
    try:
        with connect() as db:
            rows = db.execute(
                """
                select ij.job_id, ij.kind, ij.status, ij.cancel_requested,
                       ij.overall_progress, ij.overall_total, ij.stage, ij.error,
                       extract(epoch from (now() - coalesce(ij.started_at, ij.created_at)))::int as elapsed_sec,
                       s.title as script_title
                  from import_jobs ij
                  left join scripts s on s.id = ij.script_id
                 where ij.user_id = %s
                   and (
                     (ij.status in ('pending','queued','running','staging','processing')
                      and coalesce(ij.heartbeat_at, ij.updated_at, ij.created_at) > now() - interval '30 minutes')
                     or (ij.status in ('done','done_with_errors','failed','cancelled')
                         and ij.finished_at is not null
                         and ij.finished_at > now() - interval '90 seconds')
                   )
                 order by ij.id desc
                 limit 30
                """,
                (uid,),
            ).fetchall()
        for r in rows:
            kind = r["kind"] or ""
            label = _TASK_IMPORT_KIND_LABELS.get(kind, kind or "后台任务")
            stitle = (r["script_title"] or "").strip()
            if stitle:
                title = f"{stitle} · {label}"
            elif kind == "account_import":
                title = "账号迁移导入"
            else:
                title = label
            nst = _task_norm_status(r["status"])
            # 去掉与状态重复的占位 stage(默认值 'pending' 等),只留真实阶段描述
            phase = (r["stage"] or "").strip()
            if phase.lower() in ("pending", "queued", "init", "running", "staging",
                                  "processing", "done", "failed", ""):
                phase = None
            # 进度条只在"运行中"显示(排队态列默认值无意义,不画 0/5 误导)
            prog, total = r["overall_progress"], r["overall_total"]
            show_prog = nst == "running" and prog is not None and total
            tasks.append({
                "id": f"import:{r['job_id']}",
                "source": "import",
                "kind": kind,
                "title": title,
                "status": nst,
                "phase": phase,
                "progress": int(prog) if show_prog else None,
                "progress_total": int(total) if show_prog else None,
                "elapsed_sec": max(0, int(r["elapsed_sec"] or 0)),
                "error": (r["error"] or None),
                "canceling": bool(r["cancel_requested"]),
                "cancelable": True,
            })
    except Exception as exc:
        _log.warning("[tasks] import_jobs 聚合失败(降级): %s", exc)

    # ── ai_images:生图(无原生进度,只给阶段+耗时)──
    # ai_images 无 finished_at 列,用 created_at 近似"最近结束"。窗口放宽到 15min 覆盖慢生图,
    # 否则 >90s 的生图完成后浮窗会漏掉完成/失败提示(完成 toast 只在 active→非 active 跃迁时触发,
    # 不在 prevActive 里的旧 done 不会误报,故放宽安全)。
    try:
        with connect() as db:
            rows2 = db.execute(
                """
                select id, kind, prompt, status, error,
                       extract(epoch from (now() - created_at))::int as elapsed_sec
                  from ai_images
                 where user_id = %s
                   and created_at > now() - interval '15 minutes'
                   and status in ('pending','generating','done','failed')
                 order by id desc
                 limit 20
                """,
                (uid,),
            ).fetchall()
        for r in rows2:
            kind = r["kind"] or ""
            label = _TASK_IMAGE_KIND_LABELS.get(kind, "生图")
            prompt = (r["prompt"] or "").strip().replace("\n", " ")
            title = label + ("：" + prompt[:24] if prompt else "")
            tasks.append({
                "id": f"image:{r['id']}",
                "source": "image",
                "kind": kind,
                "title": title,
                "status": _task_norm_status(r["status"]),
                "phase": None,
                "progress": None,
                "progress_total": None,
                "elapsed_sec": max(0, int(r["elapsed_sec"] or 0)),
                "error": (r["error"] or None),
                "canceling": False,
                "cancelable": True,
            })
    except Exception as exc:
        _log.warning("[tasks] ai_images 聚合失败(降级): %s", exc)
    # 活跃在前(运行>排队),刚结束在后;同组按耗时新->旧
    _ord = {"running": 0, "queued": 1, "failed": 2, "done_with_errors": 2, "done": 3, "cancelled": 3}
    tasks.sort(key=lambda x: (_ord.get(x["status"], 9), x["elapsed_sec"]))
    active_count = sum(1 for t in tasks if t["status"] in ("queued", "running"))
    return json_response({"ok": True, "tasks": tasks, "active_count": active_count})
