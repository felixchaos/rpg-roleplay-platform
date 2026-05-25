from __future__ import annotations

import hashlib
import secrets


def normalize_username(username: str) -> str:
    return "".join(ch for ch in (username or "").strip().lower() if ch.isalnum() or ch in "_-.")[:48]


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 180_000).hex()
    return f"pbkdf2_sha256${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 180_000).hex()
    return secrets.compare_digest(check, digest)


def public_user(user: dict | None) -> dict | None:
    if not user:
        return None
    out = {k: user[k] for k in ("id", "public_id", "username", "display_name", "bio", "role", "created_at", "updated_at", "row_version") if k in user}
    if out.get("public_id") is not None:
        out["uid"] = str(out["public_id"])
    return out
