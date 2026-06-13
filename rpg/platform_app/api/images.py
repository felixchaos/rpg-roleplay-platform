"""platform_app.api.images — 生图存储、服务与触发端点。

Phase 1 Agent A 实现：
  - store_image(data, *, user_id, kind, ext) -> str   落盘 + 返回相对 URL
  - ai_images CRUD helpers
  - GET  /api/images/file/{filename}                  静态文件服务
  - POST /api/images/generate                         触发异步生图（入队给 Agent C 的 worker）

Phase 3 增量：
  - create_image_record 加 save_id 参数
  - POST /api/images/generate 收 save_id + 配额 quota_exceeded 回报
  - GET  /api/images/list?save_id=X                   按存档列生图记录
  - cleanup_old_chat_images(days)                      清理旧 chat/game 图片（调度待办）
"""
from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from ._deps import json_response, require_user
from ..db import connect, init_db

router = APIRouter()

# 白名单：允许存储/服务的图片扩展名
_ALLOWED_EXTS: frozenset[str] = frozenset({"png", "jpg", "jpeg", "webp"})


# ══════════════════════════════════════════════════════════════════════
#  存储抽象（委托给 platform_app.storage）
# ══════════════════════════════════════════════════════════════════════

def store_image(data: bytes, *, user_id: int, kind: str, ext: str = "png") -> str:
    """写图片到统一存储并返回相对 URL。

    文件名格式：ai_{user_id}_{random_hex}.{ext}
    返回值：/api/images/file/{filename}（保持旧 URL 形式兼容，OSS 时改此处）
    """
    from platform_app import storage  # lazy import，避免循环

    ext_clean = ext.lstrip(".").lower()
    if ext_clean not in _ALLOWED_EXTS:
        ext_clean = "png"
    filename = f"ai_{int(user_id)}_{secrets.token_hex(12)}.{ext_clean}"
    _key, url = storage.store_bytes(data, kind="ai_images", filename=filename)
    return url


# ══════════════════════════════════════════════════════════════════════
#  ai_images CRUD helpers
# ══════════════════════════════════════════════════════════════════════

def create_image_record(
    *,
    user_id: int,
    kind: str,
    prompt: str,
    api_id: str | None = None,
    model: str | None = None,
    params: dict | None = None,
    save_id: str | None = None,
) -> int:
    """INSERT 一行 ai_images，返回新行 id。

    save_id: 可选，关联游戏存档 ID（Phase 3 新增）。
    """
    from psycopg.types.json import Jsonb

    init_db()
    with connect() as db:
        row = db.execute(
            """
            insert into ai_images (user_id, kind, api_id, model, prompt, params, status, save_id)
            values (%s, %s, %s, %s, %s, %s, 'pending', %s)
            returning id
            """,
            (
                int(user_id),
                kind,
                api_id or None,
                model or None,
                prompt,
                Jsonb(params or {}),
                save_id or None,
            ),
        ).fetchone()
    if not row:
        raise RuntimeError("create_image_record: no id returned")
    return int(row["id"])


def update_image_record(
    image_id: int,
    status: str,
    *,
    url: str | None = None,
    error: str | None = None,
) -> None:
    """更新 ai_images 行的 status / url / error。"""
    init_db()
    with connect() as db:
        db.execute(
            """
            update ai_images
               set status = %s,
                   url    = coalesce(%s, url),
                   error  = coalesce(%s, error)
             where id = %s
            """,
            (status, url, error, int(image_id)),
        )


