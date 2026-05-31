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
from .security import (
    hash_password,
    normalize_email,
    normalize_username,
    verify_password,
    verify_password_with_rehash,
    generate_email_code,
    hash_email_code,
    verify_email_code,
    calc_age,
)

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
    email: str = "",
    birthday=None,
    invite_code: str | None = None,
    terms_accepted: bool = False,
    age_confirmed: bool = False,
    setup_token: str | None = None,
    ip: str = "",
    ua: str = "",
) -> dict[str, Any]:
    """两步注册 Phase 1：写 email_verifications pending，发验证码，不创建 users 行。

    Returns:
        {"ok": True, "pending_verify": True, "email_mask": "u***@example.com"}
    """
    init_db()
    username = normalize_username(username)
    if not username:
        raise ValueError("用户名不能为空")
    if len(password or "") > 1024:
        raise ValueError("密码超长")
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")

    # ── REG-01: email 必填 ────────────────────────────────────────────────────
    email_norm = normalize_email(email)
    if not email_norm or "@" not in email_norm:
        raise ValueError("请填写有效的邮箱地址")

    # ── AGE-01: 18+ 校验 ──────────────────────────────────────────────────────
    if birthday is None:
        raise ValueError("请提供出生日期")
    from datetime import date as _date
    if isinstance(birthday, str):
        try:
            birthday = _date.fromisoformat(birthday)
        except ValueError as exc:
            raise ValueError("出生日期格式错误，请使用 YYYY-MM-DD") from exc
    if calc_age(birthday) < 18:
        raise ValueError("你必须年满 18 周岁才能注册")

    with connect() as db:
        # ── REG-04: 查 banned_users ───────────────────────────────────────────
        banned = db.execute(
            "select 1 from banned_users where email_norm = %s limit 1",
            (email_norm,),
        ).fetchone()
        if banned:
            raise ValueError("该邮箱已被限制注册")

        # 检查 email 是否已被已验证用户占用
        existing_email = db.execute(
            "select 1 from users where lower(email) = %s and email_verified = true limit 1",
            (email_norm,),
        ).fetchone()
        if existing_email:
            raise ValueError("该邮箱已被注册")

        # 检查 username 是否已占用
        existing_user = db.execute(
            "select 1 from users where username = %s limit 1",
            (username,),
        ).fetchone()
        if existing_user:
            raise ValueError("注册失败，请检查输入后重试")

        # ── 邀请码校验（invite 模式）─────────────────────────────────────────
        _check_invite_code(db, invite_code)

        # ── 写 email_verifications (pending) ──────────────────────────────────
        code = generate_email_code(6)
        code_h = hash_email_code(code)
        from datetime import timezone, timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

        # 失效同邮箱之前的未使用记录（防积累），再插入新记录
        db.execute(
            "update email_verifications set used_at = now() where lower(email) = %s and used_at is null and purpose = 'register'",
            (email_norm,),
        )
        db.execute(
            """
            insert into email_verifications
              (email, code_hash, purpose, expires_at, ip, ua)
            values (%s, %s, 'register', %s, %s, %s)
            """,
            (email_norm, code_h, expires_at, ip or "", ua or ""),
        )

    # ── 临时存注册数据到 verif 行的 ua 字段太窄，改用进程内 dict ──────────────
    # 这里把 pending 注册参数序列化到全局缓存（不跨 worker），多 worker 下须改 Redis
    import json as _json
    _PENDING_REGISTER[email_norm] = _json.dumps({
        "username": username,
        "password_hash": hash_password(password),
        "display_name": (display_name or username).strip(),
        "birthday": birthday.isoformat(),
        "terms_accepted": terms_accepted,
        "age_confirmed": age_confirmed,
        "invite_code": invite_code,
        "setup_token": setup_token,
    })

    # ── 发验证码邮件 ──────────────────────────────────────────────────────────
    from .email import send_verification_email, EmailSendError
    try:
        send_verification_email(email_norm, code)
    except EmailSendError:
        _log.warning("send_verification_email failed (RESEND unconfigured?); code=%s", code)

    # email mask: u***@example.com
    local_part, domain_part = email_norm.split("@", 1)
    mask = local_part[0] + "***@" + domain_part

    return {"ok": True, "pending_verify": True, "email_mask": mask}


