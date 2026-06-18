"""platform_app.api.feedback — FB-01/02/03/04/07/08 反馈提交与管理接口。

路由:
  POST   /api/feedback                        — 用户提交反馈 (FB-01)
  GET    /api/me/feedback                     — 用户查看自己的反馈列表 (FB-07)
  DELETE /api/feedback/{id}                   — 用户撤回单条 unreviewed (FB-08)
  POST   /api/me/feedback/delete-all          — 用户撤销所有 (FB-08)
  GET    /api/admin/feedback                  — admin 审查队列 (FB-03)
  POST   /api/admin/feedback/{id}/decision    — admin 标记 ok|nsfw_terminate|spam (FB-03)

FB-04 NSFW 预审:
  POST /api/feedback 在写 DB 前调用 moderation.moderate_feedback()。
  - auto_reject (CSAM): 立刻终止账号 + 写 nsfw_terminate 行(不存原文)。
  - manual_review: 写入但在 excerpts_jsonb.__moderation__ 附加 verdict 摘要。
  - pass: 正常写入，附加低分摘要供 admin 参考。
  - API key 缺失: 全量 manual_review 降级（不拦截）。

consent_token 设计:
  前端把当时展示给用户的同意文案做 SHA256 (hex)，随请求带上。
  服务端只做长度/格式校验后存入 feedback 行，供后续合规 audit 比对。
  不在服务端重算文案——这样文案升版本时历史 token 仍可追溯。
"""
from __future__ import annotations

import hashlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from ..db import connect
from ..moderation import moderate_feedback
from ._deps import _client_ip, json_response, require_admin, require_user

router = APIRouter()
log = logging.getLogger(__name__)

# 50 KB (free_text + excerpts JSON 合计)
_MAX_PAYLOAD_BYTES = 50 * 1024
_VALID_DECISIONS = {"ok", "nsfw_terminate", "spam"}


# admin 角色门控收敛到 _deps.require_admin(唯一来源);保留本名供 Depends(_require_admin) 旧引用。
_require_admin = require_admin

# 匿名(桌面本地版)反馈:同一 IP 每小时上限,防滥用(无登录闸,这是主要节流)。
_ANON_RATE_PER_HOUR = 20


def _match_user_id_by_email(db, contact_email: str) -> int | None:
    """给定联系邮箱,找「重名登录账户」并归属过去(用户诉求:本地邮箱==登录邮箱则默认同一账户)。

    匹配规则与登录完全一致(security.normalize_username/normalize_email):
      - path A:username 就是邮箱(多数历史账户),用 normalize_username 精确匹配;
      - path B:独立 email 列,用 normalize_email 且要求 email_verified(防冒名,与登录 email 路径一致)。
    优先 path A 精确命中。匹配不到返回 None(=真匿名)。
    """
    email = (contact_email or "").strip()
    if not email or "@" not in email:
        return None
    from ..security import normalize_email, normalize_username
    uname_key = normalize_username(email)
    email_key = normalize_email(email)
    row = db.execute(
        """
        select id from users
        where deactivated_at is null
          and (
            username = %s
            or (lower(email) = %s and email_verified = true and length(email) > 0)
          )
        order by case when username = %s then 0 else 1 end
        limit 1
        """,
        (uname_key, email_key, uname_key),
    ).fetchone()
    return int(row["id"]) if row else None


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/feedback — 用户提交
# ──────────────────────────────────────────────────────────────────────────────

