"""core.startup — FastAPI app 启动配置 (middleware / exception_handlers / lifespan)。

调用方式:
    from core.startup import configure_app
    configure_app(app)

lifespan 需在 FastAPI() 构造时传入:
    from core.startup import lifespan
    app = FastAPI(lifespan=lifespan, ...)
"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar
from json import JSONDecodeError
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from core.config import (
    cors_max_age as _cors_max_age,
    gzip_min_bytes as _gzip_min_bytes,
    trusted_proxies as _trusted_proxies,
)
from core.logging import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

# ── 可观测性: request_id ContextVar ─────────────────────────────────────────
# contextvars 会跨 asyncio.to_thread 传播,SSE stream 子线程也能拿到 request_id。
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


class RequestIdFilter(logging.Filter):
    """将当前 ContextVar 中的 request_id 注入 LogRecord。"""

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        record.request_id = _request_id_var.get() or "-"  # type: ignore[attr-defined]
        return True


def get_request_id() -> str:
    """供业务代码读取当前 request_id。"""
    return _request_id_var.get() or "-"

# ── API 版本（与 app.py 保持一致）────────────────────────────────────────
API_VERSION = "1"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_LOCAL_MODES = {"local", "desktop", "self_hosted", "self-hosted"}
_SERVER_MODES = {"server", "production", "prod", "cloud"}


# ── CORS origins 计算 ────────────────────────────────────────────────────

def _cors_origins() -> tuple[list[str], bool]:
    default_origins = (
        "http://127.0.0.1:7860,http://localhost:7860,"
        "http://127.0.0.1:5173,http://localhost:5173,"
        "http://127.0.0.1:3000,http://localhost:3000"
    )
    from core.config import cors_origins_with_default as _cors_origins_with_default
    raw = _cors_origins_with_default(default_origins)
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    if not origins:
        origins = ["http://127.0.0.1:7860", "http://localhost:7860"]
    allow_all = "*" in origins
    return (["*"] if allow_all else origins), not allow_all


_origins, _allow_credentials = _cors_origins()


def _deployment_mode() -> str:
    from core.config import deployment_mode as _deployment_mode_cfg
    return _deployment_mode_cfg().strip().lower() or "local"


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True
    return "*" in _origins or origin in _origins


# ── lifespan (startup / shutdown) ────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: startup → yield → shutdown。"""
    # ── startup ──────────────────────────────────────────────────────────
    # 0. init_db — schema 创建 + migration（lazy import 避免循环依赖）
    try:
        from app import _bootstrap_init_db  # type: ignore[import]
        _bootstrap_init_db()
    except Exception:
        log.exception("[startup] init_db failed")

    # 1. MCP health loop
    try:
        import mcp_broker
        mcp_broker.start_health_loop()
    except Exception:
        pass

    # 2. command_tools + dispatcher 注册
    try:
        from tools_dsl.command_tools_register import ensure_registered
        ensure_registered()
        from tools_dsl.command_dispatcher import get_registry
        log.info(f"[startup] command_dispatcher: 已注册 {len(get_registry().list_all())} 个工具")
    except Exception as exc:
        log.exception("command tools registration failed: %s", exc)

    # 3. durable job 恢复 (B5)
    try:
        from platform_app import script_import
        result = script_import.recover_pending_sync_jobs()
        if result.get("recovered_pending") or result.get("reclaimed_stale"):
            log.info(
                "durable sync recovery: pending=%s stale=%s resubmitted=%s",
                result.get("recovered_pending"),
                result.get("reclaimed_stale"),
                len(result.get("resubmitted", [])),
            )
    except Exception:
        log.exception("durable sync recovery failed")

    # 4. 清理残留上传分片（防磁盘泄漏）
    try:
        from platform_app.script_import import cleanup_stale_upload_chunks
        n = cleanup_stale_upload_chunks(ttl_hours=24)
        if n:
            log.info("[startup] 清理 %d 个 stale upload chunks (>24h)", n)
    except Exception as e:
        log.warning("[startup] cleanup_stale_upload_chunks failed: %s", e)

    yield

    # ── shutdown ──────────────────────────────────────────────────────────
    try:
        import mcp_broker
        mcp_broker.stop_health_loop()
        mcp_broker.stop_all()
    except Exception:
        pass

    try:
        from platform_app.db.connection import close_pool
        close_pool()
    except Exception:
        pass


# ── Exception handlers ───────────────────────────────────────────────────

async def _value_error_handler(request: Request, exc: ValueError):
    return JSONResponse({"ok": False, "error": str(exc) or "invalid value"}, status_code=400)


