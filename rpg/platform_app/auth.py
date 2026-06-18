from __future__ import annotations

import datetime as _datetime
import hashlib
import secrets
import threading
import time
from datetime import datetime, timedelta
from typing import Any

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .db import connect, init_db
from .security import (
    calc_age,
    generate_email_code,
    hash_email_code,
    hash_password,
    normalize_email,
    normalize_username,
    verify_email_code,
    verify_password_with_rehash,
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
UTC = _datetime.UTC

# ── 登录速率限制 ──────────────────────────────────────────────────────────
#
# 此速率限制使用进程内 dict 实现。后端按单进程(单 uvicorn worker)部署,
# 因此进程内计数是准确且共享的。
# 注意: 若未来改为多副本横向扩展(多个独立后端进程),各进程内存独立,
# 速率限制将不再跨进程共享 —— 届时需迁移至共享后端(Redis / 数据库)。
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

# 邮箱验证码暴破防护:6 位码(10^6 空间)+ 10min 有效期,原无尝试上限 → 知道受害者
# 邮箱(注册中)即可暴破。按 email 计失败次数,超 _VERIFY_MAX_FAILS 锁定该窗口(与登录限流同构)。
_VERIFY_MAX_FAILS = 10
_VERIFY_WINDOW_SEC = 600  # 10min,与验证码有效期一致
_VERIFY_FAIL_BUCKETS: dict[str, list[float]] = {}  # email_norm → [失败时间戳...]
_VERIFY_LOCKED_UNTIL: dict[str, float] = {}        # email_norm → 解锁时间

# 兼容旧接口: _FAIL_BUCKETS/_LOCKED_UNTIL 保留但不再用于登录
_FAIL_BUCKETS: dict[str, list[float]] = {}  # key="ip|username" → [失败时间戳...]
_LOCKED_UNTIL: dict[str, float] = {}        # key → 解锁时间
_FAIL_LOCK = threading.Lock()

import logging as _logging

_log = _logging.getLogger(__name__)

_PENDING_REGISTER_UA_PREFIX = "rpg-pending-register:v1:"


class RateLimited(Exception):
    """登录被速率限制时抛出"""
    def __init__(self, retry_after_sec: int, key: str):
        self.retry_after_sec = retry_after_sec
        self.key = key
        super().__init__(f"too many failed logins; retry after {retry_after_sec}s")


def _bucket_key(ip: str, username: str) -> str:
    return f"{ip or '-'}|{(username or '').lower()}"


def _check_rate_limit(ip: str, username: str) -> None:
    # P2-5: 双独立 bucket — per-IP 和 per-username 任一超阈值即拒绝。
    # Redis 可用时用共享锁定键(跨 worker 一致,根治多 worker 限流 ×N 绕过);
    # 不可用回落进程内(单进程语义)。
    ip_key = ip or "-"
    user_key = (username or "").lower()
    import redis_bus
    if redis_bus.get_sync_client() is not None:
        for scope, k in (("ip", ip_key), ("user", user_key)):
            rem = redis_bus.lock_remaining(f"login:{scope}:{k}")
            if rem and rem > 0:
                raise RateLimited(int(rem), f"{scope}:{k}")
        return
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
    import redis_bus
    if redis_bus.get_sync_client() is not None:
        ip_cnt = redis_bus.rate_incr(f"loginfail:ip:{ip_key}", _IP_WINDOW_SEC) or 0
        if ip_cnt >= _IP_MAX_FAILS:
            redis_bus.lock_set(f"login:ip:{ip_key}", LOGIN_LOCKOUT_SEC)
            redis_bus.rate_reset(f"loginfail:ip:{ip_key}")  # 锁定即清计数,避免锁到期后一击复锁
        user_cnt = redis_bus.rate_incr(f"loginfail:user:{user_key}", _USER_WINDOW_SEC) or 0
        if user_cnt >= _USER_MAX_FAILS:
            redis_bus.lock_set(f"login:user:{user_key}", LOGIN_LOCKOUT_SEC)
            # 关键:锁定后清空固定窗口计数器。否则计数 TTL(600s)≫ 锁定 TTL(60s),
            # 锁到期后计数仍 ≥ 阈值,用户再失败 1 次就立即复锁,反复锁死 ~9 分钟。
            # 清零后锁到期需重新累积满阈值才再锁,语义与进程内滑动窗口一致。
            redis_bus.rate_reset(f"loginfail:user:{user_key}")
        _write_audit(username, ip, "login_fail", {"count": user_cnt})
        return user_cnt
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
    import redis_bus
    if redis_bus.get_sync_client() is not None:
        for scope, k in (("ip", ip_key), ("user", user_key)):
            redis_bus.rate_reset(f"loginfail:{scope}:{k}")
            redis_bus.lock_clear(f"login:{scope}:{k}")
        _write_audit(username, ip, "login_ok", {})
        return
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


def _mask_email(email: str) -> str:
    email_norm = normalize_email(email)
    if "@" not in email_norm:
        return ""
    local_part, domain_part = email_norm.split("@", 1)
    return (local_part[:1] or "*") + "***@" + domain_part


def admin_unlock(ip: str, username: str) -> None:
    """admin 手动解锁某个用户/IP（暴露给 /api/admin/login/unlock 用）"""
    ip_key = ip or "-"
    user_key = (username or "").lower()
    # Redis 模式:清共享锁定键 + 失败计数
    try:
        import redis_bus
        if redis_bus.get_sync_client() is not None:
            for scope, k in (("ip", ip_key), ("user", user_key)):
                redis_bus.lock_clear(f"login:{scope}:{k}")
                redis_bus.rate_reset(f"loginfail:{scope}:{k}")
    except Exception:
        pass
    key = _bucket_key(ip, username)
    with _FAIL_LOCK:
        _FAIL_BUCKETS.pop(key, None)
        _LOCKED_UNTIL.pop(key, None)
        _LOCKED_UNTIL_IP.pop(ip_key, None)
        _LOCKED_UNTIL_USER.pop(user_key, None)
        _FAIL_BUCKETS_IP.pop(ip_key, None)
        _FAIL_BUCKETS_USER.pop(user_key, None)
    _write_audit(username, ip, "admin_unlock", {})


def _bootstrap_admin_allowed(setup_token: str | None) -> bool:
    """首用户(空 users 表)能否被授予 admin。

    - 本地/非鉴权模式:允许(单用户桌面场景,无引导风险)。
    - server/强制鉴权模式:必须配置 RPG_SETUP_TOKEN 且请求携带匹配令牌,
      否则首用户仅为普通 user —— 杜绝公网首注册抢 admin(CWE-269)。

    注:自托管单人若想免 token 让首用户直接成 admin,把部署模式设为 local
    (或 RPG_REQUIRE_AUTH=0)即可走上面的免鉴权分支;server 模式保留硬门控。
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

    pending_payload = {
        "username": username,
        "password_hash": hash_password(password),
        "display_name": (display_name or username).strip(),
        "birthday": birthday.isoformat(),
        "terms_accepted": terms_accepted,
        "age_confirmed": age_confirmed,
        "invite_code": invite_code,
        "allow_admin": _bootstrap_admin_allowed(setup_token),
        "ip": ip or "",
        "ua": ua or "",
        "created_at": datetime.now(UTC).isoformat(),
    }
    pending_json = _encode_pending_register(pending_payload)

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

        # ── 白名单校验(allowlist 模式) ────────────────────────────────────────
        # task: 内测期所有注册路径(密码注册 + magic-link)都要白名单 gate。
        # registration_config.mode='allowlist' 时,只准 registration_allowlist
        # 里的邮箱注册。开发模式 / open 不变。
        try:
            row = db.execute(
                "select value from app_config where key = 'admin.registration_config' limit 1"
            ).fetchone()
            cfg = (row.get("value") if row else None) or {}
            mode = (cfg.get("mode") or "").lower()
        except Exception:
            mode = ""
        # ── 关闭注册(closed 模式)─────────────────────────────────────────────
        # admin 在「注册与邀请」里把模式设为 closed 即禁止任何新用户注册。
        # 例外:users 表为空时放行,保证首个用户始终能注册并 bootstrap 成 admin
        # (否则一旦误设 closed 且无 admin,实例将永久无法注册 = 自锁)。
        if mode == "closed":
            any_user = db.execute("select 1 from users limit 1").fetchone()
            if any_user:
                raise ValueError("该平台已关闭新用户注册")
        # task: mode='invite' 是 admin UI「仅邀请」按钮的语义,
        # mode='allowlist' 是 SQL 手动设置的别名 — 两者都走白名单 gate。
        if mode in ("allowlist", "invite"):
            wl = db.execute(
                "select 1 from registration_allowlist where email_norm = %s",
                (email_norm,),
            ).fetchone()
            if not wl:
                raise ValueError("该邮箱不在内测白名单。本批次仅向早期预约者开放,如需加入下一批请到 play.stellatrix.icu 留邮箱。")

        # ── 写 email_verifications (pending) ──────────────────────────────────
        code = generate_email_code(6)
        code_h = hash_email_code(code)
        expires_at = datetime.now(UTC) + timedelta(minutes=10)

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
            # SEC(H-7): ua 列存真实 user-agent,不再塞含 password_hash 的 pending_json。
            (email_norm, code_h, expires_at, ip or "", (ua or "")[:512]),
        )

    _pending_store_set(email_norm, pending_json)

    # ── 本地/自托管模式:跳过邮箱验证 ──────────────────────────────────────────
    # 开源用户反馈:自托管没有 RESEND_API_KEY → 验证码发不出(Resend 403)→ 卡注册,
    # 只能从后端日志扒验证码。邮箱验证只在 server 强制鉴权模式有意义(数据在云端);
    # 本地部署数据保存在本地,直接用刚生成的 code 完成注册并登录,无需邮件。
    from core.config import require_auth as _require_auth_reg
    if not _require_auth_reg():
        try:
            user, token = confirm_email_verification(email_norm, code)
            _log.info("[register] 本地模式自动完成注册(免邮箱验证) email=%s", _mask_email(email_norm))
            return {
                "ok": True, "pending_verify": False, "auto_verified": True,
                "user": user, "session_token": token,
                "email_mask": _mask_email(email_norm),
            }
        except Exception as _e:  # noqa: BLE001
            _log.warning("[register] 本地模式自动验证失败,回退验证码流程: %s", _e)

    # ── server 模式:发验证码邮件 ──────────────────────────────────────────────
    from .email import EmailSendError, send_verification_email
    try:
        send_verification_email(email_norm, code)
    except EmailSendError:
        _log.warning("send_verification_email failed (RESEND unconfigured?)")  # SEC(M-10): 不记明文验证码

    return {"ok": True, "pending_verify": True, "email_mask": _mask_email(email_norm)}


# 进程内 pending 注册缓存(回退用);跨 worker 走 Redis(见 _pending_store_*)
_PENDING_REGISTER: dict[str, str] = {}
_PENDING_TTL_SEC = 1800  # 待确认注册暂存有效期(覆盖 10 分钟验证码 + resend)


def _pending_redis_key(email_norm: str) -> str:
    return f"rpg:pending_reg:{email_norm}"


def _pending_store_set(email_norm: str, pending_json: str) -> None:
    """SEC(H-7): 跨 worker 暂存待确认注册(内含 Argon2 password_hash)。优先 Redis(有 TTL、
    生产多 worker 共享),回退进程内 dict。**不再写入 email_verifications.ua**——那是 DB 明文
    哈希暴露面(DB 读权限即可离线爆破未完成注册的密码)。"""
    _PENDING_REGISTER[email_norm] = pending_json
    try:
        import redis_bus
        c = redis_bus.get_sync_client()
        if c is not None:
            c.setex(_pending_redis_key(email_norm), _PENDING_TTL_SEC, pending_json)
    except Exception:
        pass


def _pending_store_get(email_norm: str, *, consume: bool = False) -> str | None:
    val = _PENDING_REGISTER.get(email_norm)
    if val is None:
        try:
            import redis_bus
            c = redis_bus.get_sync_client()
            if c is not None:
                raw = c.get(_pending_redis_key(email_norm))
                if raw is not None:
                    val = raw.decode() if isinstance(raw, (bytes, bytearray)) else str(raw)
        except Exception:
            val = None
    if consume:
        _PENDING_REGISTER.pop(email_norm, None)
        try:
            import redis_bus
            c = redis_bus.get_sync_client()
            if c is not None:
                c.delete(_pending_redis_key(email_norm))
        except Exception:
            pass
    return val


def _encode_pending_register(payload: dict[str, Any]) -> str:
    import json as _json
    return _PENDING_REGISTER_UA_PREFIX + _json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _decode_pending_register(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    import json as _json
    text = str(raw)
    if text.startswith(_PENDING_REGISTER_UA_PREFIX):
        text = text[len(_PENDING_REGISTER_UA_PREFIX):]
    try:
        payload = _json.loads(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if not payload.get("username") or not payload.get("password_hash") or not payload.get("birthday"):
        return None
    return payload


def _row_get(row, key: str, default=None):
    try:
        return row[key]
    except Exception:
        try:
            return row.get(key, default)
        except Exception:
            return default


def _check_invite_code(db, invite_code: str | None) -> None:
    """若 registration_config.mode == 'invite'，校验 invite_code；否则跳过。

    invite_codes 表 v36 已存在。注意: registration_config 来自 app_config 表
    如果该表/行不存在则视为 open 模式。
    """
    try:
        cfg_row = db.execute(
            "select value from app_config where key = 'admin.registration_config' limit 1"
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


def _verify_locked(email_norm: str) -> bool:
    """该 email 是否因验证码尝试过多被锁。Redis 主路径,失败回落进程内。"""
    import redis_bus
    if redis_bus.get_sync_client() is not None:
        return (redis_bus.lock_remaining(f"verify:{email_norm}") or 0) > 0
    now = time.monotonic()
    with _FAIL_LOCK:
        return _VERIFY_LOCKED_UNTIL.get(email_norm, 0) > now


def _record_verify_fail(email_norm: str) -> None:
    """记录一次验证码失败;达上限则锁定该 email 一个窗口。"""
    import redis_bus
    if redis_bus.get_sync_client() is not None:
        cnt = redis_bus.rate_incr(f"verifyfail:{email_norm}", _VERIFY_WINDOW_SEC) or 0
        if cnt >= _VERIFY_MAX_FAILS:
            redis_bus.lock_set(f"verify:{email_norm}", _VERIFY_WINDOW_SEC)
            redis_bus.rate_reset(f"verifyfail:{email_norm}")  # 锁定即清计数,避免锁到期一击复锁
        return
    now = time.monotonic()
    with _FAIL_LOCK:
        bucket = _VERIFY_FAIL_BUCKETS.setdefault(email_norm, [])
        bucket.append(now)
        bucket[:] = [t for t in bucket if now - t < _VERIFY_WINDOW_SEC]
        if len(bucket) >= _VERIFY_MAX_FAILS:
            _VERIFY_LOCKED_UNTIL[email_norm] = now + _VERIFY_WINDOW_SEC


def _clear_verify_fail(email_norm: str) -> None:
    """验证成功后清计数 + 解锁。"""
    import redis_bus
    if redis_bus.get_sync_client() is not None:
        redis_bus.rate_reset(f"verifyfail:{email_norm}")
        redis_bus.lock_clear(f"verify:{email_norm}")
        return
    with _FAIL_LOCK:
        _VERIFY_FAIL_BUCKETS.pop(email_norm, None)
        _VERIFY_LOCKED_UNTIL.pop(email_norm, None)


def confirm_email_verification(email: str, code: str) -> tuple[dict[str, Any], str]:
    """两步注册 Phase 2：验证 code → 创建 users 行 → 颁 session token。

    Returns:
        (user_dict, session_token)
    """
    email_norm = normalize_email(email)
    init_db()

    # 暴破防护:同 email 验证码尝试过多则锁定一个窗口(防 6 位码被穷举)
    if _verify_locked(email_norm):
        raise ValueError("验证尝试次数过多，请稍后重新获取验证码")

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
            _record_verify_fail(email_norm)  # 计失败,达上限锁定该 email 一个窗口(防暴破)
            raise ValueError("验证码错误")
        _clear_verify_fail(email_norm)  # 验证通过,清失败计数 + 解锁

        # 取 pending 注册参数。优先读进程缓存；若部署重启导致缓存丢失，
        # 从 email_verifications.ua 中恢复，保证验证码窗口内仍可完成注册。
        # SEC(H-7): 从 Redis/进程内取 pending(不再从 ua 列恢复 password_hash)。
        pending_json = _pending_store_get(email_norm, consume=True)
        pending = _decode_pending_register(pending_json)
        if not pending:
            raise ValueError("注册会话已过期，请重新注册")

        allow_admin = (
            bool(pending.get("allow_admin"))
            if "allow_admin" in pending
            else _bootstrap_admin_allowed(pending.get("setup_token"))
        )

        from datetime import date as _date
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

        # 标记 invite_code 已用。原子预占:用 rowcount 判定是否抢到。
        # 原写法不查 rowcount,两个请求拿同一单次邀请码并发 confirm 时,user 行已先 INSERT,
        # 第二个的 UPDATE 命中 0 行被忽略却照样建号 → 单次邀请码双花、邀请 gate 被绕过。
        # 命中 0 行即抛错回滚整个事务(同一 with connect(),user INSERT 一并回滚)。
        invite_code = pending.get("invite_code")
        if invite_code:
            _res = db.execute(
                "update invite_codes set used_by = %s, used_at = now() where code = %s and used_by is null",
                (user["id"], invite_code),
            )
            if _res.rowcount == 0:
                raise ValueError("邀请码已被使用，请联系邀请人获取新的邀请码")

        # 标记验证码已使用。放在用户创建之后，避免 pending 恢复失败时吞掉有效验证码。
        db.execute(
            "update email_verifications set used_at = now() where id = %s",
            (verif["id"],),
        )

        # 颁 session
        token = secrets.token_urlsafe(32)
        from datetime import timedelta
        expires_at = datetime.now(UTC) + timedelta(days=SESSION_DAYS)
        db.execute(
            "insert into sessions(token, token_hash, user_id, expires_at) values (%s, %s, %s, %s)",
            ("", _hash_token(token), user["id"], expires_at),
        )

    return user, token


def _hash_token(token: str) -> str:
    """session token → sha256 hex(DB 只存哈希,不存明文)。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _issue_session(db, user_id: int) -> str:
    active_count = db.execute(
        "select count(*) as n from sessions where user_id = %s and expires_at > now()",
        (user_id,),
    ).fetchone()["n"]
    if active_count >= 20:
        db.execute(
            """
            delete from sessions where id in (
              select id from sessions
              where user_id = %s and expires_at > now()
              order by created_at asc
              limit %s
            )
            """,
            (user_id, int(active_count) - 19),
        )
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + timedelta(days=SESSION_DAYS)
    db.execute(
        "insert into sessions(token, token_hash, user_id, expires_at) values (%s, %s, %s, %s)",
        ("", _hash_token(token), user_id, expires_at),
    )
    return token


# ──────────────────────────────────────────────────────────────────────────────
# 桌面/本地部署:默认账户 + 账户管理 + 一次性「免登录魔法链接」
# ──────────────────────────────────────────────────────────────────────────────
DESKTOP_LOGIN_TTL_MIN = 10  # 魔法链接有效期(分钟),单次使用


def local_account() -> dict[str, Any] | None:
    """本地默认账户 = 库中第一个用户(按 id)。无则 None。"""
    with connect() as db:
        row = db.execute("select * from users order by id asc limit 1").fetchone()
        return dict(row) if row else None


def bootstrap_local_account(username: str = "local", display_name: str = "本地用户") -> dict[str, Any]:
    """本地/桌面模式首启:若库中没有任何用户,创建一个默认账户(role=admin,无密码 → 回环免登录)。
    幂等:已有用户则原样返回第一个。改用户名/密码不换 id,数据始终归同一账户。"""
    existing = local_account()
    if existing:
        return existing
    init_db()
    with connect() as db:
        # 再查一次(并发/多 worker 防重),无则插入。
        row = db.execute("select * from users order by id asc limit 1").fetchone()
        if row:
            return dict(row)
        uname = normalize_username(username) or "local"
        row = db.execute(
            """
            insert into users(username, password_hash, display_name, role,
                              email, email_verified, age_confirmed, terms_accepted_at)
            values (%s, null, %s, 'admin', '', false, true, now())
            returning *
            """,
            (uname, (display_name or uname)),
        ).fetchone()
        return dict(row)


def local_account_has_password() -> bool:
    """默认账户是否已设密码(设了则回环也要求真实会话 / LAN 必须登录)。"""
    acct = local_account()
    return bool(acct and acct.get("password_hash"))


def update_local_account(user_id: int, *, username: str | None = None,
                         display_name: str | None = None) -> dict[str, Any]:
    """改本地账户用户名/昵称(不换 id)。用户名唯一冲突 → ValueError。"""
    sets, args = [], []
    if username is not None:
        uname = normalize_username(username)
        if not uname:
            raise ValueError("用户名不能为空")
        sets.append("username = %s")
        args.append(uname)
    if display_name is not None:
        sets.append("display_name = %s")
        args.append(display_name.strip() or "本地用户")
    if not sets:
        raise ValueError("无可更新字段")
    sets.append("updated_at = now()")
    args.append(int(user_id))
    with connect() as db:
        try:
            row = db.execute(
                f"update users set {', '.join(sets)} where id = %s returning *",
                tuple(args),
            ).fetchone()
        except UniqueViolation as exc:
            raise ValueError("该用户名已被占用") from exc
    if not row:
        raise ValueError("账户不存在")
    return dict(row)


def set_account_password(user_id: int, password: str) -> None:
    """设/改/清除本地账户密码。空字符串 = 清除(回到回环免登录)。
    不撤销现有 session(避免把正打开的浏览器/控制台踢掉)。"""
    pw = password or ""
    pw_hash = hash_password(pw) if pw else None
    with connect() as db:
        db.execute(
            "update users set password_hash = %s, updated_at = now() where id = %s",
            (pw_hash, int(user_id)),
        )


def create_desktop_login_token(user_id: int) -> str:
    """铸一次性桌面登录 token(控制台主进程在回环内调用)。返回明文,DB 只存哈希。"""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=DESKTOP_LOGIN_TTL_MIN)
    with connect() as db:
        # 同账户旧的未用 token 作废(始终最多一个有效魔法链接)。
        db.execute(
            "update desktop_login_tokens set used_at = now() where user_id = %s and used_at is null",
            (int(user_id),),
        )
        db.execute(
            "insert into desktop_login_tokens(token_hash, user_id, expires_at) values (%s, %s, %s)",
            (_hash_token(token), int(user_id), expires_at),
        )
    return token