async def _forward_feedback_to_central(body: dict, ua: str):
    """自部署(local/desktop)模式:本机没有「中央 admin」处理反馈 →
    把反馈转发到我的中央服务器(/api/feedback/anon),让自部署用户的反馈 + 邮件回执闭环走通。
    中央地址固定取自部署环境变量(非用户/请求可控),不构成 SSRF;默认指向正式站。"""
    import os

    import httpx

    central = (os.environ.get("RPG_CENTRAL_URL") or "https://rpg-roleplay.stellatrix.icu").rstrip("/")
    url = f"{central}/api/feedback/anon"
    payload = {
        "free_text": body.get("free_text", "") or "",
        "excerpts": body.get("excerpts", []) or [],
        "consent_token": body.get("consent_token", "") or "",
        "contact_email": (body.get("contact_email", "") or "").strip()[:320],
        "client_id": (body.get("client_id") or os.environ.get("RPG_CLIENT_ID", "") or "").strip()[:128],
        "app_version": (body.get("app_version", "") or "")[:64],
        "env_snapshot": body.get("env_snapshot") if isinstance(body.get("env_snapshot"), dict) else {},
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json=payload, headers={"user-agent": ua or "rpg-desktop"})
        data: dict = {}
        try:
            data = resp.json()
        except Exception:
            data = {}
        if resp.status_code >= 400:
            # 透传中央的 4xx 文案(NSFW 终止 / 限流 / 校验),前端已能识别这些 error_key。
            detail = data.get("detail") if isinstance(data, dict) else None
            raise HTTPException(status_code=resp.status_code, detail=detail or f"中央服务器返回 {resp.status_code}")
        return json_response({
            "ok": True,
            "forwarded": True,
            "feedback_id": data.get("feedback_id"),
            "linked": data.get("linked", False),
        })
    except HTTPException:
        raise
    except Exception as exc:
        log.warning("forward feedback to central failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"反馈转发到服务器失败:{exc}")


@router.post("/api/feedback")
async def submit_feedback(request: Request, user=Depends(require_user)):
    """FB-01/02: 提交反馈 + 写 consent_log。自部署(local/desktop)模式转发到中央服务器。"""
    body = await request.json()
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")

    # 自部署模式:转发到中央服务器(本机库不存),走 anon 收集 + 邮件回执闭环。
    try:
        from core.config import deployment_mode as _dmode

        _mode = (_dmode() or "").strip().lower()
    except Exception:
        _mode = ""
    if _mode in {"local", "desktop", "self_hosted", "self-hosted"}:
        return await _forward_feedback_to_central(body, ua)

    free_text: str = body.get("free_text", "") or ""
    excerpts = body.get("excerpts", []) or []
    consent_token: str = body.get("consent_token", "") or ""
    app_version: str = body.get("app_version", "") or ""
    # 登录用户也可选填联系邮箱:想另收一份邮件回执时用(默认用账户邮箱)。
    contact_email: str = (body.get("contact_email", "") or "").strip()[:320]

    # ── 校验 consent_token（SHA256 hex，64 字符）──────────────────────────────
    if not consent_token or len(consent_token) != 64:
        raise HTTPException(
            status_code=400,
            detail="consent_token 缺失或格式不正确（须为 64 字符 SHA256 hex）",
        )
    try:
        int(consent_token, 16)
    except ValueError:
        raise HTTPException(status_code=400, detail="consent_token 不是合法的 hex 字符串")

    # ── 校验总长 50KB ─────────────────────────────────────────────────────────
    excerpts_raw = json.dumps(excerpts, ensure_ascii=False)
    total_bytes = len(free_text.encode("utf-8")) + len(excerpts_raw.encode("utf-8"))
    if total_bytes > _MAX_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"free_text + excerpts 超过 50KB 上限（当前 {total_bytes} 字节）",
        )

    # ── excerpts 结构简单校验 ──────────────────────────────────────────────────
    if not isinstance(excerpts, list):
        raise HTTPException(status_code=400, detail="excerpts 须为数组")
    for i, ex in enumerate(excerpts):
        if not isinstance(ex, dict):
            raise HTTPException(status_code=400, detail=f"excerpts[{i}] 须为对象")

    # ── FB-04 NSFW 预审 ────────────────────────────────────────────────────────
    # 只在反馈通道生效；不影响 GM / 对话 / 记忆等主数据流（成人内容产品允许 NSFW）。
    moderation_text = free_text + "\n" + excerpts_raw
    verdict = await moderate_feedback(moderation_text)

    if verdict.action == "auto_reject":
        # CSAM 红线：不存原文，只存 verdict 摘要；立刻终止账号。
        from ..dmca import queue_account_termination

        _csam_summary = json.dumps(
            {"__moderation__": {"action": "auto_reject", "categories": verdict.categories}},
            ensure_ascii=False,
        )
        with connect() as db:
            db.execute(
                """
                insert into feedback
                  (user_id, free_text, excerpts_jsonb, consent_token, ua, app_version, ip,
                   reviewed_at, review_decision)
                values (%s, %s, %s::jsonb, %s, %s, %s, %s, now(), 'nsfw_terminate')
                """,
                (
                    user["id"],
                    "[CSAM filter triggered — content not stored]",
                    _csam_summary,
                    consent_token,
                    ua,
                    app_version,
                    ip,
                ),
            )
            queue_account_termination(
                db,
                user["id"],
                reason=f"feedback CSAM filter (auto_reject): categories={verdict.categories}",
            )
            db.execute(
                """
                insert into feedback_consent_log
                  (user_id, consent_text_hash, app_version, ip)
                values (%s, %s, %s, %s)
                """,
                (user["id"], consent_token, app_version, ip),
            )
        log.error(
            "feedback auto_reject CSAM: user_id=%s categories=%s",
            user["id"],
            verdict.categories,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "error_key": "feedback.nsfw_terminate",
                "message": (
                    "反馈内容违反 AUP §2.J 红线，账号已终止；"
                    "30 天内可下载数据。详情见 legal/acceptable-use-policy。"
                ),
            },
        )

    # manual_review 或 pass：写入，把 moderation verdict 附加进 excerpts_jsonb
    # 用 __moderation__ key（双下划线前缀，不计为用户提交的 excerpt）。
    excerpts_with_verdict: list = list(excerpts)  # 浅拷贝，不改用户原始列表
    _verdict_meta: dict = {
        "__moderation__": {
            "action": verdict.action,
            "categories": verdict.categories,
            # 只保留非零得分，降低存储量
            "scores": {k: round(v, 4) for k, v in verdict.scores.items() if v > 0.001},
        }
    }
    # 以独立对象追加到 excerpts 数组末尾；admin UI 可识别 __moderation__ key 单独展示
    excerpts_with_verdict.append(_verdict_meta)
    excerpts_raw_final = json.dumps(excerpts_with_verdict, ensure_ascii=False)

    with connect() as db:
        # 写 feedback 行
        row = db.execute(
            """
            insert into feedback
              (user_id, free_text, excerpts_jsonb, consent_token, ua, app_version, ip, contact_email)
            values (%s, %s, %s::jsonb, %s, %s, %s, %s, %s)
            returning id
            """,
            (
                user["id"],
                free_text,
                excerpts_raw_final,
                consent_token,
                ua,
                app_version,
                ip,
                contact_email or None,
            ),
        ).fetchone()
        feedback_id = row["id"]

        # 写 feedback_consent_log 行（供 audit；即便 feedback 日后被删，此行保留）
        db.execute(
            """
            insert into feedback_consent_log
              (user_id, consent_text_hash, app_version, ip)
            values (%s, %s, %s, %s)
            """,
            (user["id"], consent_token, app_version, ip),
        )

    log.info(
        "feedback submitted: id=%s user_id=%s moderation=%s",
        feedback_id,
        user["id"],
        verdict.action,
    )
    return json_response({"ok": True, "feedback_id": feedback_id})


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/feedback/anon — 桌面本地版匿名提交(无需登录;留邮箱收回执)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/feedback/anon")
async def submit_feedback_anon(request: Request):
    """桌面本地版(无登录)反馈接入服务器。可留联系邮箱;若邮箱与某登录账户重名,
    默认归并到该账户(用户诉求)。NSFW 预审 + 按 IP 限流防滥用。consent_token 可选
    (桌面安装已同意 AUP),提供则须 64-hex。"""
    body = await request.json()
    ip = _client_ip(request)
    ua = request.headers.get("user-agent", "")

    free_text: str = body.get("free_text", "") or ""
    excerpts = body.get("excerpts", []) or []
    contact_email: str = (body.get("contact_email", "") or "").strip()[:320]
    client_id: str = (body.get("client_id", "") or "").strip()[:128]
    app_version: str = (body.get("app_version", "") or "")[:64]
    _env = body.get("env_snapshot")
    env_snapshot = _env if isinstance(_env, dict) else None
    consent_token: str = body.get("consent_token", "") or ""

    if not free_text.strip():
        raise HTTPException(status_code=400, detail="反馈内容不能为空")
    if consent_token:
        if len(consent_token) != 64:
            raise HTTPException(status_code=400, detail="consent_token 格式不正确")
        try:
            int(consent_token, 16)
        except ValueError:
            raise HTTPException(status_code=400, detail="consent_token 不是合法 hex")
    if not isinstance(excerpts, list):
        raise HTTPException(status_code=400, detail="excerpts 须为数组")

    excerpts_raw = json.dumps(excerpts, ensure_ascii=False)
    env_raw = json.dumps(env_snapshot or {}, ensure_ascii=False)
    total_bytes = (
        len(free_text.encode("utf-8"))
        + len(excerpts_raw.encode("utf-8"))
        + len(env_raw.encode("utf-8"))
    )
    if total_bytes > _MAX_PAYLOAD_BYTES:
        raise HTTPException(status_code=400, detail=f"内容超过 50KB 上限(当前 {total_bytes} 字节)")

    # 按 IP 限流(无登录闸,这是主要防滥用手段)
    with connect() as db:
        recent = db.execute(
            "select count(*) as n from feedback where ip = %s and created_at > now() - interval '1 hour'",
            (ip,),
        ).fetchone()
        if recent and int(recent["n"]) >= _ANON_RATE_PER_HOUR:
            raise HTTPException(status_code=429, detail="提交过于频繁,请稍后再试")

    # NSFW 预审(与登录路径同一把关)
    verdict = await moderate_feedback(free_text + "\n" + excerpts_raw)

    with connect() as db:
        linked_user_id = _match_user_id_by_email(db, contact_email)

        if verdict.action == "auto_reject":
            _csam_summary = json.dumps(
                {"__moderation__": {"action": "auto_reject", "categories": verdict.categories}},
                ensure_ascii=False,
            )
            db.execute(
                """
                insert into feedback
                  (user_id, free_text, excerpts_jsonb, consent_token, ua, app_version, ip,
                   contact_email, client_id, env_snapshot, reviewed_at, review_decision)
                values (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s::jsonb, now(), 'nsfw_terminate')
                """,
                (linked_user_id, "[CSAM filter triggered — content not stored]", _csam_summary,
                 consent_token, ua, app_version, ip, contact_email or None, client_id or None, env_raw),
            )
            if linked_user_id is not None:
                from ..dmca import queue_account_termination
                queue_account_termination(
                    db, linked_user_id,
                    reason=f"anon feedback CSAM auto_reject: categories={verdict.categories}",
                )
            log.error("anon feedback auto_reject CSAM: ip=%s linked=%s cats=%s", ip, linked_user_id, verdict.categories)
            raise HTTPException(
                status_code=403,
                detail={"error_key": "feedback.nsfw_terminate", "message": "反馈内容违反 AUP §2.J 红线。"},
            )

        excerpts_with_verdict = list(excerpts)
        excerpts_with_verdict.append({
            "__moderation__": {
                "action": verdict.action,
                "categories": verdict.categories,
                "scores": {k: round(v, 4) for k, v in verdict.scores.items() if v > 0.001},
            },
            "__source__": "desktop_anon",
        })
        row = db.execute(
            """
            insert into feedback
              (user_id, free_text, excerpts_jsonb, consent_token, ua, app_version, ip,
               contact_email, client_id, env_snapshot)
            values (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s::jsonb)
            returning id
            """,
            (linked_user_id, free_text, json.dumps(excerpts_with_verdict, ensure_ascii=False),
             consent_token, ua, app_version, ip, contact_email or None, client_id or None, env_raw),
        ).fetchone()
        feedback_id = row["id"]
        if consent_token:
            db.execute(
                "insert into feedback_consent_log (user_id, consent_text_hash, app_version, ip, client_id) "
                "values (%s, %s, %s, %s, %s)",
                (linked_user_id, consent_token, app_version, ip, client_id or None),
            )

    log.info("anon feedback: id=%s linked_user=%s moderation=%s", feedback_id, linked_user_id, verdict.action)
    return json_response({"ok": True, "feedback_id": feedback_id, "linked": linked_user_id is not None})


