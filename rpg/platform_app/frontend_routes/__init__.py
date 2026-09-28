"""Frontend-driven supplementary routes.

These endpoints back the Claude Design front-end (Login.html, Platform.html,
Game Console.html). They live in a separate router so the existing api.py
can be edited independently without merge collisions.

Mount in ui.py with:

    from platform_app.frontend_routes import router as frontend_router
    app.include_router(frontend_router)

── 2026-07-15 拆包说明(纯机械搬家,零行为变化)────────────────────────────
原单文件(1136 行,历史多域杂烩:认证/资料/账号/存档/搜索/模型/管理)按域拆为子包。
本 __init__ 是薄门面:import 全部子模块触发装配(各子模块 `from ._shared import router`
后用 `@router.<verb>` 注册,共享同一 APIRouter 实例),再逐名 re-export 原模块的全部
公开名(含 router / 全部 api_* 端点 / 下划线辅助与常量 / 原顶层 import 名),让
`from platform_app.frontend_routes import router`、
`from platform_app.frontend_routes import _ensure_profile_extras_table`(api/platform、
api/me/profile)、测试侧 `from platform_app import frontend_routes as fr; fr.api_*` 等既有
引用零改动。

⚠️ 历史多域杂烩待治理:本次仅拆文件,绝不改任何路由 path/method/名称,也未把端点挪去
「更该归属」的模块归位——端点的历史归属治理是另一个议题。

_shared.py   — 共享 router 实例 + 通用辅助 _bad / _client_ip
auth.py      — /api/auth/*:改密 / 登录历史 / 会话列表 / 吊销
profile.py   — /api/profile/*(含 _ensure_profile_extras_table 占位)
account.py   — /api/account/*:导出 / 停用 / 硬删申请撤销 / 状态
saves.py     — /api/saves/{save_id}/*:删除 / 改名 / 激活
models.py    — /api/models/* + /api/me/models/visibility(可见性 & 校验)
search.py    — /api/search + /api/plugins + /api/skills(含 _SEARCH_SCOPES)
admin.py     — /api/admin/smtp/test + /api/admin/deployment-config(含 _DEPLOY_CFG_KEY)
"""
from __future__ import annotations

# 原顶层 import 的名字(测试/调用方可能以 module.X 形式引用)—— 保持可见
import csv  # noqa: F401
import io  # noqa: F401
import json  # noqa: F401
import os  # noqa: F401
import threading  # noqa: F401
import time  # noqa: F401
from datetime import datetime  # noqa: F401
from pathlib import Path  # noqa: F401

from fastapi import APIRouter, HTTPException, Request  # noqa: F401
from fastapi.responses import FileResponse, StreamingResponse  # noqa: F401

from .. import auth as _auth  # noqa: F401
from ..api import (  # noqa: F401
    SESSION_COOKIE,
    _delete_session_cookie,
    json_response,
    require_user,
)
from ..api._deps import is_admin  # noqa: F401
from ..db import connect, expose, init_db  # noqa: F401
from ..perms import owns_save  # noqa: F401
from ..security import hash_password, verify_password  # noqa: F401
from ..storage import AVATARS_DIR as _AVATARS_DIR  # noqa: F401
from ..storage import resolve_path as _storage_resolve_path  # noqa: F401
from ..storage import store_bytes as _storage_store_bytes  # noqa: F401
from ._shared import _bad, _client_ip, router
from .account import (
    api_account_cancel_delete,
    api_account_deactivate,
    api_account_delete,
    api_account_delete_status,
    api_account_export,
    api_account_export_post,
    api_account_request_delete,
)
from .admin import (
    _DEPLOY_CFG_KEY,
    api_admin_deployment_config_get,
    api_admin_deployment_config_set,
    api_admin_smtp_test,
)
from .auth import (
    api_change_password,
    api_list_sessions,
    api_login_history,
    api_revoke_all_sessions,
    api_revoke_session,
)
from .models import (
    api_me_models_visibility,
    api_models_validate,
    api_models_visibility,
)
from .profile import (
    _ensure_profile_extras_table,
    api_avatar_file,
    api_profile_visibility,
    api_reset_avatar,
    api_set_avatar_url,
    api_upload_avatar,
)
from .saves import (
    api_save_activate,
    api_save_delete,
    api_save_rename,
)
from .search import (
    _SEARCH_SCOPES,
    api_plugins,
    api_search,
    api_skills_list,
)

__all__ = ["router"]
