"""platform_app.api.admin — /api/admin/* 路由（需 admin 角色）。"""
from __future__ import annotations

import logging
import os
import secrets
import signal
import string
import sys
import time
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb

from platform_app.api._deps import _client_ip, json_response, require_user
from platform_app.db import connect
from platform_app.dmca import increment_strike, queue_account_termination

router = APIRouter()
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 共享依赖与辅助
# ──────────────────────────────────────────────────────────────────────────────

def _require_admin(user=Depends(require_user)):
    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


_REGISTRATION_CFG_KEY = "admin.registration_config"
_SECURITY_CFG_KEY = "admin.security_config"
_MAINTENANCE_CFG_KEY = "admin.maintenance_config"


def _get_app_config(db, key: str) -> dict:
    row = db.execute("select value from app_config where key = %s", (key,)).fetchone()
    if row and row.get("value"):
        v = row["value"]
        return v if isinstance(v, dict) else {}
    return {}


def _set_app_config(db, key: str, data: dict):
    existing = _get_app_config(db, key)
    merged = {**existing, **data}
    db.execute(
        """insert into app_config(key, value) values(%s, %s)
           on conflict(key) do update set value = excluded.value, updated_at = now()""",
        (key, Jsonb(merged)),
    )


def _write_audit(
    db,
    actor: dict,
    action: str,
    target_type: str = "",
    target_id: str = "",
    details: dict = None,
    ip: str = "",
):
    db.execute(
        """insert into admin_audit_log(actor_id, actor_username, action, target_type, target_id, details, ip)
           values(%s, %s, %s, %s, %s, %s, %s)""",
        (
            actor.get("id"),
            actor.get("username", ""),
            action,
            target_type,
            str(target_id),
            Jsonb(details or {}),
            ip,
        ),
    )


# ──────────────────────────────────────────────────────────────────────────────
# 2.2 用户管理
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/users")
async def admin_list_users(
    request: Request,
    page: int = 1,
    limit: int = 20,
    search: str = "",
    role: str = "all",
    status: str = "all",
    admin=Depends(_require_admin),
):
    page = max(1, page)
    limit = max(1, min(100, limit))
    offset = (page - 1) * limit
    search_pat = f"%{search}%" if search else ""

    with connect() as db:
        count_row = db.execute(
            """
            select count(*) as total from users u
            where (%s = '' or u.username ilike %s or u.display_name ilike %s)
              and (%s = 'all' or u.role = %s)
              and (%s = 'all'
                   or (%s = 'active' and u.deactivated_at is null)
                   or (%s = 'deactivated' and u.deactivated_at is not null))
            """,
            (search_pat, search_pat, search_pat, role, role, status, status, status),
        ).fetchone()
        total = count_row["total"] if count_row else 0

        rows = db.execute(
            """
            select
              u.id, u.username, u.display_name, u.role, u.bio,
              u.created_at, u.deactivated_at,
              coalesce(u.ban_reason, '') as ban_reason,
              (select la.created_at from login_audit la
               where la.username = u.username and la.event = 'login_ok'
               order by la.created_at desc limit 1) as last_login_at,
              (select count(*) from sessions s
               where s.user_id = u.id and s.expires_at > now()) as session_count,
              coalesce((select sum(tu.total_tokens) from token_usage tu
               where tu.user_id = u.id
                 and tu.created_at > now() - interval '30 days'), 0) as usage_tokens_30d
            from users u
            where (%s = '' or u.username ilike %s or u.display_name ilike %s)
              and (%s = 'all' or u.role = %s)
              and (%s = 'all'
                   or (%s = 'active' and u.deactivated_at is null)
                   or (%s = 'deactivated' and u.deactivated_at is not null))
            order by u.created_at desc
            limit %s offset %s
            """,
            (
                search_pat, search_pat, search_pat,
                role, role,
                status, status, status,
                limit, offset,
            ),
        ).fetchall()

    return json_response({
        "users": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
    })