# 进程内 pending 注册缓存（多 worker 须改 Redis）
_PENDING_REGISTER: dict[str, str] = {}


def _check_invite_code(db, invite_code: str | None) -> None:
    """若 registration_config.mode == 'invite'，校验 invite_code；否则跳过。

    invite_codes 表 v36 已存在。注意: registration_config 来自 app_config 表
    如果该表/行不存在则视为 open 模式。
    """
    try:
        cfg_row = db.execute(
            "select value from app_config where key = 'registration_config' limit 1"
        ).fetchone()
    except Exception:
        cfg_row = None

    mode = "open"
    if cfg_row:
        import json as _json
        try:
            cfg = _json.loads(cfg_row["value"])
            mode = cfg.get("mode", "open")
        except Exception:
            pass

    if mode != "invite":
        return

    if not invite_code:
        raise ValueError("当前平台为邀请制，请提供邀请码")

    row = db.execute(
        """
        select * from invite_codes
        where code = %s
          and used_by is null
          and (expires_at is null or expires_at > now())
        limit 1
        """,
        (invite_code,),
    ).fetchone()
    if not row:
        raise ValueError("邀请码无效或已使用")


def confirm_email_verification(email: str, code: str) -> tuple[dict[str, Any], str]:
    """两步注册 Phase 2：验证 code → 创建 users 行 → 颁 session token。

    Returns:
        (user_dict, session_token)
    """
    import json as _json
    email_norm = normalize_email(email)
    init_db()

    with connect() as db:
        # 查有效 verif 记录
        verif = db.execute(
            """
            select * from email_verifications
            where lower(email) = %s
              and purpose = 'register'
              and used_at is null
              and expires_at > now()
            order by created_at desc
            limit 1
            """,
            (email_norm,),
        ).fetchone()
        if not verif:
            raise ValueError("验证码已过期或不存在，请重新注册")

        if not verify_email_code(code, verif["code_hash"]):
            raise ValueError("验证码错误")

        # 标记已使用
        db.execute(
            "update email_verifications set used_at = now() where id = %s",
            (verif["id"],),
        )

        # 取 pending 注册参数
        pending_json = _PENDING_REGISTER.pop(email_norm, None)
        if not pending_json:
            raise ValueError("注册会话已过期，请重新注册")

        pending = _json.loads(pending_json)
        allow_admin = _bootstrap_admin_allowed(pending.get("setup_token"))

        from datetime import date as _date, timezone as _tz
        birthday = _date.fromisoformat(pending["birthday"])

        try:
            if allow_admin:
                row = db.execute(
                    """
                    insert into users(
                      username, password_hash, display_name, role,
                      email, email_verified, email_verified_at,
                      birthday, terms_accepted_at, age_confirmed
                    )
                    values (
                      %s, %s, %s,
                      CASE WHEN NOT EXISTS (SELECT 1 FROM users WHERE role = 'admin')
                           THEN 'admin' ELSE 'user' END,
                      %s, true, now(), %s,
                      CASE WHEN %s THEN now() ELSE null END, %s
                    )
                    returning *
                    """,
                    (
                        pending["username"], pending["password_hash"], pending["display_name"],
                        email_norm, birthday,
                        pending.get("terms_accepted", False),
                        pending.get("age_confirmed", False),
                    ),
                ).fetchone()
            else:
                row = db.execute(
                    """
                    insert into users(
                      username, password_hash, display_name, role,
                      email, email_verified, email_verified_at,
                      birthday, terms_accepted_at, age_confirmed
                    )
                    values (%s, %s, %s, 'user', %s, true, now(), %s,
                            CASE WHEN %s THEN now() ELSE null END, %s)
                    returning *
                    """,
                    (
                        pending["username"], pending["password_hash"], pending["display_name"],
                        email_norm, birthday,
                        pending.get("terms_accepted", False),
                        pending.get("age_confirmed", False),
                    ),
                ).fetchone()
        except UniqueViolation as exc:
            raise ValueError("注册失败，请检查输入后重试") from exc

        user = dict(row)

        # 标记 invite_code 已用
        invite_code = pending.get("invite_code")
        if invite_code:
            db.execute(
                "update invite_codes set used_by = %s, used_at = now() where code = %s and used_by is null",
                (user["id"], invite_code),
            )

        # 颁 session
        token = secrets.token_urlsafe(32)
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
        db.execute(
            "insert into sessions(token, token_hash, user_id, expires_at) values (%s, %s, %s, %s)",
            ("", _hash_token(token), user["id"], expires_at),
        )

    return user, token