async def _key_error_handler(request: Request, exc: KeyError):
    return JSONResponse({"ok": False, "error": f"missing field: {exc}"}, status_code=400)


async def _type_error_handler(request: Request, exc: TypeError):
    msg = str(exc)
    return JSONResponse({"ok": False, "error": f"invalid input type: {msg[:200]}"}, status_code=400)


async def _json_decode_handler(request: Request, exc: JSONDecodeError):
    return JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)


async def _permission_handler(request: Request, exc: PermissionError):
    return JSONResponse({"ok": False, "error": str(exc) or "forbidden"}, status_code=403)


async def _file_not_found_handler(request: Request, exc: FileNotFoundError):
    return JSONResponse({"ok": False, "error": str(exc) or "not found"}, status_code=404)


async def _internal_error_handler(request: Request, exc: Exception):
    """兜底 500 handler — 避免 FastAPI/Starlette 默认行为把堆栈+SQL 泄漏给前端。

    完整 traceback 写到服务端日志（含 request_id 便于追查），返回给前端只有通用错误码。
    """
    request_id = getattr(request.state, "request_id", None) or uuid.uuid4().hex
    log.exception("unhandled exception in request %s: %s", request_id, type(exc).__name__)
    return JSONResponse(
        {"ok": False, "error": "internal server error", "request_id": request_id, "code": "E_INTERNAL"},
        status_code=500,
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
    )


# ── Middleware ────────────────────────────────────────────────────────────

# dev 模式:RPG_ENV=dev 或 RPG_DEPLOYMENT_MODE=local/desktop/self_hosted
def _is_dev_mode() -> bool:
    """True 表示本地开发环境,放宽部分安全策略(如 CSP connect-src、cookie Secure)。"""
    rpg_env = os.getenv("RPG_ENV", "").strip().lower()
    if rpg_env == "dev":
        return True
    if rpg_env == "prod":
        return False
    mode = os.getenv("RPG_DEPLOYMENT_MODE", "local").strip().lower()
    return mode in _LOCAL_MODES