@router.patch("/api/admin/users/{user_id}")
async def admin_update_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)

    new_role = body.get("role")
    ban_reason = body.get("ban_reason")
    display_name = body.get("display_name")

    # 禁止管理员降级自己
    if new_role == "user" and admin.get("id") == user_id:
        raise HTTPException(status_code=400, detail="不允许将自己降级为普通用户")

    with connect() as db:
        if new_role is not None:
            # task: vip_user 享受平台 RAG embedder 兜底,但仍 LLM BYOK,跟 admin 区别
            # 是 vip_user 没 admin 权限(不能改其他用户、看 audit log 等)
            if new_role not in ("user", "vip_user", "admin"):
                raise HTTPException(status_code=400, detail="role 只能是 user / vip_user / admin")
            db.execute("update users set role = %s where id = %s", (new_role, user_id))
            _write_audit(db, admin, "user.update_role",
                         target_type="user", target_id=str(user_id),
                         details={"role": new_role}, ip=ip)

        updates = []
        params = []
        if ban_reason is not None:
            updates.append("ban_reason = %s")
            params.append(ban_reason)
        if display_name is not None:
            updates.append("display_name = %s")
            params.append(display_name)

        if updates:
            params.append(user_id)
            db.execute(
                f"update users set {', '.join(updates)} where id = %s",
                params,
            )
            _write_audit(db, admin, "user.update_info",
                         target_type="user", target_id=str(user_id),
                         details={k: v for k, v in body.items() if k != "role"}, ip=ip)

    return json_response({"ok": True})


@router.post("/api/admin/users/{user_id}/deactivate")
async def admin_deactivate_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    # 禁止管理员停用自己:deactivated_at 让自己的 token 立即失效 + 删自己的 session →
    # 自锁在门外且无法撤销(已停用进不了 admin)。与 update_role 的自降级保护一致。
    if admin.get("id") == user_id:
        raise HTTPException(status_code=400, detail="不允许停用自己的账户")
    with connect() as db:
        db.execute(
            "update users set deactivated_at = now() where id = %s",
            (user_id,),
        )
        result = db.execute(
            "delete from sessions where user_id = %s returning token",
            (user_id,),
        ).fetchall()
        sessions_revoked = len(result)
        _write_audit(db, admin, "user.deactivate",
                     target_type="user", target_id=str(user_id),
                     details={"sessions_revoked": sessions_revoked}, ip=ip)

    return json_response({"ok": True, "sessions_revoked": sessions_revoked})


@router.post("/api/admin/users/{user_id}/reactivate")
async def admin_reactivate_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    with connect() as db:
        db.execute(
            "update users set deactivated_at = null, ban_reason = '' where id = %s",
            (user_id,),
        )
        _write_audit(db, admin, "user.reactivate",
                     target_type="user", target_id=str(user_id),
                     details={}, ip=ip)

    return json_response({"ok": True})


@router.post("/api/admin/users/{user_id}/force-logout")
async def admin_force_logout_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    with connect() as db:
        result = db.execute(
            "delete from sessions where user_id = %s returning token",
            (user_id,),
        ).fetchall()
        sessions_revoked = len(result)
        _write_audit(db, admin, "user.force_logout",
                     target_type="user", target_id=str(user_id),
                     details={"sessions_revoked": sessions_revoked}, ip=ip)

    return json_response({"ok": True, "sessions_revoked": sessions_revoked})


# ──────────────────────────────────────────────────────────────────────────────
# 2.3 全局用量
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/usage")
async def admin_usage(
    days: int = 30,
    admin=Depends(_require_admin),
):
    days = max(1, min(365, days))

    with connect() as db:
        total_row = db.execute(
            """
            select
              coalesce(sum(input_tokens), 0) as input_tokens,
              coalesce(sum(output_tokens), 0) as output_tokens,
              coalesce(sum(total_tokens), 0) as total_tokens,
              coalesce(sum(cost_usd), 0) as cost_usd,
              count(*) as requests
            from token_usage
            where created_at > now() - (%s || ' days')::interval
            """,
            (str(days),),
        ).fetchone()

        by_user = db.execute(
            """
            select
              tu.user_id,
              u.username,
              u.display_name,
              coalesce(sum(tu.input_tokens), 0) as input_tokens,
              coalesce(sum(tu.output_tokens), 0) as output_tokens,
              coalesce(sum(tu.total_tokens), 0) as total_tokens,
              coalesce(sum(tu.cost_usd), 0) as cost_usd,
              count(*) as requests
            from token_usage tu
            join users u on u.id = tu.user_id
            where tu.created_at > now() - (%s || ' days')::interval
            group by tu.user_id, u.username, u.display_name
            order by cost_usd desc
            limit 20
            """,
            (str(days),),
        ).fetchall()

        by_api = db.execute(
            """
            select
              api_id,
              coalesce(sum(input_tokens), 0) as input_tokens,
              coalesce(sum(output_tokens), 0) as output_tokens,
              coalesce(sum(total_tokens), 0) as total_tokens,
              coalesce(sum(cost_usd), 0) as cost_usd,
              count(*) as requests
            from token_usage
            where created_at > now() - (%s || ' days')::interval
            group by api_id
            order by cost_usd desc
            """,
            (str(days),),
        ).fetchall()

        by_day = db.execute(
            """
            select
              date_trunc('day', created_at)::date as date,
              coalesce(sum(total_tokens), 0) as total_tokens,
              coalesce(sum(cost_usd), 0) as cost_usd,
              count(*) as requests
            from token_usage
            where created_at > now() - (%s || ' days')::interval
            group by 1
            order by 1 asc
            """,
            (str(days),),
        ).fetchall()

    return json_response({
        "total": dict(total_row) if total_row else {
            "input_tokens": 0, "output_tokens": 0,
            "total_tokens": 0, "cost_usd": 0, "requests": 0,
        },
        "by_user": [dict(r) for r in by_user],
        "by_api": [dict(r) for r in by_api],
        "by_day": [dict(r) for r in by_day],
    })