def _hash_token(token: str) -> str:
    """session token → sha256 hex(DB 只存哈希,不存明文)。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def login(username: str, password: str, *, ip: str = "") -> tuple[dict[str, Any], str]:
    """登录，带速率限制 + 失败审计 + email 登录支持 + Argon2id rehash。"""
    if len(password or "") > 1024:
        raise ValueError("密码超长")
    init_db()
    normalized = normalize_username(username)
    _check_rate_limit(ip, normalized)  # 锁定中直接抛 RateLimited
    with connect() as db:
        # 先尝试 username，再尝试 email（REG-01：支持邮箱登录）
        row = db.execute(
            "select * from users where username = %s and deactivated_at is null",
            (normalized,),
        ).fetchone()
        if not row:
            # 尝试邮箱登录（仅已验证邮箱）
            email_norm = normalize_email(username)
            row = db.execute(
                "select * from users where lower(email) = %s and email_verified = true and deactivated_at is null limit 1",
                (email_norm,),
            ).fetchone()

        if not row:
            _record_login_fail(ip, normalized)
            raise ValueError("用户名或密码错误")

        ok, needs_rehash = verify_password_with_rehash(row["password_hash"], password)
        if not ok:
            _record_login_fail(ip, normalized)
            raise ValueError("用户名或密码错误")

        # ENC-08: 老 PBKDF2 账号登录成功后升级为 Argon2id
        if needs_rehash:
            new_hash = hash_password(password)
            db.execute(
                "update users set password_hash = %s where id = %s",
                (new_hash, row["id"]),
            )
            _log.info("rehashed password to argon2id for user_id=%s", row["id"])

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


# ── 重发验证码（限流 1/分钟/email）─────────────────────────────────────────────
_RESEND_LAST: dict[str, float] = {}  # email_norm → last resend monotonic timestamp


def resend_verification_code(email: str, ip: str = "") -> None:
    """重发验证码。限流：同一邮箱 60 秒内只能触发一次。"""
    email_norm = normalize_email(email)
    if not email_norm or "@" not in email_norm:
        raise ValueError("无效邮箱")

    now = time.monotonic()
    last = _RESEND_LAST.get(email_norm, 0.0)
    if now - last < 60:
        wait = int(60 - (now - last)) + 1
        raise ValueError(f"发送太频繁，请 {wait} 秒后再试")
    _RESEND_LAST[email_norm] = now

    # 检查是否有有效 pending 记录（没有则不重发）
    if email_norm not in _PENDING_REGISTER:
        raise ValueError("注册会话已过期，请重新注册")

    init_db()
    with connect() as db:
        # 废弃旧记录，发新验证码
        db.execute(
            "update email_verifications set used_at = now() where lower(email) = %s and used_at is null and purpose = 'register'",
            (email_norm,),
        )
        code = generate_email_code(6)
        code_h = hash_email_code(code)
        from datetime import timezone as _tz, timedelta as _td
        expires_at = datetime.now(_tz.utc) + _td(minutes=10)
        db.execute(
            "insert into email_verifications (email, code_hash, purpose, expires_at, ip) values (%s, %s, 'register', %s, %s)",
            (email_norm, code_h, expires_at, ip or ""),
        )

    from .email import send_verification_email, EmailSendError
    try:
        send_verification_email(email_norm, code)
    except EmailSendError:
        _log.warning("resend_verification_code: email send failed for %s; code=%s", email_norm, code)
