"""platform_app.api — FastAPI router 主包,按主题拆 sub-router。"""
from fastapi import APIRouter

router = APIRouter()

# sub-router 必须先 import,然后 include
from .app_html import router as _app_html_router
from .auth import router as _auth_router
from .platform import router as _platform_router
from .scripts import router as _scripts_router
from .imports import router as _imports_router
from .saves import router as _saves_router
from .worldline_memory import router as _wm_router
from .settings import router as _settings_router
from .me import router as _me_router
from .library import router as _library_router

router.include_router(_app_html_router)
router.include_router(_auth_router)
router.include_router(_platform_router)
router.include_router(_scripts_router)
router.include_router(_imports_router)
router.include_router(_saves_router)
router.include_router(_wm_router)
router.include_router(_settings_router)
router.include_router(_me_router)
router.include_router(_library_router)

# re-export 跨模块用的符号 (让外部 `from platform_app.api import ...` 仍然工作)
from ._deps import (
    SESSION_COOKIE,
    API_VERSION,
    COMMANDS,
    current_user,
    require_user,
    json_response,
    _auth_required,
    _resolve_save_id,
    platform_for,
    command_payload,
    _redact_mcp_in_tools,
    _MCP_SECRET_FIELDS,
    _set_session_cookie,
    _client_ip,
)
from ..security import public_user

__all__ = [
    "router",
    "SESSION_COOKIE",
    "API_VERSION",
    "COMMANDS",
    "current_user",
    "require_user",
    "json_response",
    "public_user",
]
