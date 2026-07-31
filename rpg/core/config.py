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
        # .env 可能在仓库根 (生产) 或 rpg/.env (本地 setup.sh 写的位置)。两处都试,
        # 缺失一侧是无害空操作;rpg/.env 后加载,本地优先生效。
        load_dotenv(Path(__file__).parent.parent.parent / ".env", override=True)
        load_dotenv(Path(__file__).parent.parent / ".env", override=True)
    except ImportError:
        pass


# ── 部署模式 / 鉴权 ──────────────────────────────────────────────────────
def deployment_mode() -> str:
    return os.getenv("RPG_DEPLOYMENT_MODE", "local")

def require_auth() -> bool:
    """是否强制鉴权。统一委托 effective_auth_required()(认 RPG_DEPLOYMENT_MODE=server)。

    旧实现只看 RPG_REQUIRE_AUTH=="1",于是 server 模式(未显式设 RPG_REQUIRE_AUTH=1)下
    所有以本函数为闸的门控都误判成「无鉴权」:
      - 模型目录 has_credential 走服务器级凭证(env key / Vertex SA)而非 per-user 账号 key;
      - vertex 全局 SA fallback 没被禁(LLM 路径被服务器 SA 兜底);
      - get_credential 回退到服务器 env key 当作用户凭证(anthropic 泄漏);
      - base_url http 校验、注册邮箱验证被跳过(server 注册免验证)。
    委托给 mode-aware 的 effective_auth_required() 后,server 模式一律按强制鉴权处理,
    本地/桌面模式仍为 False(行为不变);RPG_REQUIRE_AUTH=1/0 显式覆盖仍优先生效。
    """
    return effective_auth_required()

def require_auth_raw() -> str:
    """返回 RPG_REQUIRE_AUTH 原始字符串（含空字符串），供需要三态判断的地方使用。"""
    return os.getenv("RPG_REQUIRE_AUTH", "")

def debug_ui() -> bool:
    return bool(os.getenv("RPG_DEBUG_UI"))

def tiered_tools_enabled() -> bool:
    """阶梯化工具加载:窗口外的工具不直接塞 schema,而是进「目录」由模型 load_tools 按需加载。
    默认开;RPG_TIERED_TOOLS=0 关闭 → 退回旧的「前 N 个直接发、其余丢弃」截断行为。"""
    return os.getenv("RPG_TIERED_TOOLS", "1") != "0"

def tool_window_size() -> int:
    """每轮直接塞进 LLM tools 数组的「完整 schema」工具数(窗口)。窗口外的进 load_tools 目录。

    窗口越小,每轮工具 token 越省(91 个全发约 9.5k token);但窗口外工具要多一次 load 往返。
    chat_tool_router 已按 _rank 排序(酒馆自管理 set_tavern_*/tavern_* 排最前 → 永远在窗口内),
    常用读取工具与锚点族次之,其余(建卡/生图等)按需加载。RPG_TOOL_WINDOW 覆盖。

    默认 16 → 18(v1.72.4):窗口是**硬名额**,不是软优先级——挤出去的工具实测等于消失
    (模型极少主动 load,见 chat_tool_router._ANCHOR_FAMILY 的两次前科)。锚点族补进
    record_history_anchor / list_recent_history 后,rank 0 占 10 个;酒馆模式还有 5 个
    rank -1 自管理工具,16 会把 get_current_scene / get_game_state 一并挤出窗口。18 让
    改动**只增不减**(改前直发的工具一个不少),代价约 +2 条 schema ≈ 400 token/轮。
    再往族里加工具前先算这笔账:窗口需 ≥ 5(酒馆) + rank0 个数 + 3(核心 get_*)。"""
    try:
        n = int(os.getenv("RPG_TOOL_WINDOW", "18"))
    except (TypeError, ValueError):
        n = 18
    return max(1, n)

