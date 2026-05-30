"""core.config — 项目配置加载入口。

汇集分散在各处的 os.getenv 调用,提供类型化访问。
"""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv_once() -> None:
    """加载项目根目录 .env (rpg/ 的上一级)。幂等。"""
    try:
        from dotenv import load_dotenv
        # core/config.py 在 rpg/core/ 下，.env 在 rpg 的上一级
        # parent = rpg/core，parent.parent = rpg，parent.parent.parent = 项目根
        # override=True: 让 .env 覆盖 shell 已设置的空值
        # (Claude Code CLI / 某些 shell 会 export ANTHROPIC_API_KEY= 空字符串,
        #  python-dotenv 默认 override=False 会保留空值,导致 .env 里的真 key 被忽略)
        load_dotenv(Path(__file__).parent.parent.parent / ".env", override=True)
    except ImportError:
        pass


# ── 部署模式 / 鉴权 ──────────────────────────────────────────────────────
def deployment_mode() -> str:
    return os.getenv("RPG_DEPLOYMENT_MODE", "local")

def require_auth() -> bool:
    return os.getenv("RPG_REQUIRE_AUTH", "0") == "1"

def require_auth_raw() -> str:
    """返回 RPG_REQUIRE_AUTH 原始字符串（含空字符串），供需要三态判断的地方使用。"""
    return os.getenv("RPG_REQUIRE_AUTH", "")

def debug_ui() -> bool:
    return bool(os.getenv("RPG_DEBUG_UI"))

# ── 网络 ─────────────────────────────────────────────────────────────────
def cors_origins() -> str | None:
    return os.getenv("RPG_CORS_ORIGINS")

def cors_origins_with_default(default: str) -> str:
    return os.getenv("RPG_CORS_ORIGINS", default)

def cors_max_age() -> int:
    return int(os.getenv("RPG_CORS_MAX_AGE", "86400"))

def gzip_min_bytes() -> int:
    return int(os.getenv("RPG_GZIP_MIN_BYTES", "1024"))

def trusted_proxies() -> str | None:
    return os.getenv("RPG_TRUSTED_PROXIES")

def trusted_proxies_raw() -> str:
    return os.getenv("RPG_TRUSTED_PROXIES", "")

# ── Cookie ───────────────────────────────────────────────────────────────
def cookie_secure() -> str | None:
    return os.getenv("RPG_COOKIE_SECURE")

def cookie_samesite() -> str:
    return os.getenv("RPG_COOKIE_SAMESITE", "lax")

# ── 安全 / 密钥 ──────────────────────────────────────────────────────────
def master_key() -> str | None:
    return os.getenv("RPG_MASTER_KEY")

def admin_password() -> str | None:
    return os.getenv("RPG_ADMIN_PASSWORD")

def setup_token() -> str | None:
    """一次性首管理员引导令牌。server 模式下,首次注册须携带与此匹配的令牌才授予 admin。"""
    return os.getenv("RPG_SETUP_TOKEN")


# 部署模式集合(与 app.py 保持一致;未知模式 fail-closed)
_SERVER_MODES = {"server", "production", "prod", "cloud"}
_LOCAL_MODES = {"local", "desktop", "self_hosted", "self-hosted"}


def effective_auth_required() -> bool:
    """是否强制鉴权(等价 app.py:_api_auth_required,集中一处供 register 等使用)。

    优先级:RPG_REQUIRE_AUTH=1/0 → RPG_DEPLOYMENT_MODE(server/local) → 未知模式 fail-closed。
    """
    explicit = require_auth_raw().strip()
    if explicit == "1":
        return True
    if explicit == "0":
        return False
    mode = deployment_mode().strip().lower()
    if mode in _SERVER_MODES:
        return True
    if mode in _LOCAL_MODES:
        return False
    return True

# ── 应用标题 ─────────────────────────────────────────────────────────────
def app_title() -> str:
    return os.getenv("RPG_APP_TITLE", "RPG Roleplay")

# ── 运行时 backend ───────────────────────────────────────────────────────
def runtime_backend() -> str:
    return os.getenv("RPG_RUNTIME_BACKEND", "auto")

# ── DB 连接池 ────────────────────────────────────────────────────────────
def db_pool_min() -> int:
    return int(os.getenv("RPG_DB_POOL_MIN", "1"))

def db_pool_max() -> int:
    return int(os.getenv("RPG_DB_POOL_MAX", "10"))

def db_pool_timeout() -> float:
    return float(os.getenv("RPG_DB_POOL_TIMEOUT", "8"))

def database_url_override() -> str | None:
    return os.getenv("RPG_DATABASE_URL")

# ── Migration ────────────────────────────────────────────────────────────
def migration_lock_timeout_ms() -> int:
    return int(os.getenv("RPG_MIGRATION_LOCK_TIMEOUT_MS", "30000"))

def skip_auto_migrate() -> bool:
    return os.getenv("RPG_SKIP_AUTO_MIGRATE") == "1"

# ── Auth / 速率限制 ──────────────────────────────────────────────────────
def min_password_length() -> int:
    return int(os.getenv("RPG_MIN_PASSWORD_LENGTH", "8"))

def login_max_fails() -> int:
    return int(os.getenv("RPG_LOGIN_MAX_FAILS", "5"))

def login_lockout_sec() -> int:
    return int(os.getenv("RPG_LOGIN_LOCKOUT_SEC", "60"))

def login_window_sec() -> int:
    return int(os.getenv("RPG_LOGIN_WINDOW_SEC", "300"))

# ── 脚本上传 ─────────────────────────────────────────────────────────────
def script_upload_max_bytes() -> int:
    return int(os.getenv("RPG_SCRIPT_UPLOAD_MAX_BYTES", str(128 * 1024 * 1024)))

def upload_chunk_max_bytes() -> int:
    return int(os.getenv("RPG_UPLOAD_CHUNK_MAX_BYTES", str(8 * 1024 * 1024)))

def sync_stale_running_seconds() -> int:
    return int(os.getenv("RPG_SYNC_STALE_RUNNING_SECONDS", "1800"))

def sync_heartbeat_seconds() -> int:
    return int(os.getenv("RPG_SYNC_HEARTBEAT_SECONDS", "60"))

# ── 集群 ─────────────────────────────────────────────────────────────────
def state_cache_ttl() -> int:
    return int(os.getenv("RPG_STATE_CACHE_TTL", "5"))

# ── Tools DSL ────────────────────────────────────────────────────────────
def enable_skill_import() -> str | None:
    return os.getenv("RPG_ENABLE_SKILL_IMPORT")

def enable_mcp_config_write() -> str | None:
    return os.getenv("RPG_ENABLE_MCP_CONFIG_WRITE")

# ── Phase manager ────────────────────────────────────────────────────────
def phase_turn_threshold() -> int:
    return int(os.getenv("RPG_PHASE_TURN_THRESHOLD", "30"))


# ── 黑天鹅子代理 (sprint 5) ────────────────────────────────────────────
def enable_black_swan() -> bool:
    """是否启用 BlackSwanAgent post-GM hook。默认关闭,需 RPG_ENABLE_BLACK_SWAN=1。"""
    return os.getenv("RPG_ENABLE_BLACK_SWAN", "0") == "1"
