from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .db import connect, init_db
from .security import hash_password, normalize_username, verify_password

SESSION_DAYS = 14

from core.config import (
    login_lockout_sec as _login_lockout_sec,
)
from core.config import (
    login_max_fails as _login_max_fails,
)
from core.config import (
    login_window_sec as _login_window_sec,
)
from core.config import (
    min_password_length as _min_password_length,
)

MIN_PASSWORD_LENGTH = _min_password_length()

# ── 登录速率限制 ──────────────────────────────────────────────────────────
#
# 警告: 此速率限制使用进程内 dict 实现。
# 多 worker 部署（uvicorn --workers N / gunicorn）下，每个 worker 有独立内存，
# 速率限制 **不在 worker 间共享**，攻击者可以通过轮询 worker 绕过限制。
# 如需多 worker 部署，请将速率限制迁移至 Redis 或数据库后端。
#
LOGIN_MAX_FAILS = _login_max_fails()
LOGIN_LOCKOUT_SEC = _login_lockout_sec()
LOGIN_WINDOW_SEC = _login_window_sec()  # 5min 内累计失败计数

# P2-5 修复: 维护双独立 bucket，防止组合 key "ip|username" 可被任一维度绕过
# per-IP: 30次/10min; per-username: 5次/10min
_IP_MAX_FAILS = 30
_IP_WINDOW_SEC = 600  # 10min
_USER_MAX_FAILS = 5
_USER_WINDOW_SEC = 600  # 10min

_FAIL_BUCKETS_IP: dict[str, list[float]] = {}    # key=ip → [失败时间戳...]
_FAIL_BUCKETS_USER: dict[str, list[float]] = {}  # key=username → [失败时间戳...]
_LOCKED_UNTIL_IP: dict[str, float] = {}          # ip → 解锁时间
_LOCKED_UNTIL_USER: dict[str, float] = {}        # username → 解锁时间

# 兼容旧接口: _FAIL_BUCKETS/_LOCKED_UNTIL 保留但不再用于登录
_FAIL_BUCKETS: dict[str, list[float]] = {}  # key="ip|username" → [失败时间戳...]
_LOCKED_UNTIL: dict[str, float] = {}        # key → 解锁时间
_FAIL_LOCK = threading.Lock()

import logging as _logging
_log = _logging.getLogger(__name__)


class RateLimited(Exception):
    """登录被速率限制时抛出"""
    def __init__(self, retry_after_sec: int, key: str):
        self.retry_after_sec = retry_after_sec
        self.key = key
        super().__init__(f"too many failed logins; retry after {retry_after_sec}s")


def _bucket_key(ip: str, username: str) -> str:
    return f"{ip or '-'}|{(username or '').lower()}"


def _check_rate_limit(ip: str, username: str) -> None:
    # P2-5: 双独立 bucket — per-IP 和 per-username 任一超阈值即拒绝
    # 多 worker 部署下此速率限制不安全（每个 worker 独立内存，不共享）
    _log.debug("rate_limit check: ip=%s username=%s (in-process, unsafe under multi-worker)", ip, username)
    ip_key = ip or "-"
    user_key = (username or "").lower()
    now = time.monotonic()
    with _FAIL_LOCK:
        # 检查 IP 锁定
        unlock_ip = _LOCKED_UNTIL_IP.get(ip_key)
        if unlock_ip and now < unlock_ip:
            raise RateLimited(int(unlock_ip - now), f"ip:{ip_key}")
        elif unlock_ip:
            _LOCKED_UNTIL_IP.pop(ip_key, None)
        # 检查 username 锁定
        unlock_user = _LOCKED_UNTIL_USER.get(user_key)
        if unlock_user and now < unlock_user:
            raise RateLimited(int(unlock_user - now), f"user:{user_key}")
        elif unlock_user:
            _LOCKED_UNTIL_USER.pop(user_key, None)
        # 清理窗口外记录（只读，不计数）
        _FAIL_BUCKETS_IP[ip_key] = [t for t in _FAIL_BUCKETS_IP.get(ip_key, []) if now - t < _IP_WINDOW_SEC]
        _FAIL_BUCKETS_USER[user_key] = [t for t in _FAIL_BUCKETS_USER.get(user_key, []) if now - t < _USER_WINDOW_SEC]