# ── 记忆桶洪泛闸(state/memory_budget.py 的两条阈值,详见该模块头部实测数据)─────
def memory_append_per_turn_max() -> int:
    """GM 单回合往同一个记忆桶最多追加几条。0 = 关闭该闸。

    默认 6:正常节奏是每回合往一个桶追加 1-2 条,超过 6 的基本只有「同一件事被逐条拆写」
    这一种形态。RPG_MEMORY_APPEND_PER_TURN 覆盖。"""
    try:
        n = int(os.getenv("RPG_MEMORY_APPEND_PER_TURN", "6"))
    except (TypeError, ValueError):
        n = 6
    return max(0, n)

def memory_family_max() -> int:
    """同一「族名」(结构前缀)在 abilities/resources 里最多几条。0 = 关闭该闸。

    默认 4:同族攒到 5 条以上的,实际见到的形态就两类 —— 一门功法被逐窍拆写(群反馈来源),
    和以「新发现」开头的资源流水账。RPG_MEMORY_FAMILY_MAX 覆盖。"""
    try:
        n = int(os.getenv("RPG_MEMORY_FAMILY_MAX", "4"))
    except (TypeError, ValueError):
        n = 4
    return max(0, n)

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
# 本地/自托管部署模式权威集合(单一真源)。散落各处的
# {"local","desktop","self_hosted","self-hosted"} 字面量统一引用它。
LOCAL_MODES = frozenset({"local", "desktop", "self_hosted", "self-hosted"})
_LOCAL_ALIASES = LOCAL_MODES


def is_local_mode() -> bool:
    """部署是否为本地/单用户家族。非 local 别名一律视为 server(fail-closed)。"""
    return deployment_mode().strip().lower() in _LOCAL_ALIASES


def is_server_mode() -> bool:
    """is_local_mode 的反面;服务器/多用户家族。"""
    return not is_local_mode()

def deployment_mode_normalized() -> str:
    """归一化后的部署模式:strip + lower,空值兜底 "local"。

    散落在 app.py / core.startup 的 _deployment_mode() 统一委托本函数。
    """
    return deployment_mode().strip().lower() or "local"


def is_local_deployment_mode(mode: str | None = None) -> bool:
    """给定模式是否属于本地/自托管集合;mode 为 None 时自查当前部署模式。"""
    if mode is None:
        mode = deployment_mode_normalized()
    return mode in LOCAL_MODES


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

# ── LLM 请求超时 ─────────────────────────────────────────────────────────
def llm_timeout_seconds(user_id: int | None = None) -> float:
    """LLM 请求读超时(秒)。单一来源,供 GM 各后端 + 子代理 harness 共用。

    本地大模型(如桌面自托管跑千问/Qwen,纯 CPU/内存硬跑)首 token 常要几分钟,
    300s 会被中途切断。故:
      优先链 = 用户 settings.request_timeout(UI 可调) > env RPG_GM_TIMEOUT > 部署默认。
      部署默认 = 本地/桌面 1800s,服务器 300s。
      上下限 = 本地 [30, 7200],服务器 [30, 900](多用户下不让单请求把 worker 占死)。
    """
    # 自包含判定部署模式(不依赖 is_local_mode —— 生产 deploy 仓的 config.py 与 OSS 已分叉,
    # 那边可能没有该函数;直接读 RPG_DEPLOYMENT_MODE 保证两仓都能用)。
    _mode = os.getenv("RPG_DEPLOYMENT_MODE", "local").strip().lower()
    local = _mode in ("local", "desktop", "self_hosted", "self-hosted")
    lo, hi = 30.0, (7200.0 if local else 900.0)
    if user_id:
        try:
            from platform_app.db import connect
            with connect() as db:
                row = db.execute(
                    "select preferences->>'settings.request_timeout' as v "
                    "from user_preferences where user_id = %s",
                    (int(user_id),),
                ).fetchone()
            if row and row.get("v") not in (None, ""):
                return max(lo, min(hi, float(row["v"])))
        except Exception:
            pass
    env = os.getenv("RPG_GM_TIMEOUT")
    if env:
        try:
            return max(lo, min(hi, float(env)))
        except (TypeError, ValueError):
            pass
    return 1800.0 if local else 300.0

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