def consume_desktop_login_token(token: str) -> tuple[dict[str, Any], str]:
    """兑换桌面登录 token → (user, session_token)。单次使用 + TTL 校验。失败 → ValueError。"""
    if not token:
        raise ValueError("缺 token")
    init_db()
    with connect() as db:
        # 原子认领:UPDATE ... WHERE used_at IS NULL RETURNING —— 行锁让第一个写者赢,
        # 并发的第二次 consume 命中 0 行(used_at 已非空)→ 真·单次使用,杜绝 TOCTOU 双发会话。
        claimed = db.execute(
            "update desktop_login_tokens set used_at = now() "
            "where token_hash = %s and used_at is null and expires_at > now() "
            "returning user_id",
            (_hash_token(token),),
        ).fetchone()
        if not claimed:
            raise ValueError("链接无效或已过期")
        user = db.execute(
            "select * from users where id = %s and deactivated_at is null",
            (claimed["user_id"],),
        ).fetchone()
        if not user:
            raise ValueError("账户不存在")
        session_token = _issue_session(db, int(user["id"]))
    return dict(user), session_token


def request_login_code(email: str, *, ip: str = "", ua: str = "") -> dict[str, Any]:
    """Send a one-time email code for passwordless login.

    The request path is intentionally non-enumerating: unknown or unverified
    emails still return ok, but no email is sent.
    """
    email_norm = normalize_email(email)
    if not email_norm or "@" not in email_norm:
        raise ValueError("请填写有效的邮箱地址")
    _check_rate_limit(ip, email_norm)

    init_db()
    user_id: int | None = None
    with connect() as db:
        row = db.execute(
            """
            select id from users
            where lower(email) = %s
              and email_verified = true
              and deactivated_at is null
            limit 1
            """,
            (email_norm,),
        ).fetchone()
        if not row:
            return {"ok": True, "pending_verify": True, "email_mask": _mask_email(email_norm)}

        recent = db.execute(
            """
            select created_at from email_verifications
            where lower(email) = %s
              and purpose = 'login'
            order by created_at desc
            limit 1
            """,
            (email_norm,),
        ).fetchone()
        if recent:
            created = recent["created_at"]
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            elapsed = (datetime.now(UTC) - created).total_seconds()
            if elapsed < 60:
                raise ValueError(f"发送太频繁，请 {int(60 - elapsed) + 1} 秒后再试")

        user_id = int(row["id"])
        code = generate_email_code(6)
        code_h = hash_email_code(code)
        expires_at = datetime.now(UTC) + timedelta(minutes=10)
        db.execute(
            "update email_verifications set used_at = now() "
            "where lower(email) = %s and purpose = 'login' and used_at is null",
            (email_norm,),
        )
        db.execute(
            """
            insert into email_verifications
              (email, code_hash, user_id, purpose, expires_at, ip, ua)
            values (%s, %s, %s, 'login', %s, %s, %s)
            """,
            (email_norm, code_h, user_id, expires_at, ip or "", ua or ""),
        )

    from .email import EmailSendError, send_login_code_email
    try:
        send_login_code_email(email_norm, code)
    except EmailSendError:
        _log.warning("send_login_code_email failed (RESEND unconfigured?)")  # SEC(M-10): 不记明文验证码

    return {"ok": True, "pending_verify": True, "email_mask": _mask_email(email_norm)}


