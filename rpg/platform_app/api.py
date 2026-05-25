from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse as BaseJSONResponse

from tool_registry import tool_payload

from psycopg.types.json import Jsonb

from . import auth, branches, knowledge, library, script_import, settings, workspace
from .db import connect, init_db, status as db_status
from .pages import PLATFORM_HTML
from .security import public_user


SESSION_COOKIE = "rpg_session"
API_VERSION = "1"
router = APIRouter()

COMMANDS = [
    ("GET", "/", "文字 RPG 主游戏界面"),
    ("GET", "/app", "多用户平台/创作平台界面"),
    ("GET", "/api/state", "读取当前可玩存档状态"),
    ("POST", "/api/new", "创建新游戏并保留旧档备份"),
    ("POST", "/api/opening", "生成开场"),
    ("POST", "/api/chat", "发送玩家行动/对话，支持流式 GM 输出与结构化状态写回"),
    ("POST", "/api/stop", "打断当前生成"),
    ("POST", "/api/save", "手动保存当前游戏"),
    ("POST", "/api/memory/mode", "设置记忆模式"),
    ("POST", "/api/memory/add", "添加长期记忆"),
    ("POST", "/api/memory/remove", "删除长期记忆"),
    ("POST", "/api/permissions", "设置 LLM 状态写入权限"),
    ("GET", "/api/models", "读取 API/模型树与前端显示模型"),
    ("POST", "/api/models/select", "选择当前前端模型"),
    ("POST", "/api/models/api", "新增或更新 API 供应商"),
    ("POST", "/api/models/model", "新增或更新 API 下属模型"),
    ("GET", "/api/tools", "插件/MCP/Skill 能力状态"),
    ("POST", "/api/mcp/server", "新增或更新 MCP 服务器配置"),
    ("POST", "/api/mcp/server/enabled", "启用或禁用 MCP 服务器"),
    ("POST", "/api/mcp/server/delete", "删除 MCP 服务器配置"),
    ("POST", "/api/mcp/server/validate", "校验 MCP stdio 命令可用性"),
    ("POST", "/api/skills/import", "本地部署导入 Skill 包"),
    ("POST", "/api/worldline/variable", "新增或锁定用户世界线变量"),
    ("POST", "/api/worldline/variable/remove", "移除用户世界线变量"),
    ("POST", "/api/auth/register", "注册账号"),
    ("POST", "/api/auth/login", "登录并写入会话 cookie"),
    ("POST", "/api/auth/logout", "退出登录"),
    ("GET", "/api/platform", "平台总览：主页、剧本、存档、库、工具"),
    ("GET", "/api/scripts", "剧本列表"),
    ("POST", "/api/scripts/import", "导入 TXT/MD 剧本并自动识别章节"),
    ("GET", "/api/scripts/{script_id}/chapters", "读取剧本章节目录与预览"),
    ("POST", "/api/scripts/{script_id}/knowledge/sync", "重建剧本 ChapterFact、世界书、人设卡和检索块"),
    ("GET", "/api/scripts/{script_id}/chapter-facts", "读取剧本 ChapterFact 时间线"),
    ("GET", "/api/scripts/{script_id}/character-cards", "读取剧本人设卡"),
    ("GET", "/api/scripts/{script_id}/worldbook", "读取剧本世界书条目"),
    ("GET", "/api/saves", "游戏存档目录"),
    ("POST", "/api/saves", "基于剧本创建新存档"),
    ("GET", "/api/branches/{save_id}", "读取某个存档的分支树"),
    ("POST", "/api/branches/continue", "从任意对话节点派生/激活当前游戏 runtime"),
    ("POST", "/api/branches/activate", "直接激活某个分支节点为当前游戏 runtime"),
    ("POST", "/api/branches/delete", "删除某条连线下的整条分支"),
    ("GET", "/api/saves/{save_id}/context-runs", "读取某个存档的上下文子代理运行记录"),
    ("GET", "/api/settings", "读取设置"),
    ("POST", "/api/settings", "写入设置"),
    ("GET", "/api/library", "文件库列表"),
    ("POST", "/api/library/upload", "文件库上传"),
    ("POST", "/api/library/mkdir", "文件库创建文件夹"),
    ("POST", "/api/library/delete", "文件库删除"),
    ("GET", "/api/library/download", "文件库下载"),
    ("GET", "/api/platform/commands", "读取全部功能指令清单"),
]


def json_response(content, status_code: int = 200, **kwargs):
    if isinstance(content, dict) and "meta" not in content:
        content = {
            **content,
            "meta": {
                "api_version": API_VERSION,
                "stable": True,
            },
        }
    return BaseJSONResponse(jsonable_encoder(content), status_code=status_code, **kwargs)


def _set_session_cookie(response: BaseJSONResponse, request: Request, token: str) -> None:
    secure_env = os.environ.get("RPG_COOKIE_SECURE")
    secure = request.url.scheme == "https" if secure_env is None else secure_env == "1"
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=secure,
        samesite=os.environ.get("RPG_COOKIE_SAMESITE", "lax"),
        max_age=auth.SESSION_DAYS * 24 * 60 * 60,
        path="/",
    )


@router.get("/app", response_class=HTMLResponse)
@router.get("/app/{path:path}", response_class=HTMLResponse)
async def app_page() -> HTMLResponse:
    return HTMLResponse(PLATFORM_HTML)


@router.post("/api/auth/register")
async def api_register(request: Request):
    body = await request.json()
    try:
        user = auth.register(body.get("username", ""), body.get("password", ""), body.get("display_name", ""))
        workspace.ensure_default(user["id"])
        user, token = auth.login(body.get("username", ""), body.get("password", ""))
        response = json_response({"ok": True, "user": public_user(user), "platform": platform_for(user)})
        _set_session_cookie(response, request, token)
        return response
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