# ──────────────────────────────────────────────────────────────────────────────
# GET /api/me/feedback — 用户查看自己的反馈
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/me/feedback")
async def list_my_feedback(
    limit: int = 20,
    user=Depends(require_user),
):
    """FB-07: 用户查看自己的历史反馈（含状态）。"""
    limit = max(1, min(100, limit))
    with connect() as db:
        rows = db.execute(
            """
            select id, free_text, review_decision, reviewed_at, created_at,
                   admin_reply, replied_at
            from feedback
            where user_id = %s
            order by created_at desc
            limit %s
            """,
            (user["id"], limit),
        ).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "free_text_preview": (r["free_text"] or "")[:100],
            "review_decision": r["review_decision"],
            "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            # 反馈回复: admin 的回信(对用户可见)
            "admin_reply": r["admin_reply"] or None,
            "replied_at": r["replied_at"].isoformat() if r["replied_at"] else None,
        })
    return json_response({"ok": True, "items": items})


# ──────────────────────────────────────────────────────────────────────────────
# GET /api/feedback/anon/replies — 自部署客户端按 client_id 拉自己的反馈 + 回执
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/feedback/anon/replies")
async def list_anon_feedback_replies(client_id: str = "", limit: int = 30):
    """自部署(无登录)客户端凭安装级 client_id 拉取自己提交过的反馈 + admin 回执,
    让回执在控制台内可见(不只发邮件)。client_id 是每安装一次性 UUID(config.js 生成),
    仅返回该安装自己的提交。"""
    client_id = (client_id or "").strip()[:128]
    if len(client_id) < 16:
        raise HTTPException(status_code=400, detail="client_id 缺失或过短")
    limit = max(1, min(100, limit))
    with connect() as db:
        rows = db.execute(
            """
            select id, free_text, review_decision, reviewed_at, created_at,
                   admin_reply, replied_at
            from feedback
            where client_id = %s
            order by created_at desc
            limit %s
            """,
            (client_id, limit),
        ).fetchall()
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "free_text_preview": (r["free_text"] or "")[:100],
            "review_decision": r["review_decision"],
            "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "admin_reply": r["admin_reply"] or None,
            "replied_at": r["replied_at"].isoformat() if r["replied_at"] else None,
        })
    return json_response({"ok": True, "items": items})


