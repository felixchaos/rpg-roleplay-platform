"""platform_app.api — FastAPI router 主包,按主题拆 sub-router。"""
# ruff: noqa: F401
from fastapi import APIRouter

router = APIRouter()

# sub-router 必须先 import,然后 include
from .admin import router as _admin_router
from .auth import router as _auth_router
from .federation import router as _federation_router
from .feedback import router as _feedback_router
from .imports import router as _imports_router
from .library import router as _library_router
from .me import router as _me_router
from .platform import router as _platform_router
from .policy import router as _policy_router
from .saves import router as _saves_router
from .script_edit import router as _script_edit_router
from .scripts import router as _scripts_router
from .settings import router as _settings_router
from .splash import router as _splash_router
from .worldline_memory import router as _wm_router

router.include_router(_auth_router)
router.include_router(_platform_router)
router.include_router(_scripts_router)
router.include_router(_script_edit_router)
router.include_router(_imports_router)
router.include_router(_saves_router)
router.include_router(_wm_router)
router.include_router(_settings_router)
router.include_router(_me_router)
router.include_router(_library_router)
router.include_router(_admin_router)
router.include_router(_splash_router)
router.include_router(_feedback_router)
router.include_router(_policy_router)
router.include_router(_federation_router)

# re-export 跨模块用的符号 (让外部 `from platform_app.api import ...` 仍然工作)
from ..security import public_user
from ._deps import (
    _MCP_SECRET_FIELDS,
    API_VERSION,
    COMMANDS,
    SESSION_COOKIE,
    _auth_required,
    _client_ip,
    _delete_session_cookie,
    _redact_mcp_in_tools,
    _resolve_save_id,
    _set_session_cookie,
    command_payload,
    current_user,
    json_response,
    platform_for,
    require_user,
)

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