# ──────────────────────────────────────────────────────────────────────────────
# 2.4 审计日志
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/audit")
async def admin_audit_log(
    page: int = 1,
    limit: int = 50,
    action_type: str = "",
    admin=Depends(_require_admin),
):
    page = max(1, page)
    limit = max(1, min(200, limit))
    offset = (page - 1) * limit

    with connect() as db:
        count_row = db.execute(
            """
            select count(*) as total from admin_audit_log
            where (%s = '' or action like %s)
            """,
            (action_type, f"{action_type}%"),
        ).fetchone()
        total = count_row["total"] if count_row else 0

        rows = db.execute(
            """
            select id, actor_username, action, target_type, target_id, details, ip, created_at
            from admin_audit_log
            where (%s = '' or action like %s)
            order by created_at desc
            limit %s offset %s
            """,
            (action_type, f"{action_type}%", limit, offset),
        ).fetchall()

    return json_response({
        "entries": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "limit": limit,
    })


# ──────────────────────────────────────────────────────────────────────────────
# 2.5 系统健康
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/health")
async def admin_health(admin=Depends(_require_admin)):
    # DB latency
    db_ok = False
    db_latency_ms = 0.0
    db_pool_size = 0
    db_pool_idle = 0
    try:
        t0 = time.perf_counter()
        with connect() as db:
            db.execute("select 1").fetchone()
        db_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        db_ok = True

        # try to get pool stats
        try:
            from platform_app.db import get_pool
            pool = get_pool()
            if pool is not None:
                db_pool_size = pool.get_stats().get("pool_size", 0)
                db_pool_idle = pool.get_stats().get("pool_available", 0)
        except Exception:
            pass
    except Exception as exc:
        log.warning("health check db error: %s", exc)

    # process info
    pid = os.getpid()
    memory_rss_mb = None
    uptime_s = None

    try:
        import psutil
        proc = psutil.Process(pid)
        memory_rss_mb = round(proc.memory_info().rss / (1024 * 1024), 2)
        uptime_s = round(time.time() - proc.create_time(), 1)
    except ImportError:
        pass
    except Exception:
        pass

    # disk
    disk_free_gb = 0.0
    disk_total_gb = 0.0
    disk_percent_used = 0.0
    try:
        st = os.statvfs("/")
        disk_total_gb = round(st.f_frsize * st.f_blocks / (1024 ** 3), 2)
        disk_free_gb = round(st.f_frsize * st.f_bavail / (1024 ** 3), 2)
        disk_percent_used = round((1 - st.f_bavail / st.f_blocks) * 100, 1) if st.f_blocks else 0.0
    except Exception:
        pass

    overall_ok = db_ok

    return json_response({
        "db": {
            "ok": db_ok,
            "latency_ms": db_latency_ms,
            "pool_size": db_pool_size,
            "pool_idle": db_pool_idle,
        },
        "process": {
            "pid": pid,
            "uptime_s": uptime_s,
            "memory_rss_mb": memory_rss_mb,
        },
        "disk": {
            "free_gb": disk_free_gb,
            "total_gb": disk_total_gb,
            "percent_used": disk_percent_used,
        },
        "python_version": sys.version,
        "ok": overall_ok,
    })