def confirm_login_code(email: str, code: str, *, ip: str = "") -> tuple[dict[str, Any], str]:
    email_norm = normalize_email(email)
    if not email_norm or "@" not in email_norm:
        raise ValueError("请填写有效的邮箱地址")
    code = (code or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError("请输入 6 位数字验证码")
    _check_rate_limit(ip, email_norm)

    init_db()
    with connect() as db:
        verif = db.execute(
            """
            select * from email_verifications
            where lower(email) = %s
              and purpose = 'login'
              and used_at is null
              and expires_at > now()
            order by created_at desc
            limit 1
            """,
            (email_norm,),
        ).fetchone()
        if not verif or not verify_email_code(code, verif["code_hash"]):
            _record_login_fail(ip, email_norm)
            raise ValueError("验证码错误或已过期")

        row = db.execute(
            """
            select * from users
            where id = %s
              and lower(email) = %s
              and email_verified = true
              and deactivated_at is null
            limit 1
            """,
            (verif["user_id"], email_norm),
        ).fetchone()
        if not row:
            _record_login_fail(ip, email_norm)
            raise ValueError("验证码错误或已过期")

        token = _issue_session(db, int(row["id"]))
        db.execute("update email_verifications set used_at = now() where id = %s", (verif["id"],))
        _record_login_success(ip, email_norm)
        return dict(row), token


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
        expires_at = datetime.now(UTC) + timedelta(days=SESSION_DAYS)

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

    # Redis 共享冷却(workers>1 一致;否则用户轮询不同 worker 可绕过 60s 冷却刷验证码)
    import redis_bus
    if redis_bus.get_sync_client() is not None:
        rem = redis_bus.lock_remaining(f"resend:{email_norm}")
        if rem and rem > 0:
            raise ValueError(f"发送太频繁，请 {rem} 秒后再试")
        redis_bus.lock_set(f"resend:{email_norm}", 60)
    else:
        now = time.monotonic()
        last = _RESEND_LAST.get(email_norm, 0.0)
        if now - last < 60:
            wait = int(60 - (now - last)) + 1
            raise ValueError(f"发送太频繁，请 {wait} 秒后再试")
        _RESEND_LAST[email_norm] = now

    init_db()
    with connect() as db:
        # SEC(H-7): pending 只从 Redis/进程内取(不再从 ua 列恢复 password_hash)。
        pending = _decode_pending_register(_pending_store_get(email_norm))
        if not pending:
            raise ValueError("注册会话已过期，请重新注册")

        pending_json = _encode_pending_register(pending)
        _pending_store_set(email_norm, pending_json)

        # 废弃旧记录，发新验证码
        db.execute(
            "update email_verifications set used_at = now() where lower(email) = %s and used_at is null and purpose = 'register'",
            (email_norm,),
        )
        code = generate_email_code(6)
        code_h = hash_email_code(code)
        from datetime import timedelta as _td
        expires_at = datetime.now(UTC) + _td(minutes=10)
        db.execute(
            "insert into email_verifications (email, code_hash, purpose, expires_at, ip, ua) values (%s, %s, 'register', %s, %s, %s)",
            # SEC(H-7): ua 列存真实 user-agent,不再塞含 password_hash 的 pending_json。
            (email_norm, code_h, expires_at, ip or "", str(pending.get("ua") or "")[:512]),
        )

    from .email import EmailSendError, send_verification_email
    try:
        send_verification_email(email_norm, code)
    except EmailSendError:
        _log.warning("resend_verification_code: email send failed for %s", email_norm)  # SEC(M-10)


# ── 密码重置（忘记密码）────────────────────────────────────────────────────────
#
# 复用 email_verifications 表（purpose='password_reset'），不新建独立表。
# 字段映射: email / code_hash(存 token HMAC) / expires_at / used_at / user_id
# user_id 列在 email_verifications 是否存在需运行时检查；若无则只用 email 关联。
#
_RESET_RATE: dict[str, float] = {}   # email_norm → 最近触发时间（防 spam）
_RESET_RATE_LOCK = threading.Lock()
_RESET_MAX_PER_10MIN = 3             # 每邮箱 10 分钟内最多 3 次请求


def _check_reset_rate(email_norm: str) -> None:
    """超过频率限制时抛 ValueError（调用方展示通用 ok 以防枚举）。"""
    _interval = int(600 / _RESET_MAX_PER_10MIN)  # 每次请求间隔 ≥200s
    # Redis 共享冷却(workers>1 一致;否则轮询不同 worker 可绕过重置限流刷邮件)
    import redis_bus
    if redis_bus.get_sync_client() is not None:
        if (redis_bus.lock_remaining(f"reset:{email_norm}") or 0) > 0:
            raise ValueError("rate_limited")
        redis_bus.lock_set(f"reset:{email_norm}", _interval)
        return
    now = time.monotonic()
    key = f"r:{email_norm}"
    with _RESET_RATE_LOCK:
        if key not in _RESET_RATE:
            _RESET_RATE[key] = now
            return
        elapsed = now - _RESET_RATE[key]
        if elapsed < _interval:  # 简单滑动阈值
            raise ValueError("rate_limited")
        _RESET_RATE[key] = now


def request_password_reset(email: str, ip: str = "") -> dict:
    """触发密码重置邮件。

    - 不论 email 是否存在都返回 {'ok': True}（防枚举攻击）。
    - 若 email 存在且已验证：写 email_verifications(purpose='password_reset')
      并发送重置链接邮件。
    - token TTL 30 分钟。
    """
    email_norm = normalize_email(email)
    if not email_norm or "@" not in email_norm:
        return {"ok": True}   # 静默，防枚举

    try:
        _check_reset_rate(email_norm)
    except ValueError:
        return {"ok": True}   # 限流也静默

    init_db()
    with connect() as db:
        row = db.execute(
            "SELECT id FROM users WHERE LOWER(email) = %s AND email_verified = true AND deactivated_at IS NULL LIMIT 1",
            (email_norm,),
        ).fetchone()
        if not row:
            return {"ok": True}   # 邮箱不存在，静默

        row["id"]
        token = secrets.token_urlsafe(32)
        token_hash = hash_email_code(token)   # 复用已有 HMAC util
        expires_at = datetime.now(UTC) + timedelta(minutes=30)

        # 废弃同邮箱的旧 password_reset 记录
        db.execute(
            "UPDATE email_verifications SET used_at = NOW() "
            "WHERE LOWER(email) = %s AND purpose = 'password_reset' AND used_at IS NULL",
            (email_norm,),
        )
        # 写新记录（兼容有/无 user_id 列两种 schema）
        try:
            db.execute(
                "INSERT INTO email_verifications (email, code_hash, purpose, expires_at, ip) "
                "VALUES (%s, %s, 'password_reset', %s, %s)",
                (email_norm, token_hash, expires_at, ip or ""),
            )
        except Exception:
            _log.warning("request_password_reset: insert failed for %s", email_norm, exc_info=True)
            return {"ok": True}

    from .email import EmailSendError, send_password_reset_email
    try:
        send_password_reset_email(email_norm, token)
    except EmailSendError:
        _log.warning("request_password_reset: send email failed (RESEND unconfigured?)")

    return {"ok": True}


def consume_magic_token(token: str, email: str) -> dict:
    """校验 magic_token 与 email 匹配，30天有效。不立即消费，verify_passwordless_and_login 时再标 used。"""
    if not token or not email:
        raise ValueError("token/email 不能为空")
    norm = normalize_email(email)
    if not norm or "@" not in norm:
        raise ValueError("邮箱格式不正确")
    init_db()
    with connect() as db:
        # SEC(H-6): 加 used_at is null —— magic 邀请单次使用,登录成功后即失效,杜绝 30 天内重放。
        row = db.execute(
            "select email_norm, magic_token, batch, created_at, used_by_user_id from registration_allowlist "
            "where magic_token = %s and email_norm = %s and used_at is null",
            (token, norm),
        ).fetchone()
    if not row:
        raise ValueError("邀请链接无效、已过期或已被使用")
    created = row["created_at"]
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    age = (datetime.now(UTC) - created).total_seconds()
    if age > 30 * 86400:
        raise ValueError("邀请链接已过期 (30天)")
    return {"email": norm, "batch": row["batch"]}


def request_passwordless_code(email: str, source: str = "magic_link") -> dict:
    """对 email 发 6位 OTP。无论用户是否已注册都生效(allowlist 已在 consume_magic_token 验过)。"""
    email_norm = normalize_email(email)
    if not email_norm or "@" not in email_norm:
        raise ValueError("邮箱格式不正确")
    init_db()
    with connect() as db:
        # 废弃同邮箱旧 passwordless_login 记录
        db.execute(
            "update email_verifications set used_at = now() "
            "where lower(email) = %s and purpose = 'passwordless_login' and used_at is null",
            (email_norm,),
        )
        code = generate_email_code(6)
        code_h = hash_email_code(code)
        expires_at = datetime.now(UTC) + timedelta(minutes=10)
        db.execute(
            """
            insert into email_verifications
              (email, code_hash, purpose, expires_at, ua)
            values (%s, %s, 'passwordless_login', %s, %s)
            """,
            (email_norm, code_h, expires_at, source),
        )
    from .email import EmailSendError, send_login_code_email
    try:
        send_login_code_email(email_norm, code)
    except EmailSendError:
        _log.warning("request_passwordless_code: send email failed for %s", email_norm)  # SEC(M-10)
    return {"ok": True}


def verify_passwordless_and_login(email: str, code: str, ip: str = "") -> dict:
    """验证 OTP(purpose='passwordless_login')，若用户不存在则按白名单建 user，返回 session token。"""
    email_norm = normalize_email(email)
    if not email_norm or "@" not in email_norm:
        raise ValueError("邮箱格式不正确")
    code = (code or "").strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError("请输入 6 位数字验证码")
    _check_rate_limit(ip, email_norm)
    init_db()
    with connect() as db:
        verif = db.execute(
            """
            select * from email_verifications
            where lower(email) = %s
              and purpose = 'passwordless_login'
              and used_at is null
              and expires_at > now()
            order by created_at desc
            limit 1
            """,
            (email_norm,),
        ).fetchone()
        if not verif or not verify_email_code(code, verif["code_hash"]):
            _record_login_fail(ip, email_norm)
            raise ValueError("验证码错误或已过期")

        # 查是否已有用户
        user_row = db.execute(
            """
            select * from users
            where lower(email) = %s and deactivated_at is null
            limit 1
            """,
            (email_norm,),
        ).fetchone()

        if user_row is None:
            # 未注册：必须在白名单里
            wl_row = db.execute(
                "select email_norm, batch from registration_allowlist where email_norm = %s",
                (email_norm,),
            ).fetchone()
            if not wl_row:
                _record_login_fail(ip, email_norm)
                raise ValueError("该邮箱不在注册白名单中，请确认邀请链接正确")
            # 建 user (password_hash = NULL — passwordless 账号)
            try:
                user_row = db.execute(
                    """
                    insert into users (
                      username, password_hash, display_name, role,
                      email, email_verified, email_verified_at,
                      terms_accepted_at, age_confirmed
                    )
                    values (%s, NULL, %s, 'user', %s, true, now(), now(), true)
                    returning *
                    """,
                    (email_norm, email_norm, email_norm),
                ).fetchone()
            except UniqueViolation as err:
                # 极端竞态：刚才建好了，重查
                user_row = db.execute(
                    "select * from users where lower(email) = %s and deactivated_at is null limit 1",
                    (email_norm,),
                ).fetchone()
                if not user_row:
                    raise ValueError("注册失败，请稍后重试") from err
            # 标记白名单 used
            db.execute(
                "update registration_allowlist set used_by_user_id = %s, used_at = now() where email_norm = %s",
                (user_row["id"], email_norm),
            )

        user = dict(user_row)
        token = _issue_session(db, int(user["id"]))
        db.execute("update email_verifications set used_at = now() where id = %s", (verif["id"],))
        _record_login_success(ip, email_norm)

    needs_profile = not bool((user.get("username") or "").strip()) or user.get("username") == user.get("email")
    return {
        "user_id": user["id"],
        "username": user.get("username") or "",
        "needs_profile": needs_profile,
        "session_token": token,
    }


def login_via_magic_token(email: str, ip: str = "") -> dict:
    """task: magic link 直接登录(不发 OTP,token + email 匹配本身即认证)。

    consume_magic_token 已校验 token + email + 30 天有效期,这里直接:
    1. 查/建 user(未注册 → 按白名单建 passwordless 账号)
    2. 标记 allowlist used_at
    3. _issue_session → 返 session_token + needs_profile

    跟 verify_passwordless_and_login 的差异:跳过 OTP 校验(magic_token 已是认证)。
    """
    email_norm = normalize_email(email)
    if not email_norm or "@" not in email_norm:
        raise ValueError("邮箱格式不正确")
    _check_rate_limit(ip, email_norm)
    init_db()
    with connect() as db:
        user_row = db.execute(
            "select * from users where lower(email) = %s and deactivated_at is null limit 1",
            (email_norm,),
        ).fetchone()
        if user_row is None:
            # 未注册:必须在白名单(magic token 已通过 consume_magic_token 校验,这里二次保险)
            wl_row = db.execute(
                "select email_norm, batch from registration_allowlist where email_norm = %s",
                (email_norm,),
            ).fetchone()
            if not wl_row:
                _record_login_fail(ip, email_norm)
                raise ValueError("该邮箱不在注册白名单中,请确认邀请链接正确")
            # 建 passwordless 账号
            try:
                user_row = db.execute(
                    """
                    insert into users (
                      username, password_hash, display_name, role,
                      email, email_verified, email_verified_at,
                      terms_accepted_at, age_confirmed
                    )
                    values (%s, NULL, %s, 'user', %s, true, now(), now(), true)
                    returning *
                    """,
                    (email_norm, email_norm, email_norm),
                ).fetchone()
            except UniqueViolation:
                user_row = db.execute(
                    "select * from users where lower(email) = %s and deactivated_at is null limit 1",
                    (email_norm,),
                ).fetchone()
                if not user_row:
                    raise ValueError("注册失败,请稍后重试") from None
            db.execute(
                "update registration_allowlist set used_by_user_id = %s, used_at = now() where email_norm = %s",
                (user_row["id"], email_norm),
            )
        user = dict(user_row)
        # SEC(H-6): 标记 magic 邀请为已用(已注册用户分支过去从不标 → 30 天内可无限重放)。
        # 幂等(used_at is null 才更新),配合 consume_magic_token 的 used_at is null 实现单次使用。
        db.execute(
            "update registration_allowlist set used_by_user_id = %s, used_at = now() "
            "where email_norm = %s and used_at is null",
            (int(user["id"]), email_norm),
        )
        token = _issue_session(db, int(user["id"]))
        _record_login_success(ip, email_norm)

    needs_profile = not bool((user.get("username") or "").strip()) or user.get("username") == user.get("email")
    return {
        "user_id": user["id"],
        "username": user.get("username") or "",
        "needs_profile": needs_profile,
        "session_token": token,
    }


def confirm_password_reset(token: str, new_password: str, ip: str = "") -> dict:
    """验证重置 token，更新密码。

    Raises:
        ValueError: token 无效 / 已过期 / 已使用
        ValueError: 新密码不符合策略
    """
    if not token:
        raise ValueError("invalid_token")
    if len(new_password or "") < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    if len(new_password or "") > 1024:
        raise ValueError("密码超长")

    # SEC(L-2): per-IP 软上限,防对 reset token 的零摩擦探测 / DoS 放大(每次探针触发全表扫 + HMAC)。
    try:
        import redis_bus as _rb
        _c = _rb.rate_incr(f"pwreset:{ip or '-'}", 600)
        if _c and _c > 30:
            raise ValueError("尝试过于频繁,请稍后再试")
    except ValueError:
        raise
    except Exception:
        pass

    token_hash = hash_email_code(token)
    init_db()
    with connect() as db:
        verif = db.execute(
            "SELECT id, email, used_at FROM email_verifications "
            "WHERE code_hash = %s AND purpose = 'password_reset' AND expires_at > NOW() "
            "LIMIT 1",
            (token_hash,),
        ).fetchone()
        if not verif:
            raise ValueError("重置链接无效或已过期，请重新申请")
        if verif["used_at"]:
            raise ValueError("该重置链接已使用过，请重新申请")

        # 查找对应用户
        email_norm = verif["email"]
        user = db.execute(
            "SELECT id FROM users WHERE LOWER(email) = %s AND email_verified = true AND deactivated_at IS NULL LIMIT 1",
            (email_norm,),
        ).fetchone()
        if not user:
            raise ValueError("账号不存在或已被禁用")

        pw_hash = hash_password(new_password)
        db.execute("UPDATE users SET password_hash = %s, updated_at = NOW() WHERE id = %s",
                   (pw_hash, user["id"]))
        db.execute("UPDATE email_verifications SET used_at = NOW() WHERE id = %s",
                   (verif["id"],))
        # 安全：重置密码后废除所有旧 session
        db.execute("DELETE FROM sessions WHERE user_id = %s", (user["id"],))

    _log.info("password_reset: user_id=%s ip=%s", user["id"], ip)
    return {"ok": True}
