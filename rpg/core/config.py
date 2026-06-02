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

# ── 安全 / 密钥 ──────────────────────────────────────────────────────────
def master_key() -> str | None:
    return os.getenv("RPG_MASTER_KEY")

def admin_password() -> str | None:
    return os.getenv("RPG_ADMIN_PASSWORD")

def setup_token() -> str | None:
    """一次性首管理员引导令牌。server 模式下,首次注册须携带与此匹配的令牌才授予 admin。"""
    return os.getenv("RPG_SETUP_TOKEN")


# ── 部署模式规范化 ────────────────────────────────────────────────────────
# 部署模式只有两个规范值:"local" 和 "server"。
#   local  → 单用户本地/桌面:免鉴权、file 存储、放开危险工具(skill 导入 / MCP 写盘)。
#   server → 多用户服务器:强制鉴权、db 存储、危险工具默认关闭。
# 兼容旧别名(desktop/self_hosted 归 local;production/prod/cloud 归 server)。
# 关键安全语义:除已知 local 别名外,一切(含未设/未知值)都判定为 server(fail-closed)。
_LOCAL_ALIASES = {"local", "desktop", "self_hosted", "self-hosted"}


def is_local_mode() -> bool:
    """部署是否为本地/单用户家族。非 local 别名一律视为 server(fail-closed)。"""
    return deployment_mode().strip().lower() in _LOCAL_ALIASES


def is_server_mode() -> bool:
    """is_local_mode 的反面;服务器/多用户家族。"""
    return not is_local_mode()


def effective_auth_required() -> bool:
    """是否强制鉴权(集中一处供 register / schema / _deps 等使用)。

    优先级:RPG_REQUIRE_AUTH=1/0 显式覆盖 → 否则按部署模式(server 强制 / local 不强制)。
    """
    explicit = require_auth_raw().strip()
    if explicit == "1":
        return True
    if explicit == "0":
        return False
    return is_server_mode()

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