def _record_login_fail(ip: str, username: str) -> int:
    """记录一次失败。返回 username bucket 内累计失败次数。超阈值会被锁定。"""
    # P2-5: 分别记录 per-IP 和 per-username bucket
    ip_key = ip or "-"
    user_key = (username or "").lower()
    now = time.monotonic()
    with _FAIL_LOCK:
        # per-IP bucket
        ip_bucket = _FAIL_BUCKETS_IP.setdefault(ip_key, [])
        ip_bucket.append(now)
        ip_bucket[:] = [t for t in ip_bucket if now - t < _IP_WINDOW_SEC]
        if len(ip_bucket) >= _IP_MAX_FAILS:
            _LOCKED_UNTIL_IP[ip_key] = now + LOGIN_LOCKOUT_SEC
        # per-username bucket
        user_bucket = _FAIL_BUCKETS_USER.setdefault(user_key, [])
        user_bucket.append(now)
        user_bucket[:] = [t for t in user_bucket if now - t < _USER_WINDOW_SEC]
        count = len(user_bucket)
        if count >= _USER_MAX_FAILS:
            _LOCKED_UNTIL_USER[user_key] = now + LOGIN_LOCKOUT_SEC
    _write_audit(username, ip, "login_fail", {"count": count})
    return count


def _record_login_success(ip: str, username: str) -> None:
    ip_key = ip or "-"
    user_key = (username or "").lower()
    with _FAIL_LOCK:
        _FAIL_BUCKETS_IP.pop(ip_key, None)
        _LOCKED_UNTIL_IP.pop(ip_key, None)
        _FAIL_BUCKETS_USER.pop(user_key, None)
        _LOCKED_UNTIL_USER.pop(user_key, None)
    _write_audit(username, ip, "login_ok", {})


def _write_audit(username: str, ip: str, event: str, meta: dict[str, Any]) -> None:
    try:
        init_db()
        with connect() as db:
            db.execute(
                """
                create table if not exists login_audit (
                  id bigint generated by default as identity primary key,
                  username text,
                  ip text,
                  event text not null,
                  meta jsonb not null default '{}'::jsonb,
                  created_at timestamptz not null default now()
                )
                """
            )
            db.execute(
                "insert into login_audit(username, ip, event, meta) values (%s, %s, %s, %s)",
                (username, ip, event, Jsonb(meta)),
            )
    except Exception:
        import logging as _logging
        _logging.getLogger(__name__).warning("audit write failed", exc_info=True)


def admin_unlock(ip: str, username: str) -> None:
    """admin 手动解锁某个用户/IP（暴露给 /api/admin/login/unlock 用）"""
    key = _bucket_key(ip, username)
    with _FAIL_LOCK:
        _FAIL_BUCKETS.pop(key, None)
        _LOCKED_UNTIL.pop(key, None)
    _write_audit(username, ip, "admin_unlock", {})


def _bootstrap_admin_allowed(setup_token: str | None) -> bool:
    """首用户(空 users 表)能否被授予 admin。

    - 本地/非鉴权模式:允许(单用户桌面场景,无引导风险)。
    - server/强制鉴权模式:必须配置 RPG_SETUP_TOKEN 且请求携带匹配令牌,
      否则首用户仅为普通 user —— 杜绝公网首注册抢 admin(CWE-269)。
    """
    from core.config import effective_auth_required
    from core.config import setup_token as _cfg_setup_token
    if not effective_auth_required():
        return True
    configured = (_cfg_setup_token() or "").strip()
    provided = (setup_token or "").strip()
    if not configured or not provided:
        return False
    return secrets.compare_digest(provided, configured)