def _client_ip(request: Request) -> str:
    """获取客户端 IP。

    安全：默认只用 TCP 层的 request.client.host，不信任 X-Forwarded-For
    （否则攻击者直接换头就能绕过按 IP 的速率限制）。
    仅当 TCP 对端 IP 在 RPG_TRUSTED_PROXIES 白名单里（如 nginx/cloudflare 后端），
    才信 XFF 的第一段。
    """
    tcp_ip = request.client.host if request.client else ""
    trusted = {
        ip.strip() for ip in os.environ.get("RPG_TRUSTED_PROXIES", "").split(",") if ip.strip()
    }
    if tcp_ip and tcp_ip in trusted:
        xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if xff:
            return xff
    return tcp_ip


@router.post("/api/auth/login")
async def api_login(request: Request):
    body = await request.json()
    ip = _client_ip(request)
    try:
        user, token = auth.login(body.get("username", ""), body.get("password", ""), ip=ip)
        workspace.ensure_default(user["id"])
        response = json_response({"ok": True, "user": public_user(user), "platform": platform_for(user)})
        _set_session_cookie(response, request, token)
        return response
    except auth.RateLimited as rl:
        return json_response(
            {"ok": False, "error": f"登录失败次数过多，请 {rl.retry_after_sec} 秒后再试"},
            status_code=429,
            headers={"Retry-After": str(rl.retry_after_sec)},
        )
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/auth/logout")
async def api_logout(request: Request):
    auth.logout(request.cookies.get(SESSION_COOKIE))
    response = json_response({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/api/auth/me")
async def api_me(request: Request):
    user = current_user(request)
    # 安全：未登录不返回 DB 细节，仅返回 driver/ok 健康标识
    is_admin = bool(user and user.get("role") == "admin")
    return json_response({
        "ok": True,
        "user": public_user(user) if user else None,
        "database": db_status(reveal_details=is_admin),
    })


@router.get("/api/platform")
async def api_platform(request: Request):
    user = current_user(request)
    # 服务器/生产模式下未登录拒绝返回任何平台信息
    if not user and _auth_required():
        return json_response({"ok": False, "error": "需要登录"}, status_code=401)
    return json_response(platform_for(user))


@router.post("/api/profile")
async def api_profile(request: Request):
    user = require_user(request)
    body = await request.json()
    updated = auth.update_profile(user["id"], body.get("display_name", user["display_name"]), body.get("bio", ""))
    return json_response({"ok": True, "user": public_user(updated)})


@router.get("/api/scripts")
async def api_scripts(request: Request, limit: int | None = None, cursor: str | None = None):
    user = require_user(request)
    return json_response({"ok": True, **workspace.scripts_page(user["id"], limit, cursor)})


@router.post("/api/scripts/import")
async def api_import_script(request: Request):
    user = require_user(request)
    body = await request.json()
    try:
        # task 17: 之前漏传 upload_id，分片上传走完后端拿不到 raw → "请提供 file 或 upload_id"。
        # 现在透传 body.upload_id，单次 POST + 分片两条路径都能工作。
        return json_response({
            "ok": True,
            **script_import.import_script(
                user["id"],
                body.get("file") or {},
                split_rule=body.get("split_rule", "auto"),
                custom_pattern=body.get("custom_pattern", ""),
                title=body.get("title", ""),
                upload_id=str(body.get("upload_id") or ""),
            ),
        })
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/scripts/{script_id}/chapters")
async def api_script_chapters(
    request: Request, script_id: int,
    limit: int | None = None, cursor: str | None = None, q: str | None = None,
):
    """章节列表，支持 ?q=... 标题/内容全文 ILIKE 搜索。"""
    user = require_user(request)
    try:
        if q:
            # 全文搜索分支
            with connect() as db:
                owned = db.execute("select 1 from scripts where id=%s and owner_id=%s", (script_id, user["id"])).fetchone()
                if not owned:
                    return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
                rows = db.execute(
                    """
                    select id, chapter_index, title, volume_title, word_count,
                           substring(content for 200) as preview
                    from script_chapters
                    where script_id = %s and (title ilike %s or content ilike %s)
                    order by chapter_index limit %s
                    """,
                    (script_id, f"%{q}%", f"%{q}%", int(limit or 50)),
                ).fetchall()
            from .db import expose as _expose
            return json_response({"ok": True, "items": [_expose(r) for r in rows], "query": q})
        return json_response({"ok": True, **script_import.list_chapters(user["id"], script_id, limit, cursor)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/usage/timeline")
async def api_my_usage_timeline(request: Request):
    """时间序列用量（dashboard 图表用）。group_by=day|model"""
    user = require_user(request)
    from . import usage as usage_mod
    try:
        return json_response(usage_mod.timeline_usage(
            user["id"],
            days=int(request.query_params.get("days") or 30),
            group_by=request.query_params.get("group_by") or "day",
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/knowledge/sync")
async def api_script_knowledge_sync(request: Request, script_id: int):
    """触发后台异步同步。立即返回 job_id；通过 /import-status 轮询进度。"""
    user = require_user(request)
    # 校验 owner
    with connect() as db:
        owned = db.execute("select 1 from scripts where id = %s and owner_id = %s", (script_id, user["id"])).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    job_id = script_import._schedule_knowledge_sync(user["id"], script_id)
    return json_response({"ok": True, "knowledge": {"job_id": job_id, "status": "pending", "async": True}})


@router.get("/api/scripts/{script_id}/import-status")
async def api_script_import_status(request: Request, script_id: int):
    """查询某剧本最近一次后台同步任务的状态。"""
    user = require_user(request)
    return json_response(script_import.get_sync_status(user["id"], script_id))


# ── 拆书流水线（多阶段 + 预算 + 取消 + 持久化进度）─────────────
@router.post("/api/scripts/{script_id}/import-budget")
async def api_script_import_budget(request: Request, script_id: int):
    """开始拆书前给出预算（token/cost/时长）。

    Body: {"enable_cards": true, "enable_worldbook": true,
           "model_api_id": "...", "model_real_name": "..."}（全可选）
    """
    user = require_user(request)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    from . import import_pipeline
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
async def api_script_import_pipeline(request: Request, script_id: int):
    """启动完整拆书流水线，立即返 job_id。前端轮询 /import-job-status 看进度。"""
    user = require_user(request)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    from . import import_pipeline
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
async def api_import_job_status(request: Request, job_id: str):
    """轮询任务状态：进度、当前阶段、token/cost 累计、错误。"""
    user = require_user(request)
    from . import import_pipeline
    return json_response(import_pipeline.get_job_status(user["id"], job_id=job_id))


@router.get("/api/scripts/import-jobs/{job_id}/stream")
async def api_import_job_stream(request: Request, job_id: str):
    """SSE 实时推送 job 进度，前端不再轮询。

    每秒检测一次 DB，状态/阶段/进度变化时推 event；任务结束（done/failed/cancelled）后退出。
    """
    from fastapi.responses import StreamingResponse
    user = require_user(request)
    from . import import_pipeline
    import json as _json
    import asyncio as _asyncio

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
async def api_import_job_cancel(request: Request, job_id: str):
    """请求取消。worker 在下一个检查点退出。"""
    user = require_user(request)
    from . import import_pipeline
    try:
        return json_response(import_pipeline.cancel_job(user["id"], job_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=404)


@router.get("/api/me/import-jobs")
async def api_my_import_jobs(request: Request):
    """列出本人最近 20 个导入任务（dashboard 用）。"""
    user = require_user(request)
    from . import import_pipeline
    limit = int(request.query_params.get("limit") or 20)
    return json_response(import_pipeline.list_jobs(user["id"], limit=limit))


@router.post("/api/scripts/preview")
async def api_script_preview(request: Request):
    """Dry-run：不入库返切分预览，前端调参用。"""
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(script_import.preview_split(
            file_item=body.get("file"),
            split_rule=body.get("split_rule", "auto"),
            custom_pattern=body.get("custom_pattern", ""),
            upload_id=body.get("upload_id", ""),
            user_id=user["id"],
            sample_limit=int(body.get("sample_limit", 20)),
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/delete")
async def api_script_delete(request: Request, script_id: int):
    """删除剧本。force=True 时连带删除其下所有存档。"""
    user = require_user(request)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    try:
        return json_response(script_import.delete_script(user["id"], script_id, force=bool(body.get("force"))))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


@router.post("/api/scripts/{script_id}/chapters/{chapter_index}")
async def api_chapter_update(request: Request, script_id: int, chapter_index: int):
    """编辑单章 title/content/volume_title。"""
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(script_import.update_chapter(
            user["id"], script_id, chapter_index,
            title=body.get("title"), content=body.get("content"),
            volume_title=body.get("volume_title"),
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/chapters/merge")
async def api_chapter_merge(request: Request, script_id: int):
    """合并 first_index 和 first_index+1 两章。"""
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(script_import.merge_chapters(
            user["id"], script_id, int(body.get("first_index") or 0),
            separator=body.get("separator") or "\n\n",
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/chapters/{chapter_index}/split")
async def api_chapter_split(request: Request, script_id: int, chapter_index: int):
    """按字符位置 split_at 把一章拆成两章。"""
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(script_import.split_chapter(
            user["id"], script_id, chapter_index,
            split_at=int(body.get("split_at") or 0),
            new_title=body.get("new_title") or "",
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/resplit")
async def api_script_resplit(request: Request, script_id: int):
    """用新规则重切已导入剧本。保留 script + 存档，只换章节。"""
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(script_import.resplit_script(
            user["id"], script_id,
            split_rule=body.get("split_rule", "auto"),
            custom_pattern=body.get("custom_pattern", ""),
        ))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


# ── 大文件分片上传（替代单次 base64 POST，避免内存爆）─────────────
@router.post("/api/uploads/init")
async def api_upload_init(request: Request):
    """开始分片上传，返回 upload_id。"""
    user = require_user(request)
    body = await request.json()
    try:
        return json_response({"ok": True, **script_import.init_upload(
            user["id"],
            body.get("filename", ""),
            int(body.get("total_bytes") or 0),
            int(body.get("total_chunks") or 0),
        )})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/uploads/{upload_id}/chunk")
async def api_upload_chunk(request: Request, upload_id: str):
    """上传一个 chunk。body: {"chunk_index": N, "base64": "..."}"""
    user = require_user(request)
    body = await request.json()
    try:
        import base64 as _b64
        blob = _b64.b64decode(str(body.get("base64") or ""), validate=True)
        return json_response({"ok": True, **script_import.put_chunk(
            user["id"], upload_id, int(body.get("chunk_index") or 0), blob,
        )})
    except (ValueError, __import__("binascii").Error) as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/uploads/{upload_id}/finish")
async def api_upload_finish(request: Request, upload_id: str):
    """全部分片到齐后调，返回 file_item（可直接传给 /api/scripts/import 的 file 字段）。"""
    user = require_user(request)
    try:
        return json_response(script_import.finish_upload(user["id"], upload_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/uploads/{upload_id}/cancel")
async def api_upload_cancel(request: Request, upload_id: str):
    """放弃上传，清掉服务器上的临时块。"""
    user = require_user(request)
    try:
        return json_response(script_import.cancel_upload(user["id"], upload_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/scripts/{script_id}/chapter-facts")
async def api_script_chapter_facts(request: Request, script_id: int, limit: int | None = None, cursor: str | None = None):
    user = require_user(request)
    try:
        return json_response({"ok": True, **knowledge.list_chapter_facts(user["id"], script_id, limit, cursor)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/scripts/{script_id}/character-cards")
async def api_script_character_cards(request: Request, script_id: int, limit: int | None = None, cursor: str | None = None):
    user = require_user(request)
    try:
        return json_response({"ok": True, **knowledge.list_character_cards(user["id"], script_id, limit, cursor)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/scripts/{script_id}/character-cards/{card_id}")
async def api_script_character_card(request: Request, script_id: int, card_id: int):
    """单条剧本角色卡详情。"""
    user = require_user(request)
    try:
        card = knowledge.get_character_card(user["id"], script_id, card_id)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)
    if not card:
        return json_response({"ok": False, "error": "character_card 不存在"}, status_code=404)
    return json_response({"ok": True, "card": card})


@router.post("/api/scripts/{script_id}/character-cards")
async def api_script_upsert_character_card(request: Request, script_id: int):
    """创建/更新剧本角色卡（payload 传 id 则 update，否则 insert）。"""
    user = require_user(request)
    body = await request.json()
    try:
        return json_response({"ok": True, "card": knowledge.upsert_character_card(user["id"], script_id, body)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/scripts/{script_id}/character-cards/{card_id}/delete")
async def api_script_delete_character_card(request: Request, script_id: int, card_id: int):
    user = require_user(request)
    try:
        return json_response(knowledge.delete_character_card(user["id"], script_id, card_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


@router.post("/api/scripts/{script_id}/character-cards/{card_id}/enabled")
async def api_script_card_enabled(request: Request, script_id: int, card_id: int):
    """快捷切换 enabled（检索中临时屏蔽某角色）。"""
    user = require_user(request)
    body = await request.json()
    try:
        return json_response({"ok": True, "card": knowledge.set_character_card_enabled(
            user["id"], script_id, card_id, bool(body.get("enabled", True))
        )})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/scripts/{script_id}/worldbook")
async def api_script_worldbook(request: Request, script_id: int, limit: int | None = None, cursor: str | None = None):
    user = require_user(request)
    try:
        return json_response({"ok": True, **knowledge.list_worldbook_entries(user["id"], script_id, limit, cursor)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/saves")
async def api_saves(request: Request, limit: int | None = None, cursor: str | None = None):
    """轻量列表：只返摘要字段（turn/player_name/world_time/history_count），不含 state_snapshot。"""
    user = require_user(request)
    return json_response({"ok": True, **workspace.saves_page(user["id"], limit, cursor)})


@router.get("/api/saves/{save_id}/export")
async def api_save_export(request: Request, save_id: int):
    """下载存档 JSON（含 commits + messages + memories）。"""
    user = require_user(request)
    from . import save_io
    try:
        return json_response({"ok": True, **save_io.export_save(user["id"], save_id)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


@router.post("/api/saves/import")
async def api_save_import(request: Request):
    """上传一份导出的 JSON 恢复成新存档，按当前 user 重映射 owner。"""
    user = require_user(request)
    body = await request.json()
    from . import save_io
    try:
        return json_response(save_io.import_save(user["id"], body.get("payload") or body))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/saves/{save_id}")
async def api_save_detail(request: Request, save_id: int):
    """单条详情：包含完整 state_snapshot。"""
    user = require_user(request)
    try:
        return json_response({"ok": True, "save": workspace.save_detail(user["id"], save_id)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=403)


@router.post("/api/saves")
async def api_create_save(request: Request):
    user = require_user(request)
    body = await request.json()
    raw_script_id = body.get("script_id")
    if raw_script_id is None:
        return json_response({"ok": False, "error": "script_id 必填"}, status_code=400)
    try:
        script_id = int(raw_script_id)
    except (TypeError, ValueError):
        return json_response({"ok": False, "error": "script_id 必须为整数"}, status_code=400)
    # 校验 script 归属
    with connect() as db:
        owned = db.execute("select 1 from scripts where id = %s and owner_id = %s", (script_id, user["id"])).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该剧本"}, status_code=403)
    # task 29：把 UI 填的 new_card / character 传到 create_save，让初始 state_snapshot
    # 真的反映用户输入的姓名/身份/设定，否则 NewGameModal 的角色卡字段就被丢了。
    new_card = body.get("new_card") if isinstance(body.get("new_card"), dict) else None
    character: dict[str, Any] | None = None
    cid = body.get("character_id")
    ckind = body.get("character_kind")
    if cid is not None and ckind:
        character = {"id": cid, "kind": str(ckind)}
    return json_response({"ok": True, "save": workspace.create_save(
        user["id"], script_id, body.get("title", ""),
        new_card=new_card, character=character,
    )})


@router.get("/api/branches/{save_id}")
async def api_branches(request: Request, save_id: int, limit: int | None = None, cursor: str | None = None):
    user = require_user(request)
    # 先校验存档归属，避免 tree() 内部抛 raw exception
    with connect() as db:
        owned = db.execute("select 1 from game_saves where id = %s and user_id = %s", (save_id, user["id"])).fetchone()
    if not owned:
        return json_response({"ok": False, "error": "无权访问该存档"}, status_code=403)
    return json_response(branches.tree(user["id"], save_id, limit, cursor))


@router.post("/api/branches/continue")
async def api_continue_branch(request: Request):
    """task 38：接受两种 body 形态：
       A) {node_id: <int>}              —— 老路径，前端拿得到 commit id 时直接传
       B) {save_id, message_index, ...} —— Game Console 「从这里新建分支」用，
          后端把 message_index → turn_index → commit_id。
       缺字段或解析失败一律 400（不再因 int(None) 抛 TypeError 成 500）。"""
    user = require_user(request)
    body = await request.json() if (await request.body()) else {}
    node_id_raw = body.get("node_id")
    save_id_raw = body.get("save_id")
    msg_idx_raw = body.get("message_index")

    node_id: int | None = None
    if node_id_raw is not None and str(node_id_raw) != "":
        try:
            node_id = int(node_id_raw)
        except (TypeError, ValueError):
            return json_response({"ok": False, "error": "node_id 不是整数"}, status_code=400)

    if node_id is None and save_id_raw is not None and msg_idx_raw is not None:
        try:
            save_id = int(save_id_raw)
            message_index = int(msg_idx_raw)
        except (TypeError, ValueError):
            return json_response({"ok": False, "error": "save_id/message_index 不是整数"}, status_code=400)
        node_id = branches.resolve_commit_id_by_message(user["id"], save_id, message_index)
        if node_id is None:
            return json_response(
                {"ok": False, "error": f"无法在 save={save_id} 找到 message_index={message_index} 对应的提交"},
                status_code=400,
            )

    if node_id is None:
        return json_response(
            {"ok": False, "error": "缺字段：需要 node_id 或 (save_id + message_index)"},
            status_code=400,
        )
    try:
        return json_response(branches.continue_from(user["id"], node_id))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/branches/activate")
async def api_activate_branch(request: Request):
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(branches.activate_node(user["id"], int(body.get("node_id"))))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/branches/delete")
async def api_delete_branch(request: Request):
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(branches.delete_subtree(user["id"], int(body.get("node_id"))))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/saves/{save_id}/context-runs")
async def api_save_context_runs(request: Request, save_id: int, limit: int | None = None, cursor: str | None = None):
    user = require_user(request)
    try:
        return json_response({"ok": True, **knowledge.list_context_runs(user["id"], save_id, limit, cursor)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


# worldline variable 写入路由：见 ui.py（同时更新 runtime state 和 DB）
# 此处提供只读列表接口供前端管理面板使用
@router.get("/api/worldline/variables")
async def api_worldline_variables(request: Request):
    user = require_user(request)
    body = {"save_id": request.query_params.get("save_id")}
    try:
        save_id = _resolve_save_id(user["id"], body)
        return json_response({"ok": True, **knowledge.list_worldline_variables(user["id"], save_id)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/memories")
async def api_memories(request: Request):
    user = require_user(request)
    body = {"save_id": request.query_params.get("save_id")}
    try:
        save_id = _resolve_save_id(user["id"], body)
        return json_response({
            "ok": True,
            **knowledge.list_memories(
                user["id"],
                save_id,
                bucket=request.query_params.get("bucket"),
                limit=request.query_params.get("limit"),
                cursor=request.query_params.get("cursor"),
            ),
        })
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/settings")
async def api_settings(request: Request):
    user = require_user(request)
    return json_response({"ok": True, "settings": settings.list_settings(user["id"])})


@router.post("/api/settings")
async def api_save_setting(request: Request):
    user = require_user(request)
    body = await request.json()
    return json_response({"ok": True, "settings": settings.set_setting(user["id"], body.get("key", ""), body.get("value"))})


# ── 个人主页 ────────────────────────────────────────────────────────
@router.get("/api/me/profile")
async def api_my_profile(request: Request):
    """个人主页一次拉全：账户 + 用量摘要 + 凭证清单 + 偏好"""
    user = require_user(request)
    from . import usage as usage_mod
    from . import user_credentials
    with connect() as db:
        prefs_row = db.execute(
            "select preferences, updated_at from user_preferences where user_id = %s",
            (user["id"],),
        ).fetchone()
        save_count = db.execute(
            "select count(*) as n from game_saves where user_id = %s", (user["id"],)
        ).fetchone()
        script_count = db.execute(
            "select count(*) as n from scripts where owner_id = %s", (user["id"],)
        ).fetchone()
    return json_response({
        "ok": True,
        "user": {k: v for k, v in user.items() if k != "password_hash"},
        "stats": {
            "saves": int(save_count["n"]) if save_count else 0,
            "scripts": int(script_count["n"]) if script_count else 0,
        },
        "usage_30d": usage_mod.aggregate_usage(user["id"], days=30),
        "credentials": user_credentials.list_credentials(user["id"])["items"],
        "preferences": dict(prefs_row["preferences"]) if prefs_row else {},
        "preferences_updated_at": str(prefs_row["updated_at"]) if prefs_row else None,
    })


@router.get("/api/me/usage")
async def api_my_usage(request: Request):
    """单独的用量明细 API（dashboard 用）"""
    user = require_user(request)
    days = int(request.query_params.get("days") or 30)
    from . import usage as usage_mod
    return json_response(usage_mod.aggregate_usage(user["id"], days=days))


@router.get("/api/me/stats")
async def api_my_stats(request: Request):
    """玩家档案统计：回合数 / 分支 / 字数 / 连续登录。

    task 49（mock 清扫第二轮）：之前 MeOverview 用 totalRounds = saves.reduce(× 7)、
    playHours = totalRounds × 1.2 / 60，以及 "本周 +6.4h / 最深 6 层 / 共 418 万字 /
    7 天连续登录 / 最长 14 天" 全部硬编码。这里给出全部真实派生值；没有真实
    来源的字段（如累计游玩分钟数）返回 null，由前端显示「—」而不是假数字。
    """
    user = require_user(request)
    cur_token = request.cookies.get(SESSION_COOKIE) or ""
    with connect() as db:
        # 剧本汇总
        sc_row = db.execute(
            "select coalesce(count(*), 0) as n, "
            "coalesce(sum(word_count), 0) as words, "
            "coalesce(sum(chapter_count), 0) as chapters "
            "from scripts where owner_id = %s",
            (user["id"],),
        ).fetchone()
        # 存档数
        sv_row = db.execute(
            "select count(*) as n from game_saves where user_id = %s", (user["id"],)
        ).fetchone()
        # 回合数：每个 save 取最大 turn_index 后求和
        rounds_row = db.execute(
            """
            select coalesce(sum(per_save_max), 0) as n from (
              select max(b.turn_index) as per_save_max
              from branch_nodes b join game_saves s on s.id = b.save_id
              where s.user_id = %s
              group by b.save_id
            ) t
            """,
            (user["id"],),
        ).fetchone()
        # 分支节点总数（含主线节点）
        nodes_row = db.execute(
            """
            select count(*) as n
            from branch_nodes b join game_saves s on s.id = b.save_id
            where s.user_id = %s
            """,
            (user["id"],),
        ).fetchone()
        # 分支条数 = 同一父节点下"额外的"子节点（fork 出来的兄弟）
        # 主线一路接龙时 parent_id 唯一 child 不算分支；
        # 真正的 fork 是 parent 有 ≥2 个 child，分支数 = sum(siblings - 1)
        branches_row = db.execute(
            """
            select coalesce(sum(extra), 0) as n from (
              select count(*) - 1 as extra
              from branch_nodes b join game_saves s on s.id = b.save_id
              where s.user_id = %s and b.parent_id is not null
              group by b.parent_id
              having count(*) > 1
            ) t
            """,
            (user["id"],),
        ).fetchone()
        # 最深分支层数：用递归 CTE 算每个 save 的最大深度
        depth_row = db.execute(
            """
            with recursive bn as (
              select b.id, b.save_id, b.parent_id, 1 as depth
              from branch_nodes b join game_saves s on s.id = b.save_id
              where s.user_id = %s and b.parent_id is null
              union all
              select c.id, c.save_id, c.parent_id, bn.depth + 1
              from branch_nodes c join bn on c.parent_id = bn.id
            )
            select coalesce(max(depth), 0) as n from bn
            """,
            (user["id"],),
        ).fetchone()
        # 上次登录：当前 session 之外，最近一次 login_ok
        last_login_row = db.execute(
            """
            select created_at from login_audit
            where username = %s and event = 'login_ok'
            order by created_at desc
            offset 1 limit 1
            """,
            (user.get("username"),),
        ).fetchone()
        # 取最近 365 天的登录日期集合
        days_rows = db.execute(
            """
            select distinct date_trunc('day', created_at at time zone 'UTC')::date as d
            from login_audit
            where username = %s and event = 'login_ok'
              and created_at >= now() - interval '365 days'
            order by d desc
            """,
            (user.get("username"),),
        ).fetchall()
    # 用 Python 算连续登录天数
    from datetime import date, timedelta
    login_days = [r["d"] for r in days_rows]
    today = date.today()
    streak = 0
    if login_days and login_days[0] in (today, today - timedelta(days=1)):
        cur = login_days[0]
        for d in login_days:
            if d == cur:
                streak += 1
                cur = cur - timedelta(days=1)
            elif d < cur:
                break
    longest = 0
    if login_days:
        prev = None
        run = 0
        for d in login_days:  # desc 排序
            if prev is None or (prev - d).days == 1:
                run += 1
            else:
                longest = max(longest, run)
                run = 1
            prev = d
        longest = max(longest, run)
    return json_response({
        "ok": True,
        "imported": {
            "scripts": int(sc_row["n"] or 0),
            "words": int(sc_row["words"] or 0),
            "chapters": int(sc_row["chapters"] or 0),
        },
        "saves_count": int(sv_row["n"] or 0),
        "total_rounds": int(rounds_row["n"] or 0),
        "branch_nodes": int(nodes_row["n"] or 0),
        "branches": int(branches_row["n"] or 0),
        "max_branch_depth": int(depth_row["n"] or 0),
        "last_login_at": last_login_row["created_at"].isoformat() if last_login_row and last_login_row["created_at"] else None,
        "login_streak": int(streak),
        "longest_login_streak": int(longest),
        # 没有真实数据源的字段：显式 null，由 UI 显示 "—"，禁止编造
        "play_minutes_total": None,
        "play_minutes_week": None,
    })


@router.post("/api/me/preference")
async def api_set_preference(request: Request):
    """更新或合并界面偏好（主题/字号/默认模型...）"""
    user = require_user(request)
    body = await request.json()
    # 支持两种写法：整对象覆盖 (replace=true) 或 patch 合并 (默认)
    replace = bool(body.get("replace", False))
    payload = body.get("preferences") if "preferences" in body else body.get("value", body)
    if not isinstance(payload, dict):
        return json_response({"ok": False, "error": "preferences 必须是对象"}, status_code=400)
    with connect() as db:
        if replace:
            row = db.execute(
                """
                insert into user_preferences(user_id, preferences) values (%s, %s)
                on conflict(user_id) do update set preferences = excluded.preferences, updated_at = now()
                returning preferences, updated_at
                """,
                (user["id"], Jsonb(payload)),
            ).fetchone()
        else:
            row = db.execute(
                """
                insert into user_preferences(user_id, preferences) values (%s, %s)
                on conflict(user_id) do update set
                  preferences = user_preferences.preferences || excluded.preferences,
                  updated_at = now()
                returning preferences, updated_at
                """,
                (user["id"], Jsonb(payload)),
            ).fetchone()
    return json_response({"ok": True, "preferences": dict(row["preferences"]), "updated_at": str(row["updated_at"])})


# ── 用户级 API 凭证（加密存储，按用户隔离）──────────────────────────────
# ── 用户级 persona / character card（独立于剧本存档）─────────────
@router.get("/api/me/personas")
async def api_my_personas(request: Request):
    """列出本人所有玩家身份卡（杭雁菱穿越者 / 林知意信使 / ...）"""
    user = require_user(request)
    from . import user_cards
    return json_response(user_cards.list_personas(user["id"]))


@router.post("/api/me/personas")
async def api_upsert_persona(request: Request):
    """创建或更新 persona。传 id 强制更新某条；否则按 slug upsert。"""
    user = require_user(request)
    body = await request.json()
    from . import user_cards
    try:
        return json_response({"ok": True, "persona": user_cards.upsert_persona(user["id"], body)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/personas/{persona_id}")
async def api_get_persona(request: Request, persona_id: int):
    user = require_user(request)
    from . import user_cards
    p = user_cards.get_persona(user["id"], persona_id)
    if not p:
        return json_response({"ok": False, "error": "persona 不存在"}, status_code=404)
    return json_response({"ok": True, "persona": p})


@router.post("/api/me/personas/{persona_id}/delete")
async def api_delete_persona(request: Request, persona_id: int):
    user = require_user(request)
    from . import user_cards
    return json_response(user_cards.delete_persona(user["id"], persona_id))


@router.get("/api/me/character-cards")
async def api_my_character_cards(request: Request):
    """用户自创的 NPC 卡库，可挂任何剧本/存档"""
    user = require_user(request)
    from . import user_cards
    q = request.query_params.get("q") or None
    enabled = request.query_params.get("enabled") == "1"
    return json_response(user_cards.list_user_cards(user["id"], q=q, enabled_only=enabled))


@router.post("/api/me/character-cards")
async def api_upsert_character_card(request: Request):
    user = require_user(request)
    body = await request.json()
    from . import user_cards
    try:
        return json_response({"ok": True, "card": user_cards.upsert_user_card(user["id"], body)})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/character-cards/{card_id}")
async def api_get_character_card(request: Request, card_id: int):
    user = require_user(request)
    from . import user_cards
    c = user_cards.get_user_card(user["id"], card_id)
    if not c:
        return json_response({"ok": False, "error": "card 不存在"}, status_code=404)
    return json_response({"ok": True, "card": c})


@router.post("/api/me/character-cards/{card_id}/delete")
async def api_delete_character_card(request: Request, card_id: int):
    user = require_user(request)
    from . import user_cards
    return json_response(user_cards.delete_user_card(user["id"], card_id))


# ── 酒馆 (SillyTavern) 角色卡兼容 ───────────────────────────────────
@router.post("/api/me/character-cards/import-tavern")
async def api_import_tavern_card(request: Request):
    """导入酒馆角色卡。

    payload 形态（支持多种来源）：
    - {"json": {...V2 dict...}}                # 直接传 V2 对象
    - {"json_string": "{...}"}                  # JSON 字符串
    - {"base64": "..."}                          # base64-encoded JSON
    - {"png_base64": "..."}                      # PNG 文件 base64（解析 tEXt chunk）
    """
    user = require_user(request)
    body = await request.json()
    from . import tavern_cards, user_cards
    try:
        if body.get("png_base64"):
            import base64 as _b64
            try:
                blob = _b64.b64decode(body["png_base64"], validate=True)
            except Exception as exc:
                raise ValueError(f"png_base64 不合法：{exc}")
            v2 = tavern_cards.parse_png_card(blob)
        elif body.get("json") is not None:
            v2 = tavern_cards.parse_card(body["json"])
        elif body.get("json_string"):
            v2 = tavern_cards.parse_card(body["json_string"])
        elif body.get("base64"):
            v2 = tavern_cards.parse_card(body["base64"])
        else:
            return json_response({"ok": False, "error": "需要 json / json_string / base64 / png_base64 之一"}, status_code=400)

        payload = tavern_cards.tavern_to_user_card(v2)
        card = user_cards.upsert_user_card(user["id"], payload)
        return json_response({"ok": True, "card": card, "imported_from": "tavern_v2"})
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/me/character-cards/{card_id}/export-tavern")
async def api_export_tavern_card(request: Request, card_id: int):
    """导出本人 NPC 卡为酒馆 V2 JSON 格式（可直接下载/给酒馆导入）。"""
    user = require_user(request)
    from . import user_cards, tavern_cards
    card = user_cards.get_user_card(user["id"], card_id)
    if not card:
        return json_response({"ok": False, "error": "card 不存在"}, status_code=404)
    v2 = tavern_cards.user_card_to_tavern_v2(card)
    return json_response({"ok": True, "card": v2, "spec": "chara_card_v2"})


@router.get("/api/me/character-cards/{card_id}/export-png")
async def api_export_tavern_png(request: Request, card_id: int):
    """导出 PNG 嵌入式酒馆卡（tEXt chara chunk），可直接拖进酒馆。"""
    from fastapi.responses import Response
    user = require_user(request)
    from . import user_cards, tavern_cards
    card = user_cards.get_user_card(user["id"], card_id)
    if not card:
        return json_response({"ok": False, "error": "card 不存在"}, status_code=404)
    v2 = tavern_cards.user_card_to_tavern_v2(card)
    png = tavern_cards.write_png_card(v2)
    name = (card.get("name") or f"card_{card_id}").replace(" ", "_")
    return Response(
        content=png, media_type="image/png",
        headers={"Content-Disposition": f'attachment; filename="{name}.png"'},
    )


@router.post("/api/scripts/batch-import")
async def api_scripts_batch_import(request: Request):
    """从 ZIP 包批量导入剧本：每个 TXT/MD 视为一本书。

    Body: {"file": {"name": "books.zip", "base64": "..."}}
    """
    user = require_user(request)
    body = await request.json()
    file_item = body.get("file") or {}
    if not file_item:
        return json_response({"ok": False, "error": "缺 file"}, status_code=400)
    from .library import decode_upload
    try:
        raw = decode_upload(file_item)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)

    import io
    import zipfile
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        return json_response({"ok": False, "error": "不是合法 ZIP 文件"}, status_code=400)

    imported = []
    failed = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith((".txt", ".md"))]
        if len(names) > 50:
            return json_response({"ok": False, "error": "ZIP 最多包含 50 个文件"}, status_code=400)
        for name in names:
            try:
                content = zf.read(name)
                if len(content) > script_import.MAX_SCRIPT_UPLOAD_BYTES:
                    failed.append({"name": name, "error": "too large"})
                    continue
                import base64 as _b64
                result = script_import.import_script(
                    user["id"],
                    file_item={"name": name.rsplit("/", 1)[-1], "base64": _b64.b64encode(content).decode()},
                    split_rule=body.get("split_rule", "auto"),
                )
                imported.append({"name": name, "script_id": result["script"]["id"]})
            except Exception as exc:
                failed.append({"name": name, "error": str(exc)[:200]})
    return json_response({
        "ok": True, "imported": imported, "failed": failed,
        "total": len(names), "succeeded": len(imported),
    })


@router.get("/api/me/credentials")
async def api_my_credentials(request: Request):
    """列出当前用户已配置的 API 凭证（不含 raw key）"""
    user = require_user(request)
    from . import user_credentials
    return json_response(user_credentials.list_credentials(user["id"]))


@router.post("/api/me/credentials")
async def api_set_credential(request: Request):
    """设置/更新当前用户某个 provider 的 API key。

    base_url_override 仅 admin 可设；普通用户的 base_url 强制走 catalog。
    """
    user = require_user(request)
    body = await request.json()
    from . import user_credentials
    is_admin = user.get("role") == "admin"
    try:
        result = user_credentials.set_credential(
            user["id"],
            body.get("api_id", ""),
            body.get("api_key", ""),
            base_url_override=body.get("base_url_override", "") if is_admin else "",
            enabled=bool(body.get("enabled", True)),
            allow_base_url=is_admin,
        )
        return json_response(result)
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/me/credentials/delete")
async def api_delete_credential(request: Request):
    user = require_user(request)
    body = await request.json()
    from . import user_credentials
    return json_response(user_credentials.delete_credential(user["id"], body.get("api_id", "")))


@router.get("/api/me/credentials/test")
async def api_test_credential(request: Request):
    """用户级凭证可用性自检（不暴露 key）"""
    user = require_user(request)
    api_id = request.query_params.get("api_id", "")
    from . import user_credentials
    cred = user_credentials.get_credential(user["id"], api_id)
    return json_response({
        "ok": True,
        "api_id": api_id,
        "has_credential": cred is not None,
        "base_url_override": (cred or {}).get("base_url_override", ""),
    })


@router.get("/api/library")
async def api_library(request: Request, path: str = "", limit: int | None = None, cursor: str | None = None):
    user = require_user(request)
    try:
        return json_response(library.list_dir(user["id"], path, limit, cursor))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/library/upload")
async def api_library_upload(request: Request):
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(library.upload(user["id"], body.get("path", ""), body.get("files") or []))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/library/mkdir")
async def api_library_mkdir(request: Request):
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(library.mkdir(user["id"], body.get("path", "")))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/api/library/delete")
async def api_library_delete(request: Request):
    user = require_user(request)
    body = await request.json()
    try:
        return json_response(library.delete(user["id"], body.get("path", "")))
    except ValueError as exc:
        return json_response({"ok": False, "error": str(exc)}, status_code=400)


@router.get("/api/library/download")
async def api_library_download(request: Request, path: str) -> FileResponse:
    user = require_user(request)
    try:
        target = library.download_path(user["id"], path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.exists():
        raise HTTPException(status_code=404, detail="file not found")
    # 安全：所有用户上传文件强制下载，不允许浏览器把它当 html/svg/js 解析执行
    # 这避免了上传 .html → 同源 XSS、上传 .svg → 内嵌 JS 等场景
    download_name = target.name
    return FileResponse(
        target,
        media_type="application/octet-stream",
        filename=download_name,
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.get("/api/platform/commands")
async def api_commands(request: Request):
    """命令清单：未登录 + 服务器模式下拒绝；登录用户可见，但隐藏 admin-only 命令"""
    user = current_user(request)
    if not user and _auth_required():
        return json_response({"ok": False, "error": "需要登录"}, status_code=401)
    return json_response({"ok": True, "commands": command_payload()})


def _auth_required() -> bool:
    """与 ui.py:_api_auth_required 同义，避免循环导入；服务器模式禁止匿名访问。"""
    explicit = os.environ.get("RPG_REQUIRE_AUTH", "").strip()
    if explicit == "1":
        return True
    if explicit == "0":
        return False
    mode = os.environ.get("RPG_DEPLOYMENT_MODE", "local").strip().lower()
    return mode not in {"local", "desktop", "self_hosted", "self-hosted"}


def current_user(request: Request) -> dict | None:
    try:
        init_db()
        user = auth.user_from_token(request.cookies.get(SESSION_COOKIE))
        if user:
            workspace.ensure_default(user["id"])
        return user
    except Exception:
        return None


def require_user(request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="需要登录")
    return user


def _resolve_save_id(user_id: int, body: dict) -> int:
    raw = body.get("save_id")
    if raw:
        return int(raw)
    with connect() as db:
        row = db.execute(
            "select id from game_saves where user_id = %s order by updated_at desc, id desc limit 1",
            (user_id,),
        ).fetchone()
    if not row:
        raise ValueError("还没有可写入的存档")
    return int(row["id"])


def platform_for(user: dict | None) -> dict:
    """构建 /api/platform 和注册/登录响应的 payload。

    安全：MCP server 的 command/args/env 含 secret，普通用户必须脱敏。
    与 ui.py:_redact_tools 共用同一份逻辑，避免再次出现"漏脱敏入口"。
    """
    payload = workspace.overview(user)
    is_admin = bool(user and user.get("role") == "admin")
    payload["tools"] = _redact_mcp_in_tools(tool_payload(), is_admin)
    payload["commands"] = command_payload()
    if user:
        payload["library"] = library.list_dir(user["id"], "")
    return payload


_MCP_SECRET_FIELDS = ("command", "args", "env", "credential", "secret", "token")


def _redact_mcp_in_tools(tools: dict, is_admin: bool) -> dict:
    """递归脱敏 tools 里的 mcp.servers[].command/args/env。"""
    if is_admin:
        return tools
    import copy
    out = copy.deepcopy(tools)
    for srv in ((out.get("mcp") or {}).get("servers") or []):
        for field in _MCP_SECRET_FIELDS:
            srv.pop(field, None)
    for srv in (out.get("mcp_servers") or []):
        for field in _MCP_SECRET_FIELDS:
            srv.pop(field, None)
    return out


def command_payload() -> list[dict]:
    return [{"method": method, "path": path, "name": path.rsplit("/", 1)[-1] or path, "desc": desc} for method, path, desc in COMMANDS]