def get_image_record(image_id: int) -> dict[str, Any] | None:
    """按 id 查 ai_images 行，不存在返回 None。"""
    init_db()
    with connect() as db:
        row = db.execute(
            "select * from ai_images where id = %s",
            (int(image_id),),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_user_images(
    user_id: int,
    *,
    kind: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """列出某用户的 ai_images，按 created_at desc 排序。"""
    init_db()
    with connect() as db:
        if kind:
            rows = db.execute(
                """
                select * from ai_images
                 where user_id = %s and kind = %s
                 order by created_at desc
                 limit %s
                """,
                (int(user_id), kind, int(limit)),
            ).fetchall()
        else:
            rows = db.execute(
                """
                select * from ai_images
                 where user_id = %s
                 order by created_at desc
                 limit %s
                """,
                (int(user_id), int(limit)),
            ).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════
#  路由：静态文件服务
# ══════════════════════════════════════════════════════════════════════

@router.get("/api/images/file/{filename}")
async def api_image_file(filename: str, request: Request) -> FileResponse:
    """服务 platform_data/ai_images/ 下的图片文件（旧 URL 保留兼容）。

    鉴权链接:必须登录(cookie)才能取图,未登录 → 401(同 /api/storage)。
    安全防护：
      1. 路径穿越：文件名不得含 / \\ .. 或以 . 开头。
      2. 扩展名白名单：只允许 png / jpg / jpeg / webp。
      3. 实际路径由 storage.resolve_path 做根限定（防穿越/symlink 逃逸）。
    """
    require_user(request)
    from platform_app import storage  # lazy import

    # 1. 路径穿越检查（直接拒绝任何非纯文件名字符）
    if (
        "/" in filename
        or "\\" in filename
        or ".." in filename
        or filename.startswith(".")
    ):
        raise HTTPException(status_code=400, detail="非法文件名")

    # 2. 扩展名白名单
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail="不支持的文件类型")

    # 3. 通过 storage 解析真实路径（内部做路径穿越防护）
    try:
        target = storage.resolve_path("ai_images/" + filename)
    except ValueError:
        raise HTTPException(status_code=400, detail="非法路径")

    if not target.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(str(target))


# ══════════════════════════════════════════════════════════════════════
#  路由：触发生图
# ══════════════════════════════════════════════════════════════════════

@router.post("/api/images/generate")
async def api_generate_image(request: Request):
    """UI 按钮入口：接收生图请求，入队异步 job，立即返回 {image_id, status}。

    body: {prompt, kind, api_id?, model?, ref?, attach?, save_id?}

    attach 可选，格式：
      {"type": "user_avatar"}
      {"type": "card_avatar",  "card_id": <int>}
      {"type": "script_cover", "script_id": <int>}
    worker 完成后把 url 写回目标（带 ownership 校验）。

    save_id 可选：关联游戏存档，用于 GET /api/images/list 按存档查询（Phase 3）。

    若触发每日配额限制，返回 {ok: False, code: "quota_exceeded", status: "failed"}。
    """
    user = require_user(request)
    user_id: int = int(user["id"])

    try:
        body = await request.json()
    except Exception:
        body = {}

    prompt: str = str(body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt 不能为空")

    kind: str = str(body.get("kind") or "chat").strip()
    api_id: str | None = body.get("api_id") or None
    model: str | None = body.get("model") or None
    ref: str | None = body.get("ref") or None
    attach: dict | None = body.get("attach") or None
    save_id: str | None = str(body.get("save_id") or "").strip() or None
    # 分辨率(UI 或 LLM 都可传):宽高/比例白名单,形如 1024x1024 / 832*1216 / 16:9;非法忽略由 provider 默认
    import re as _re_size
    _raw_size = str(body.get("size") or "").strip()
    size: str | None = _raw_size if _re_size.match(r"^\d{1,5}[x*:]\d{1,5}$", _raw_size) else None

    # 校验 attach 结构 + 入队前归属鉴权
    if attach is not None:
        attach_type = attach.get("type") or ""
        if attach_type not in ("user_avatar", "card_avatar", "script_cover"):
            raise HTTPException(
                status_code=400,
                detail=f"attach.type 无效: {attach_type!r}，合法值：user_avatar / card_avatar / script_cover",
            )
        # S3: 入队前校验归属，防耗配额/信息泄露
        if attach_type == "card_avatar":
            card_id = attach.get("card_id") or attach.get("id")
            if not card_id:
                raise HTTPException(status_code=400, detail="attach.card_id 不能为空")
            init_db()
            _att_script_id = attach.get("script_id")
            with connect() as db:
                if not _att_script_id:
                    # 兜底:NPC 卡(card_type='npc'、user_id=NULL、挂 script_id)未带 script_id 时,
                    # 从卡自身取并注入 attach —— 让入队鉴权与 worker 写回都走 scripts.owner_id,
                    # 不再误落到 user_id 分支报 403(剧本 owner 给本剧本 NPC 卡生图本应允许)。
                    _c = db.execute(
                        "select user_id, script_id, card_type from character_cards where id = %s",
                        (int(card_id),),
                    ).fetchone()
                    if (_c and _c.get("card_type") == "npc"
                            and _c.get("script_id") and not _c.get("user_id")):
                        _att_script_id = int(_c["script_id"])
                        attach["script_id"] = _att_script_id
                if _att_script_id:
                    # NPC 卡(user_id=NULL,挂 script_id):owner 走 scripts.owner_id
                    row = db.execute(
                        "select 1 from scripts where id = %s and owner_id = %s",
                        (int(_att_script_id), user_id),
                    ).fetchone()
                else:
                    row = db.execute(
                        "select 1 from character_cards where id = %s and user_id = %s",
                        (int(card_id), user_id),
                    ).fetchone()
            if not row:
                raise HTTPException(status_code=403, detail="无权为该角色卡生图：卡不存在或不属于当前用户")
        elif attach_type == "script_cover":
            script_id = attach.get("script_id") or attach.get("id")
            if not script_id:
                raise HTTPException(status_code=400, detail="attach.script_id 不能为空")
            init_db()
            with connect() as db:
                row = db.execute(
                    "select 1 from scripts where id = %s and owner_id = %s",
                    (int(script_id), user_id),
                ).fetchone()
            if not row:
                raise HTTPException(status_code=403, detail="无权为该剧本生图：剧本不存在或不属于当前用户")
        # user_avatar: 始终放行（只能给自己生）

    from platform_app.image_jobs import enqueue_image_generation  # type: ignore[import]

    _gen_extra: dict = {}
    if ref:
        _gen_extra["ref"] = ref
    if size:
        _gen_extra["size"] = size
    result: dict = enqueue_image_generation(
        user_id,
        prompt,
        kind,
        api_id=api_id,
        model=model,
        origin="api_direct",
        extra=_gen_extra or None,
        attach=attach,
        save_id=save_id,
    )

    # 每日配额超限：enqueue 返回 error="quota_exceeded"
    if result.get("error") == "quota_exceeded":
        return json_response(
            {"ok": False, "code": "quota_exceeded", "status": "failed"},
            status_code=429,
        )

    return json_response({"ok": True, **result})


@router.get("/api/images/list")
async def api_list_images(request: Request):
    """按存档列出该用户的生图记录（仅 owner 可查）。

    query: save_id (必填)
    返回：[{id, url, kind, prompt, status, created_at}] 按 created_at desc 排序。
    """
    user = require_user(request)
    user_id: int = int(user["id"])

    save_id: str = str(request.query_params.get("save_id") or "").strip()
    if not save_id:
        raise HTTPException(status_code=400, detail="save_id 不能为空")

    init_db()
    with connect() as db:
        rows = db.execute(
            """
            select id, url, kind, prompt, status, created_at
              from ai_images
             where user_id = %s and save_id = %s
             order by created_at desc
            """,
            (user_id, save_id),
        ).fetchall()

    results = [
        {
            "id": r["id"],
            "url": r["url"] or "",
            "kind": r["kind"] or "",
            "prompt": r["prompt"] or "",
            "status": r["status"] or "pending",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        }
        for r in rows
    ]
    return json_response({"ok": True, "images": results})


@router.get("/api/images/{image_id}")
async def api_get_image(image_id: int, request: Request):
    """查询单张生图记录，仅 owner 可查。

    返回：{id, status, url, error, kind}
    status: pending | generating | done | failed
    """
    user = require_user(request)
    user_id: int = int(user["id"])

    record = get_image_record(image_id)
    if record is None or int(record.get("user_id") or 0) != user_id:
        raise HTTPException(status_code=404, detail="图片记录不存在或无权访问")

    return json_response({
        "ok": True,
        "id": record["id"],
        "status": record.get("status") or "pending",
        "url": record.get("url") or "",
        "error": record.get("error") or "",
        "kind": record.get("kind") or "",
    })


# ══════════════════════════════════════════════════════════════════════
#  保留策略：清理旧 chat/game 图片
# ══════════════════════════════════════════════════════════════════════

def cleanup_old_chat_images(days: int = 14) -> int:
    """删除 kind in ('chat','game') 且超过 days 天的 ai_images 行及对应本地文件。

    返回删除的行数。

    调度待办：
      本期只提供函数体，不绑定任何调度器。
      建议后续在 postproc worker 的定时任务中（或独立 cron）每天调用一次：
        from platform_app.api.images import cleanup_old_chat_images
        cleanup_old_chat_images(days=14)
    """
    import logging as _logging
    _log = _logging.getLogger(__name__)

    init_db()
    with connect() as db:
        # 先取出要删的行（需要 url 来定位本地文件）
        rows = db.execute(
            """
            select id, url from ai_images
             where kind in ('chat', 'game')
               and created_at < now() - (%s || ' days')::interval
            """,
            (str(int(days)),),
        ).fetchall()

        if not rows:
            return 0

        ids = [int(r["id"]) for r in rows]

        # 删 DB 行
        db.execute(
            "delete from ai_images where id = any(%s::bigint[])",
            (ids,),
        )

    # 尝试删本地文件（失败逐条忽略，不影响已删 DB 行）
    from platform_app import storage as _storage  # lazy import
    deleted_files = 0
    for r in rows:
        url: str = r["url"] or ""
        # URL 格式：/api/images/file/{filename}
        if url.startswith("/api/images/file/"):
            filename = url[len("/api/images/file/"):]
            # 安全检查：只允许纯文件名
            if filename and "/" not in filename and "\\" not in filename and not filename.startswith("."):
                try:
                    _storage.delete_file("ai_images/" + filename)
                    deleted_files += 1
                except Exception as exc:
                    _log.debug("[cleanup_old_chat_images] unlink failed %s: %s", filename, exc)

    _log.info(
        "[cleanup_old_chat_images] deleted %d rows / %d files (days=%d)",
        len(ids), deleted_files, days,
    )
    return len(ids)