def register(
    username: str,
    password: str,
    display_name: str = "",
    *,
    setup_token: str | None = None,
) -> dict[str, Any]:
    init_db()
    username = normalize_username(username)
    if not username:
        raise ValueError("用户名不能为空")
    if len(password or "") > 1024:
        raise ValueError("密码超长")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    allow_admin = _bootstrap_admin_allowed(setup_token)
    pw_hash = hash_password(password)
    disp = (display_name or username).strip()
    with connect() as db:
        try:
            if allow_admin:
                # 防 TOCTOU：用条件 INSERT，只有当 users 表中还没有 admin 时才插入 admin 角色
                row = db.execute(
                    """
                    insert into users(username, password_hash, display_name, role)
                    values (
                        %s, %s, %s,
                        CASE WHEN NOT EXISTS (SELECT 1 FROM users WHERE role = 'admin')
                             THEN 'admin'
                             ELSE 'user'
                        END
                    )
                    returning *
                    """,
                    (username, pw_hash, disp),
                ).fetchone()
            else:
                row = db.execute(
                    """
                    insert into users(username, password_hash, display_name, role)
                    values (%s, %s, %s, 'user')
                    returning *
                    """,
                    (username, pw_hash, disp),
                ).fetchone()
        except UniqueViolation as exc:
            raise ValueError("注册失败，请检查输入后重试") from exc
        return dict(row)


def _hash_token(token: str) -> str:
    """session token → sha256 hex(DB 只存哈希,不存明文)。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def login(username: str, password: str, *, ip: str = "") -> tuple[dict[str, Any], str]:
    """登录，带速率限制 + 失败审计"""
    if len(password or "") > 1024:
        raise ValueError("密码超长")
    init_db()
    normalized = normalize_username(username)
    _check_rate_limit(ip, normalized)  # 锁定中直接抛 RateLimited
    with connect() as db:
        # P1-1: 加 deactivated_at IS NULL 过滤，停用账号不允许登录
        row = db.execute(
            "select * from users where username = %s and deactivated_at is null",
            (normalized,),
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            _record_login_fail(ip, normalized)
            raise ValueError("用户名或密码错误")
        token = secrets.token_urlsafe(32)
        # 使用 timezone-aware UTC 时间, 避免 server 本地时区漂移 session 过期
        expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)

        # P2-2: 并发会话上限 20，超出时驱逐最旧的会话
        active_count = db.execute(
            "select count(*) as n from sessions where user_id = %s and expires_at > now()",
            (row["id"],),
        ).fetchone()["n"]
        if active_count >= 20:
            evict_count = int(active_count) - 19
            db.execute(
                """
                delete from sessions where id in (
                  select id from sessions
                  where user_id = %s and expires_at > now()
                  order by created_at asc
                  limit %s
                )
                """,
                (row["id"], evict_count),
            )

        # 安全:DB 只存 token 的 sha256 哈希,不存可直接使用的明文(拖库不得有效会话)
        # 注: token 列保留为 '' 兼容老 schema, 后续 migration 删除该列
        db.execute(
            "insert into sessions(token, token_hash, user_id, expires_at) values (%s, %s, %s, %s)",
            ("", _hash_token(token), row["id"], expires_at),
        )
        _record_login_success(ip, normalized)
        return dict(row), token


def logout(token: str | None) -> None:
    if not token:
        return
    init_db()
    with connect() as db:
        # 仅按 token_hash 删除。旧的明文 token 兼容分支已废弃 — 拖库后不允许重放。
        # 历史明文行需运维一次性清空（update sessions set token='' where token<>''）。
        db.execute("delete from sessions where token_hash = %s", (_hash_token(token),))


def user_from_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    init_db()
    with connect() as db:
        # 仅按 token_hash 查找。旧明文行已不接受 — 拖库后历史 token 立即失效。
        # P1-1: 加 users.deactivated_at IS NULL，停用账号的 token 立即失效
        row = db.execute(
            """
            select users.* from sessions
            join users on users.id = sessions.user_id
            where sessions.token_hash = %s
              and sessions.expires_at > now()
              and users.deactivated_at is null
            """,
            (_hash_token(token),),
        ).fetchone()
        return dict(row) if row else None


def get_user(user_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        row = db.execute("select * from users where id = %s", (user_id,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        return dict(row)


def update_profile(user_id: int, display_name: str, bio: str) -> dict[str, Any]:
    init_db()
    with connect() as db:
        row = db.execute(
            "update users set display_name = %s, bio = %s, row_version = row_version + 1, updated_at = now() where id = %s returning *",
            (display_name.strip(), bio.strip(), user_id),
        ).fetchone()
        return dict(row)