# ──────────────────────────────────────────────────────────────────────────────
# DELETE /api/feedback/{id} — 用户撤回单条
# ──────────────────────────────────────────────────────────────────────────────

@router.delete("/api/feedback/{feedback_id}")
async def delete_my_feedback(
    feedback_id: int,
    user=Depends(require_user),
):
    """FB-08: 用户撤回单条 unreviewed 反馈。
    已被 nsfw_terminate 标记的不允许删除（403）。
    consent_log 行保留。
    """
    with connect() as db:
        row = db.execute(
            "select user_id, review_decision from feedback where id = %s",
            (feedback_id,),
        ).fetchone()

        if not row:
            raise HTTPException(status_code=404, detail="反馈不存在")
        if row["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="无权操作此反馈")
        if row["review_decision"] == "nsfw_terminate":
            raise HTTPException(
                status_code=403,
                detail="该反馈已被标记为 nsfw_terminate，根据 AUP §2.J 不允许删除（合规证据保留）",
            )

        db.execute("delete from feedback where id = %s", (feedback_id,))

    return json_response({"ok": True})


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/me/feedback/delete-all — 用户撤销所有
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/me/feedback/delete-all")
async def delete_all_my_feedback(user=Depends(require_user)):
    """FB-08: 用户一键撤回所有未被 nsfw_terminate 标记的反馈。
    nsfw_terminate 行保留（合规证据）。consent_log 保留。
    """
    with connect() as db:
        cur = db.execute(
            """
            delete from feedback
            where user_id = %s
              and (review_decision is null or review_decision != 'nsfw_terminate')
            """,
            (user["id"],),
        )
        deleted = cur.rowcount

    return json_response({"ok": True, "deleted": deleted})


# ──────────────────────────────────────────────────────────────────────────────
# GET /api/admin/feedback — admin 审查队列 (FB-03)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/feedback")
async def admin_list_feedback(
    status: str = "unreviewed",
    limit: int = 50,
    admin=Depends(_require_admin),
):
    """FB-03: admin 查看反馈审查队列。status=unreviewed|reviewed|all"""
    limit = max(1, min(200, limit))
    with connect() as db:
        rows = db.execute(
            """
            select f.id, f.user_id, u.username,
                   f.free_text, f.excerpts_jsonb,
                   f.review_decision, f.reviewed_at,
                   f.admin_reply, f.replied_at,
                   f.app_version, f.created_at
            from feedback f
            left join users u on u.id = f.user_id
            where (
              %s = 'all'
              or (%s = 'unreviewed' and f.review_decision is null)
              or (%s = 'reviewed' and f.review_decision is not null)
            )
            order by f.created_at desc
            limit %s
            """,
            (status, status, status, limit),
        ).fetchall()

    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "user_id": r["user_id"],
            "username": r["username"] or "—",
            "free_text": r["free_text"] or "",
            "excerpts": r["excerpts_jsonb"] if r["excerpts_jsonb"] else [],
            "review_decision": r["review_decision"],
            "reviewed_at": r["reviewed_at"].isoformat() if r["reviewed_at"] else None,
            "admin_reply": r["admin_reply"] or None,
            "replied_at": r["replied_at"].isoformat() if r["replied_at"] else None,
            "app_version": r["app_version"] or "",
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        })
    return json_response({"ok": True, "items": items})


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/admin/feedback/{id}/decision — admin 审查决定 (FB-03)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/feedback/{feedback_id}/decision")
async def admin_feedback_decision(
    request: Request,
    feedback_id: int,
    admin=Depends(_require_admin),
):
    """FB-03: admin 标记反馈。
    decision=ok|nsfw_terminate|spam
    nsfw_terminate 时走现有 /api/admin/users/{id}/terminate 逻辑。
    """
    body = await request.json()
    ip = _client_ip(request)
    decision = body.get("decision", "")
    notes = body.get("notes", "") or ""

    if decision not in _VALID_DECISIONS:
        raise HTTPException(
            status_code=400,
            detail=f"decision 须为 {' | '.join(_VALID_DECISIONS)}",
        )

    with connect() as db:
        row = db.execute(
            "select id, user_id from feedback where id = %s",
            (feedback_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="反馈不存在")

        db.execute(
            """
            update feedback
            set review_decision = %s, reviewed_at = now()
            where id = %s
            """,
            (decision, feedback_id),
        )

        # nsfw_terminate: 调现有 queue_account_termination
        if decision == "nsfw_terminate":
            from ..dmca import queue_account_termination
            terminate_reason = f"反馈审查 nsfw_terminate (feedback_id={feedback_id}): {notes}"
            queue_account_termination(db, row["user_id"], terminate_reason)
            log.warning(
                "feedback nsfw_terminate: feedback_id=%s user_id=%s admin=%s",
                feedback_id, row["user_id"], admin.get("username"),
            )

    return json_response({"ok": True, "decision": decision})


# ──────────────────────────────────────────────────────────────────────────────
# POST /api/admin/feedback/{id}/reply — admin 给用户的回复 (对用户可见)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/feedback/{feedback_id}/reply")
async def admin_feedback_reply(
    request: Request,
    feedback_id: int,
    admin=Depends(_require_admin),
):
    """给反馈写一条对用户可见的回复,展示在用户的「我的反馈历史」。
    reply 为空 = 撤回回复(置空)。审核决定(decision)与回复互不影响,可分别操作。"""
    body = await request.json()
    reply = (body.get("reply") or "").strip()
    emailed = False
    with connect() as db:
        row = db.execute(
            """
            select f.id, f.free_text, f.contact_email, f.user_id,
                   u.email as user_email, u.email_verified
            from feedback f left join users u on u.id = f.user_id
            where f.id = %s
            """,
            (feedback_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="反馈不存在")
        if reply:
            db.execute(
                "update feedback set admin_reply = %s, replied_at = now() where id = %s",
                (reply, feedback_id),
            )
        else:
            db.execute(
                "update feedback set admin_reply = null, replied_at = null where id = %s",
                (feedback_id,),
            )

        # 邮件回执:优先反馈时留的联系邮箱,否则用账户已验证邮箱。失败不阻断(同 auth.py 模式)。
        if reply:
            to_email = (row["contact_email"] or "").strip()
            if not to_email and row["user_email"] and row["email_verified"]:
                to_email = (row["user_email"] or "").strip()
            if to_email:
                try:
                    from .. import email as email_mod
                    email_mod.send_feedback_reply_email(to_email, reply, row["free_text"] or "")
                    db.execute("update feedback set reply_emailed_at = now() where id = %s", (feedback_id,))
                    emailed = True
                except Exception as exc:
                    log.warning("feedback reply email failed: id=%s err=%s", feedback_id, exc)
    return json_response({"ok": True, "reply": reply, "emailed": emailed})
