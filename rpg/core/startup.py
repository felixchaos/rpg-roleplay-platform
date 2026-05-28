"""core.startup — FastAPI app 启动配置 (middleware / exception_handlers / lifespan)。

调用方式:
    from core.startup import configure_app
    configure_app(app)

lifespan 需在 FastAPI() 构造时传入:
    from core.startup import lifespan
    app = FastAPI(lifespan=lifespan, ...)
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from json import JSONDecodeError
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.gzip import GZipMiddleware

from core.config import (
    cors_max_age as _cors_max_age,
    gzip_min_bytes as _gzip_min_bytes,
)
from core.logging import get_logger

if TYPE_CHECKING:
    pass

log = get_logger(__name__)

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


# ── Middleware ────────────────────────────────────────────────────────────

async def api_contract_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
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
    if original_path.startswith("/api"):
        response.headers.setdefault("Cache-Control", "no-store")
        response.headers["X-API-Version"] = API_VERSION
        response.headers["X-Request-ID"] = request_id
        response.headers.setdefault("Vary", "Origin")
    return response


# ── configure_app 入口 ────────────────────────────────────────────────────

def configure_app(app: FastAPI) -> None:
    """应用所有 middleware / exception_handlers 到 app 实例。

    lifespan 须在 FastAPI(lifespan=lifespan, ...) 构造时传入，不在此处注册。
    """
    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
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
