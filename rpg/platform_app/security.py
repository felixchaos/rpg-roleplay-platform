from __future__ import annotations

import hashlib
import hmac
import os
import secrets

# ── Argon2id (REG-01 / ENC-08) ───────────────────────────────────────────────
try:
    from argon2 import PasswordHasher
    _ph = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=1)  # OWASP 2023
    _ARGON2_AVAILABLE = True
except ImportError:
    _ARGON2_AVAILABLE = False
    _ph = None  # type: ignore[assignment]


def normalize_username(username: str) -> str:
    return "".join(ch for ch in (username or "").strip().lower() if ch.isalnum() or ch in "_-.")[:48]


# ── Legacy PBKDF2 (内部) ──────────────────────────────────────────────────────

def _verify_pbkdf2(stored: str, plaintext: str) -> bool:
    """校验老 PBKDF2 哈希。被 verify_password 和 auth._verify_pbkdf2 共用。"""
    try:
        algo, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    check = hashlib.pbkdf2_hmac("sha256", plaintext.encode("utf-8"), salt.encode("utf-8"), 180_000).hex()
    return secrets.compare_digest(check, digest)


def hash_password(password: str) -> str:
    """新账号优先用 Argon2id；argon2-cffi 未安装时退回 PBKDF2。"""
    if _ARGON2_AVAILABLE and _ph is not None:
        return _ph.hash(password)
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 180_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


# 为向后兼容 auth.py import hash_password_argon2 的旧代码名称
hash_password_argon2 = hash_password


def verify_password_with_rehash(stored: str | None, plaintext: str) -> tuple[bool, bool]:
    """返回 (verified, needs_rehash).

    needs_rehash=True 表示存储的是老 PBKDF2 且验证通过 — 登录后应升级为 Argon2id。
    """
    if not stored:
        return False, False
    if stored.startswith("$argon2") and _ARGON2_AVAILABLE and _ph is not None:
        try:
            _ph.verify(stored, plaintext)
            return True, _ph.check_needs_rehash(stored)
        except Exception:
            return False, False
    elif stored.startswith("pbkdf2"):
        ok = _verify_pbkdf2(stored, plaintext)
        return ok, ok  # 通过则需要 rehash
    else:
        return False, False


def verify_password(password: str, stored: str) -> bool:
    """向后兼容的简单验证 — 供 login() 外部直接调用。"""
    ok, _ = verify_password_with_rehash(stored, password)
    return ok


# ── Email 规范化 (REG-03) ──────────────────────────────────────────────────────

def normalize_email(raw: str) -> str:
    """RFC 5322 风格小写 + 去 +tag。防 +tag 重注册绕过 REG-03。"""
    raw = (raw or "").strip().lower()
    if "@" not in raw:
        return raw
    local, domain = raw.split("@", 1)
    if "+" in local:
        local = local.split("+", 1)[0]
    return f"{local}@{domain}"


# ── 验证码生成 / HMAC (REG-02) ───────────────────────────────────────────────

def generate_email_code(n_digits: int = 6) -> str:
    """生成 n 位密码学安全数字验证码。"""
    return "".join(secrets.choice("0123456789") for _ in range(n_digits))


def _email_server_secret() -> bytes:
    """从环境变量读取 HMAC 密钥；缺失时退回随机密钥(仅单进程有效,重启失效)。"""
    key = os.environ.get("EMAIL_CODE_SECRET", "")
    if key:
        return key.encode()
    # fallback: 进程级随机 key（无 EMAIL_CODE_SECRET 时验证码不跨进程）
    global _PROC_SECRET
    if "_PROC_SECRET" not in globals():
        _PROC_SECRET = secrets.token_bytes(32)
    return _PROC_SECRET  # type: ignore[return-value]


def hash_email_code(code: str, server_secret: bytes | None = None) -> str:
    """HMAC-SHA256(server_secret, code) — 存 DB 的哈希值。"""
    secret = server_secret if server_secret is not None else _email_server_secret()
    return hmac.new(secret, code.encode(), hashlib.sha256).hexdigest()


def verify_email_code(code: str, stored_hash: str, server_secret: bytes | None = None) -> bool:
    """恒定时间比对验证码哈希。"""
    expected = hash_email_code(code, server_secret)
    return secrets.compare_digest(expected, stored_hash)


# ── 年龄计算 (AGE-01) ─────────────────────────────────────────────────────────

def calc_age(birthday, today=None) -> int:
    """birthday 是 date 对象,返回完整周岁。"""
    from datetime import date as _date
    today = today or _date.today()
    return (today.year - birthday.year
            - ((today.month, today.day) < (birthday.month, birthday.day)))


# ── Public profile ─────────────────────────────────────────────────────────────

def public_user(user: dict | None, db=None) -> dict | None:
    """返回用户的公开安全字段。

    db: 可选 psycopg 连接。传入时额外派生 is_co_builder（从 registration_allowlist join）。
    不传 db 时 is_co_builder=False（用于不需要该字段的调用路径）。
    """
    if not user:
        return None
    out = {k: user[k] for k in ("id", "public_id", "username", "display_name", "bio", "role", "created_at", "updated_at", "row_version", "welcome_dismissed_at") if k in user}
    if out.get("public_id") is not None:
        out["uid"] = str(out["public_id"])
    out["has_password"] = bool(user.get("password_hash"))
    # co_builder_opt_out 直接从 user 行读
    out["co_builder_opt_out"] = bool(user.get("co_builder_opt_out", False))
    # is_co_builder: 派生字段，需要 join registration_allowlist
    if db is not None and user.get("id") is not None:
        row = db.execute(
            "select 1 from registration_allowlist where used_by_user_id = %s limit 1",
            (user["id"],),
        ).fetchone()
        out["is_co_builder"] = row is not None
    else:
        out["is_co_builder"] = False
    return out