# ──────────────────────────────────────────────────────────────────────────────
# 2.6 日志
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/logs")
async def admin_logs(
    lines: int = 100,
    level: str = "",
    admin=Depends(_require_admin),
):
    lines = max(1, min(500, lines))
    log_file = os.environ.get("LOG_FILE", "")

    if log_file and os.path.isfile(log_file):
        try:
            with open(log_file, encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            # read last lines*3 to filter, then take last `lines`
            tail = all_lines[-(lines * 3):]
            if level:
                level_up = level.upper()
                tail = [line for line in tail if level_up in line]
            tail = tail[-lines:]
            return json_response({
                "lines": [line.rstrip("\n") for line in tail],
                "total_lines": len(tail),
                "source": "file",
            })
        except Exception as exc:
            log.warning("admin_logs read error: %s", exc)

    return json_response({
        "lines": ["（日志文件路径未配置）"],
        "total_lines": 1,
        "source": "none",
    })


# ──────────────────────────────────────────────────────────────────────────────
# 2.7 注册与邀请
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_REGISTRATION = {
    "mode": "open",
    "require_email_verify": False,
    "auto_approve": True,
}


@router.get("/api/admin/registration")
async def admin_get_registration(admin=Depends(_require_admin)):
    with connect() as db:
        cfg = _get_app_config(db, _REGISTRATION_CFG_KEY)
    merged = {**_DEFAULT_REGISTRATION, **cfg}
    return json_response(merged)


@router.post("/api/admin/registration")
async def admin_set_registration(
    request: Request,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)

    allowed_keys = {"mode", "require_email_verify", "auto_approve"}
    update = {k: v for k, v in body.items() if k in allowed_keys}

    with connect() as db:
        _set_app_config(db, _REGISTRATION_CFG_KEY, update)
        _write_audit(db, admin, "config.registration",
                     details=update, ip=ip)

    return json_response({"ok": True})


@router.get("/api/admin/invite-codes")
async def admin_list_invite_codes(
    page: int = 1,
    limit: int = 50,
    used: str = "all",
    admin=Depends(_require_admin),
):
    page = max(1, page)
    limit = max(1, min(200, limit))
    offset = (page - 1) * limit

    with connect() as db:
        count_row = db.execute(
            """
            select count(*) as total from invite_codes
            where (%s = 'all'
                   or (%s = 'used' and used_by is not null)
                   or (%s = 'unused' and used_by is null))
            """,
            (used, used, used),
        ).fetchone()
        total = count_row["total"] if count_row else 0

        rows = db.execute(
            """
            select ic.id, ic.code, ic.note, ic.expires_at, ic.used_at, ic.created_at,
                   u.username as used_by_username
            from invite_codes ic
            left join users u on u.id = ic.used_by
            where (%s = 'all'
                   or (%s = 'used' and ic.used_by is not null)
                   or (%s = 'unused' and ic.used_by is null))
            order by ic.created_at desc
            limit %s offset %s
            """,
            (used, used, used, limit, offset),
        ).fetchall()

    return json_response({
        "codes": [dict(r) for r in rows],
        "total": total,
    })


@router.post("/api/admin/invite-codes")
async def admin_create_invite_codes(
    request: Request,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)

    count = max(1, min(20, int(body.get("count", 1))))
    expires_in_days = body.get("expires_in_days")
    note = body.get("note", "")

    alphabet = string.ascii_uppercase + string.digits
    created = []

    with connect() as db:
        for _ in range(count):
            code = "".join(secrets.choice(alphabet) for _ in range(8))
            if expires_in_days is not None:
                db.execute(
                    """
                    insert into invite_codes(code, created_by, expires_at, note)
                    values(%s, %s, now() + (%s || ' days')::interval, %s)
                    returning code, expires_at, created_at
                    """,
                    (code, admin.get("id"), str(int(expires_in_days)), note),
                )
            else:
                db.execute(
                    """
                    insert into invite_codes(code, created_by, note)
                    values(%s, %s, %s)
                    returning code, expires_at, created_at
                    """,
                    (code, admin.get("id"), note),
                )
            row = db.execute(
                "select code, expires_at, created_at from invite_codes where code = %s",
                (code,),
            ).fetchone()
            if row:
                created.append(dict(row))

        _write_audit(db, admin, "invite.create",
                     details={"count": count, "expires_in_days": expires_in_days, "note": note},
                     ip=ip)

    return json_response({"codes": created})


@router.post("/api/admin/invite-codes/{code}/delete")
async def admin_delete_invite_code(
    request: Request,
    code: str,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    with connect() as db:
        result = db.execute(
            "delete from invite_codes where code = %s and used_by is null returning id",
            (code,),
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="邀请码不存在或已被使用")
        _write_audit(db, admin, "invite.delete",
                     target_type="invite_code", target_id=code,
                     details={}, ip=ip)

    return json_response({"ok": True})


# ──────────────────────────────────────────────────────────────────────────────
# 2.8 安全配置
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_SECURITY = {
    "ip_blocklist": [],
    "rate_limit_per_ip": 30,
    "rate_limit_per_user": 10,
    "rate_window_minutes": 10,
    "password_min_length": 6,
    "password_require_numbers": False,
    "session_timeout_days": 14,
    "login_lock_threshold": 10,
    "login_lock_duration_min": 30,
}


@router.get("/api/admin/security-config")
async def admin_get_security_config(admin=Depends(_require_admin)):
    with connect() as db:
        cfg = _get_app_config(db, _SECURITY_CFG_KEY)
    merged = {**_DEFAULT_SECURITY, **cfg}
    return json_response(merged)


@router.post("/api/admin/security-config")
async def admin_set_security_config(
    request: Request,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)

    allowed_keys = set(_DEFAULT_SECURITY.keys())
    update = {k: v for k, v in body.items() if k in allowed_keys}

    with connect() as db:
        _set_app_config(db, _SECURITY_CFG_KEY, update)
        _write_audit(db, admin, "config.security", details=update, ip=ip)

    return json_response({"ok": True})


# ──────────────────────────────────────────────────────────────────────────────
# 2.9 维护模式
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_MAINTENANCE = {
    "maintenance_mode": False,
    "announcement": "",
    "maintenance_since": None,
}


@router.get("/api/admin/maintenance")
async def admin_get_maintenance(admin=Depends(_require_admin)):
    with connect() as db:
        cfg = _get_app_config(db, _MAINTENANCE_CFG_KEY)
    merged = {**_DEFAULT_MAINTENANCE, **cfg}
    return json_response(merged)


@router.post("/api/admin/maintenance")
async def admin_set_maintenance(
    request: Request,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)

    update: dict = {}
    if "maintenance_mode" in body:
        update["maintenance_mode"] = bool(body["maintenance_mode"])
        if update["maintenance_mode"]:
            update["maintenance_since"] = datetime.now(UTC).isoformat()
        else:
            update["maintenance_since"] = None
    if "announcement" in body:
        update["announcement"] = str(body["announcement"])

    with connect() as db:
        _set_app_config(db, _MAINTENANCE_CFG_KEY, update)
        _write_audit(db, admin, "maintenance.toggle", details=update, ip=ip)

    return json_response({"ok": True})


# ──────────────────────────────────────────────────────────────────────────────
# 2.10 服务重启
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/restart")
async def admin_restart(
    request: Request,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    with connect() as db:
        _write_audit(db, admin, "system.restart", details={}, ip=ip)

    os.kill(os.getpid(), signal.SIGHUP)
    return json_response({
        "ok": True,
        "message": "重启信号已发送，服务将在当前请求完成后重载",
    })


# ──────────────────────────────────────────────────────────────────────────────
# 3.1 DMCA 下架队列
# 依赖表 dmca_takedowns（v37 迁移创建）
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/dmca/takedowns")
async def admin_dmca_list(
    status: str = "open",
    limit: int = 50,
    admin=Depends(_require_admin),
):
    limit = max(1, min(200, limit))
    with connect() as db:
        rows = db.execute(
            """
            select id, complainant_name, complainant_email, infringing_url,
                   original_work_desc, status, notes,
                   counter_received_at, restore_after,
                   created_at, actioned_at, actioned_by
            from dmca_takedowns
            where (%s = 'all' or status = %s)
            order by created_at desc
            limit %s
            """,
            (status, status, limit),
        ).fetchall()
    return json_response({"takedowns": [dict(r) for r in rows]})


@router.post("/api/admin/dmca/takedowns")
async def admin_dmca_create(
    request: Request,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)

    required = ("complainant_name", "complainant_email", "infringing_url")
    for f in required:
        if not body.get(f):
            raise HTTPException(status_code=400, detail=f"缺少必填字段: {f}")

    with connect() as db:
        row = db.execute(
            """
            insert into dmca_takedowns
              (complainant_name, complainant_email, infringing_url,
               original_work_desc, status, created_by, created_at)
            values (%s, %s, %s, %s, 'open', %s, now())
            returning id
            """,
            (
                body["complainant_name"],
                body["complainant_email"],
                body["infringing_url"],
                body.get("original_work_desc", ""),
                admin.get("id"),
            ),
        ).fetchone()
        takedown_id = row["id"] if row else None
        _write_audit(db, admin, "dmca.takedown.create",
                     target_type="dmca_takedown", target_id=str(takedown_id),
                     details=body, ip=ip)

    return json_response({"ok": True, "id": takedown_id})


@router.post("/api/admin/dmca/takedowns/{takedown_id}/action")
async def admin_dmca_action(
    request: Request,
    takedown_id: int,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)
    action = body.get("action")
    reason = body.get("reason", "")

    if action not in ("takedown", "restore", "reject"):
        raise HTTPException(status_code=400, detail="action 须为 takedown|restore|reject")

    status_map = {"takedown": "closed", "restore": "restored", "reject": "rejected"}
    new_status = status_map[action]

    with connect() as db:
        result = db.execute(
            """
            update dmca_takedowns
            set status = %s, notes = coalesce(notes,'') || %s,
                actioned_at = now(), actioned_by = %s
            where id = %s
            returning id
            """,
            (new_status, f"\n[{action}] {reason}", admin.get("id"), takedown_id),
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="下架记录不存在")
        _write_audit(db, admin, f"dmca.takedown.{action}",
                     target_type="dmca_takedown", target_id=str(takedown_id),
                     details={"reason": reason, "new_status": new_status}, ip=ip)

    return json_response({"ok": True, "status": new_status})


@router.post("/api/admin/dmca/takedowns/{takedown_id}/counter")
async def admin_dmca_counter(
    request: Request,
    takedown_id: int,
    admin=Depends(_require_admin),
):
    """录入反通知，自动计算 10 天后可恢复时间。"""
    body = await request.json()
    ip = _client_ip(request)

    with connect() as db:
        result = db.execute(
            """
            update dmca_takedowns
            set counter_received_at = now(),
                restore_after = now() + interval '10 days',
                notes = coalesce(notes,'') || %s,
                status = 'counter_received'
            where id = %s
            returning id
            """,
            (f"\n[counter-notice] {body.get('notes', '')}", takedown_id),
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="下架记录不存在")
        _write_audit(db, admin, "dmca.takedown.counter_notice",
                     target_type="dmca_takedown", target_id=str(takedown_id),
                     details=body, ip=ip)

    return json_response({"ok": True, "restore_after": "now() + 10 days"})


# ──────────────────────────────────────────────────────────────────────────────
# 3.2 DMCA Strike 管理
# 依赖表 dmca_strikes（v37 迁移创建）
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/dmca/strikes")
async def admin_dmca_strikes_list(
    admin=Depends(_require_admin),
):
    with connect() as db:
        # dmca_strikes 是「每用户一行」的聚合表(strike_count + 最近一次信息),不是逐条日志。
        # 原查询按逐条日志取 ds.id/ds.reason/ds.created_at → 列不存在 500。改读聚合列。
        rows = db.execute(
            """
            select ds.user_id, u.username, ds.strike_count,
                   ds.last_strike_at, ds.last_strike_reason, ds.terminated_at
            from dmca_strikes ds
            join users u on u.id = ds.user_id
            where ds.strike_count > 0
            order by ds.last_strike_at desc nulls last
            limit 200
            """,
        ).fetchall()

    def _iso(v):
        try:
            return v.isoformat()
        except Exception:
            return None

    users = [{
        "user_id": r["user_id"],
        "username": r["username"],
        "strike_count": r["strike_count"],
        "last_strike_at": _iso(r.get("last_strike_at")),
        "last_strike_reason": r.get("last_strike_reason") or "",
        "terminated_at": _iso(r.get("terminated_at")),
        # 聚合表无逐条历史;给出最近一次作为单条,供前端展示。
        "strikes": ([{"reason": r.get("last_strike_reason") or "", "created_at": _iso(r.get("last_strike_at"))}]
                    if r["strike_count"] else []),
    } for r in rows]
    return json_response({"users": users})


@router.post("/api/admin/dmca/strikes/{user_id}/increment")
async def admin_dmca_strike_increment(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)
    reason = body.get("reason", "")
    if not reason:
        raise HTTPException(status_code=400, detail="reason 不能为空")

    with connect() as db:
        result = increment_strike(db, user_id, reason)

        _write_audit(db, admin, "dmca.strike.increment",
                     target_type="user", target_id=str(user_id),
                     details={"reason": reason, "strike_count": result["strike_count"]}, ip=ip)

        if result["terminate"]:
            terminate_reason = (
                f"DMCA 累犯 {result['strike_count']} 次，已达终止阈值。最近原因: {reason}"
            )
            queue_account_termination(db, user_id, terminate_reason)
            _write_audit(db, admin, "dmca.auto_terminate",
                         target_type="user", target_id=str(user_id),
                         details={"strike_count": result["strike_count"], "reason": terminate_reason},
                         ip=ip)

    return json_response(result)


# ──────────────────────────────────────────────────────────────────────────────
# 3.3 CSAM 举报管理
# 依赖表 csam_reports（v37 迁移创建）
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/csam/reports")
async def admin_csam_list(
    status: str = "pending",
    limit: int = 50,
    admin=Depends(_require_admin),
):
    limit = max(1, min(200, limit))
    with connect() as db:
        rows = db.execute(
            """
            select r.id, r.reporter_id, r.reported_user_id,
                   r.content_url, r.description, r.status,
                   r.decision, r.decision_notes, r.cybertip_report_id,
                   r.created_at, r.decided_at, r.decided_by,
                   u.username as reported_username
            from csam_reports r
            left join users u on u.id = r.reported_user_id
            where (%s = 'all' or r.status = %s)
            order by r.created_at desc
            limit %s
            """,
            (status, status, limit),
        ).fetchall()
    return json_response({"reports": [dict(r) for r in rows]})


@router.post("/api/admin/csam/reports/{report_id}/decision")
async def admin_csam_decision(
    request: Request,
    report_id: int,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)
    decision = body.get("decision")
    notes = body.get("notes", "")

    if decision not in ("founded", "unfounded", "escalate"):
        raise HTTPException(status_code=400, detail="decision 须为 founded|unfounded|escalate")

    with connect() as db:
        result = db.execute(
            """
            update csam_reports
            set decision = %s, decision_notes = %s, status = %s,
                decided_at = now(), decided_by = %s
            where id = %s
            returning id, reported_user_id
            """,
            (decision, notes, "decided", admin.get("id"), report_id),
        ).fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="举报记录不存在")

        _write_audit(db, admin, f"csam.decision.{decision}",
                     target_type="csam_report", target_id=str(report_id),
                     details={"decision": decision, "notes": notes}, ip=ip)

    return json_response({"ok": True, "decision": decision})


# ──────────────────────────────────────────────────────────────────────────────
# 3.4 AUP 账户暂停 / 解封 / 终止
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/users/{user_id}/suspend")
async def admin_suspend_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    body = await request.json()
    ip = _client_ip(request)
    reason = body.get("reason", "")
    duration_days = body.get("duration_days")  # None = 无限期

    if not reason:
        raise HTTPException(status_code=400, detail="reason 不能为空")

    if duration_days is not None:
        try:
            duration_days = int(duration_days)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="duration_days 须为整数") from None

    with connect() as db:
        if duration_days is not None:
            db.execute(
                """
                update users
                set deactivated_at = now(),
                    ban_reason = %s,
                    suspended_until = now() + (%s || ' days')::interval
                where id = %s
                """,
                (reason, str(duration_days), user_id),
            )
        else:
            db.execute(
                "update users set deactivated_at = now(), ban_reason = %s where id = %s",
                (reason, user_id),
            )
        # 撤销所有活跃 Session
        result = db.execute(
            "delete from sessions where user_id = %s returning token",
            (user_id,),
        ).fetchall()
        sessions_revoked = len(result)
        _write_audit(db, admin, "aup.suspend",
                     target_type="user", target_id=str(user_id),
                     details={"reason": reason, "duration_days": duration_days,
                               "sessions_revoked": sessions_revoked}, ip=ip)

    return json_response({"ok": True, "sessions_revoked": sessions_revoked})


@router.post("/api/admin/users/{user_id}/unsuspend")
async def admin_unsuspend_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    ip = _client_ip(request)
    with connect() as db:
        db.execute(
            """
            update users
            set deactivated_at = null, ban_reason = '', suspended_until = null
            where id = %s
            """,
            (user_id,),
        )
        _write_audit(db, admin, "aup.unsuspend",
                     target_type="user", target_id=str(user_id),
                     details={}, ip=ip)

    return json_response({"ok": True})


@router.post("/api/admin/users/{user_id}/terminate")
async def admin_terminate_user(
    request: Request,
    user_id: int,
    admin=Depends(_require_admin),
):
    """永久终止账户：写 banned_users + account_delete_queue，撤销所有 Session。"""
    body = await request.json()
    ip = _client_ip(request)
    reason = body.get("reason", "")

    if not reason:
        raise HTTPException(status_code=400, detail="reason 不能为空")

    # 管理员不能终止自己
    if admin.get("id") == user_id:
        raise HTTPException(status_code=400, detail="不允许终止自己的账户")

    with connect() as db:
        queue_account_termination(db, user_id, reason)
        _write_audit(db, admin, "aup.terminate",
                     target_type="user", target_id=str(user_id),
                     details={"reason": reason}, ip=ip)


# ──────────────────────────────────────────────────────────────────────────────
# 2.x 注册白名单批量导入
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/api/admin/allowlist/bulk")
async def api_bulk_allowlist(request: Request, admin=Depends(_require_admin)):
    """批量 upsert registration_allowlist。
    body: {entries: [{email, magic_token, batch?}, ...]}
    用于 landing 邮件批次：生成 magic_token 后由运营写入白名单。
    """
    body = await request.json()
    entries = body.get("entries") or []
    inserted = 0
    with connect() as db:
        for e in entries:
            email = (e.get("email") or "").strip().lower()
            token = (e.get("magic_token") or "").strip()
            if not email or not token:
                continue
            db.execute(
                """
                insert into registration_allowlist (email_norm, magic_token, batch, source)
                values (%s, %s, %s, 'landing-batch')
                on conflict (email_norm) do update set
                  magic_token = excluded.magic_token,
                  batch = excluded.batch
                """,
                (email, token, int(e.get("batch") or 1)),
            )
            inserted += 1
    return json_response({"ok": True, "inserted": inserted})


@router.get("/api/admin/allowlist")
async def api_list_allowlist(
    limit: int = 100,
    batch: int = 0,
    admin=Depends(_require_admin),
):
    """列出白名单条目（管理员用）。"""
    limit = max(1, min(500, limit))
    with connect() as db:
        if batch:
            rows = db.execute(
                "select email_norm, magic_token, batch, source, created_at, used_by_user_id, used_at "
                "from registration_allowlist where batch = %s order by created_at desc limit %s",
                (batch, limit),
            ).fetchall()
        else:
            rows = db.execute(
                "select email_norm, magic_token, batch, source, created_at, used_by_user_id, used_at "
                "from registration_allowlist order by created_at desc limit %s",
                (limit,),
            ).fetchall()
    return json_response({
        "ok": True,
        "entries": [dict(r) for r in rows],
        "count": len(rows),
    })


# ──────────────────────────────────────────────────────────────────────────────
# Beta Co-builders 名单
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/api/admin/co-builders")
async def admin_co_builders(admin=Depends(_require_admin)):
    """列出「测试期共建者」名单：通过 magic link 注册且已完成账户设置的用户。

    只含 username != email 的用户（magic link 建账号时初值 username = email；
    用户补填昵称后 username 才与 email 不同，视作「已完成注册」）。
    opted_out=True 的条目仍包含在内，便于管理员审核，但发布时应过滤掉。
    """
    with connect() as db:
        rows = db.execute(
            """
            select u.id as user_id, u.username, u.display_name, u.email,
                   u.created_at as registered_at, u.co_builder_opt_out as opted_out
            from users u
            join registration_allowlist a on a.used_by_user_id = u.id
            where u.username is not null and u.username != u.email
            order by u.created_at
            """,
        ).fetchall()
    entries = [dict(r) for r in rows]
    return json_response({
        "ok": True,
        "count": len(entries),
        "entries": entries,
    })


@router.post("/api/internal/allowlist/bulk")
async def api_internal_allowlist_bulk(request: Request):
    """task: 跨服务推送 — landing-deploy/backend/send_invites.py 用此 endpoint
    把 batch=N 的 (email, magic_token) 批量写入 RPG registration_allowlist。

    认证:Header X-Internal-Secret 必须匹配 env RPG_ALLOWLIST_SHARED_SECRET。
    无 session/cookie 要求,免 require_admin(因为是 ECS02 ↔ ECS06 跨服务调用)。

    Body: {"entries": [{"email": "x@x", "magic_token": "AbC...", "batch": 1}, ...]}
    Return: {"ok": true, "inserted": N, "updated": M}
    """
    expected = os.environ.get("RPG_ALLOWLIST_SHARED_SECRET", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="endpoint disabled (RPG_ALLOWLIST_SHARED_SECRET not set)")
    got = (request.headers.get("X-Internal-Secret") or "").strip()
    if not secrets.compare_digest(got, expected):
        raise HTTPException(status_code=401, detail="invalid X-Internal-Secret")
    body = await request.json()
    entries = body.get("entries") or []
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="entries must be a list")
    inserted = 0
    updated = 0
    skipped = []
    with connect() as db:
        for e in entries:
            if not isinstance(e, dict):
                continue
            email = (e.get("email") or "").strip().lower()
            token = (e.get("magic_token") or "").strip()
            batch = int(e.get("batch") or 1)
            if not email or not token:
                skipped.append({"email": email, "reason": "missing email or magic_token"})
                continue
            try:
                row = db.execute(
                    """
                    insert into registration_allowlist (email_norm, magic_token, batch, source)
                    values (%s, %s, %s, 'landing-batch')
                    on conflict (email_norm) do update
                      set magic_token = excluded.magic_token,
                          batch = excluded.batch
                    returning (xmax = 0) as is_new
                    """,
                    (email, token, batch),
                ).fetchone()
                if row and row.get("is_new"):
                    inserted += 1
                else:
                    updated += 1
            except Exception as exc:
                skipped.append({"email": email, "reason": f"{type(exc).__name__}: {exc}"})
    return json_response({
        "ok": True,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    })