def _build_csp(dev: bool) -> str:
    """构建 Content-Security-Policy 策略字符串。

    dev 模式:connect-src 放宽以支持 Vite HMR ws://localhost:*。
    prod 模式:仅允许已知第三方 AI API 端点。
    """
    if dev:
        connect_src = (
            "'self' ws: wss: http://localhost:* http://127.0.0.1:* "
            "ws://localhost:* ws://127.0.0.1:* "
            "api.anthropic.com api.openai.com api.deepseek.com "
            "dashscope.aliyuncs.com ark.cn-beijing.volces.com "
            "api.minimax.chat hunyuan.tencentcloudapi.com"
        )
    else:
        connect_src = (
            "'self' wss: https: "
            "api.anthropic.com api.openai.com api.deepseek.com "
            "dashscope.aliyuncs.com ark.cn-beijing.volces.com "
            "api.minimax.chat hunyuan.tencentcloudapi.com"
        )
    directives = [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
        "style-src 'self' 'unsafe-inline' fonts.googleapis.com",
        "font-src 'self' fonts.gstatic.com",
        "img-src 'self' data: https:",
        f"connect-src {connect_src}",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    return "; ".join(directives)


# 安全 headers 默认值（HTML/static 资源加；JSON API 不强加 CSP, 避免破坏 fetch 路径）
_DEFAULT_HTML_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

# Cookie 白名单前缀（只允许 rpg_session / rpg.lang 这两种 cookie）
_ALLOWED_COOKIE_PREFIXES = ("rpg_session", "rpg.lang")


def _is_https(request: Request) -> bool:
    """判断请求是否经由 HTTPS,支持 nginx/CF 反代场景。

    仅当 RPG_TRUSTED_PROXIES 已设置时才信任 X-Forwarded-Proto,防止客户端伪造。
    """
    if request.url.scheme == "https":
        return True
    if _trusted_proxies():
        xfp = request.headers.get("x-forwarded-proto", "").lower()
        return xfp == "https"
    return False


def _harden_set_cookie(header_value: str, is_https: bool) -> str:
    """强制 Set-Cookie 头带上 Secure/HttpOnly/SameSite=Lax 属性。

    仅处理 rpg_ / rpg. 前缀的 cookie(白名单范围)。
    """
    # 解析 cookie 名
    parts = [p.strip() for p in header_value.split(";")]
    if not parts:
        return header_value
    name_value = parts[0]
    cookie_name = name_value.split("=", 1)[0].strip()
    is_allowed = any(
        cookie_name == allowed or cookie_name.startswith(allowed)
        for allowed in _ALLOWED_COOKIE_PREFIXES
    )
    if not is_allowed:
        return header_value  # 非白名单 cookie,不干预

    attrs_lower = {p.strip().lower().split("=")[0] for p in parts[1:]}
    result = list(parts)

    # HttpOnly
    if "httponly" not in attrs_lower:
        result.append("HttpOnly")
    # SameSite=Lax
    if "samesite" not in attrs_lower:
        result.append("SameSite=Lax")
    # Secure(仅 HTTPS 模式)
    if is_https and "secure" not in attrs_lower:
        result.append("Secure")
    # Max-Age=14天(1209600秒),若未设置
    if "max-age" not in attrs_lower and "expires" not in attrs_lower:
        result.append("Max-Age=1209600")

    return "; ".join(result)


async def api_contract_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id  # 让 _internal_error_handler 能拿到
    _request_id_var.set(request_id)  # 可观测性: SSE stream + to_thread 子线程均可读取
    original_path = request.scope.get("path", "")
    prefix = f"/api/v{API_VERSION}"
    if original_path == prefix:
        request.scope["path"] = "/api"
    elif original_path.startswith(prefix + "/"):
        request.scope["path"] = "/api" + original_path[len(prefix):]
    if original_path.startswith("/api") and request.method in MUTATING_METHODS:
        origin = request.headers.get("origin")
        if not _origin_allowed(origin):
            return JSONResponse(
                {"ok": False, "error": "Origin 不在允许列表", "request_id": request_id},
                status_code=403,
                headers={"X-API-Version": API_VERSION, "X-Request-ID": request_id, "Cache-Control": "no-store"},
            )
    response = await call_next(request)

    # ── GPC 确认 ─────────────────────────────────────────────────────────
    from platform_app.privacy import annotate_gpc
    annotate_gpc(request, response)

    # ── Cookie 强化 ───────────────────────────────────────────────────────
    _https = _is_https(request)
    raw_cookies = response.headers.getlist("set-cookie")
    if raw_cookies:
        # MutableHeaders 不支持 multi-value 逐条替换,需先删后加
        del response.headers["set-cookie"]
        for cookie_val in raw_cookies:
            response.headers.append("set-cookie", _harden_set_cookie(cookie_val, _https))

    if original_path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-API-Version"] = API_VERSION
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("Vary", "Origin")
    else:
        # 非 /api 路径（HTML/JS/CSS/static）默认加安全 headers
        for k, v in _DEFAULT_HTML_SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        # CSP 仅加在 HTML 路径上(非 API)
        _dev = _is_dev_mode()
        response.headers.setdefault("Content-Security-Policy", _build_csp(_dev))
        # HSTS — 反代友好,读 X-Forwarded-Proto
        if _https:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )
    return response


# ── configure_app 入口 ────────────────────────────────────────────────────

def configure_app(app: FastAPI) -> None:
    """应用所有 middleware / exception_handlers 到 app 实例。

    lifespan 须在 FastAPI(lifespan=lifespan, ...) 构造时传入，不在此处注册。
    """
    # CORS — 注意: allow_credentials=True 时 allow_headers 必须明确枚举（Fetch 规范不允许 *）
    # 旧实现 allow_headers=["*"] + credentials=True 严格浏览器下静默失败
    _allowed_request_headers = [
        "Content-Type",
        "Authorization",
        "X-Requested-With",
        "X-Request-ID",
        "X-API-Version",
        "Accept",
        "Accept-Language",
        "Origin",
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=(["*"] if not _allow_credentials else _allowed_request_headers),
        expose_headers=["X-API-Version", "X-Request-ID"],
        max_age=_cors_max_age(),
    )

    # GZip
    app.add_middleware(GZipMiddleware, minimum_size=_gzip_min_bytes())

    # Custom middleware (后注册的先执行)
    app.middleware("http")(api_contract_middleware)

    # Exception handlers
    app.add_exception_handler(ValueError, _value_error_handler)
    app.add_exception_handler(KeyError, _key_error_handler)
    app.add_exception_handler(TypeError, _type_error_handler)
    app.add_exception_handler(JSONDecodeError, _json_decode_handler)
    app.add_exception_handler(PermissionError, _permission_handler)
    app.add_exception_handler(FileNotFoundError, _file_not_found_handler)
    # 兜底 Exception handler — 必须最后注册（具体异常优先匹配）
    app.add_exception_handler(Exception, _internal_error_handler)

    # 可观测性: 把 RequestIdFilter 挂到 root logger,让所有子 logger 日志都带 request_id
    _rid_filter = RequestIdFilter()
    logging.getLogger().addFilter(_rid_filter)
