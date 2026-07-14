"""platform_app.api.me —— /api/me/*、/api/achievements、/api/u/{username}/achievements、
/api/cards/public、/api/gm-style/schema 路由(profile / 偏好 / 凭据 / 用量 / 成就 / 角色卡 /
导出导入等,用户自助域,包化)。

原单文件(1537 行)按资源族拆为子包;本 __init__ 是薄门面:import 全部子模块触发装配
(各子模块 `from ._shared import router` 后用 `@router.<verb>` 注册,共享同一 APIRouter
实例),再逐名 re-export 原模块的全部公开名(含 router / 全部 api_* 端点 / 下划线辅助与常量),
让 `from platform_app.api.me import router`(以及生产侧 `from platform_app.api.me import
_detect_image_mime` in frontend_routes、测试侧 `from platform_app.api import me as me_api;
me_api.api_set_credential`)与既有引用零改动。

── 2026-07-15 拆包说明(纯机械搬家,零行为变化)────────────────────────────
_shared.py     — 共享的单一 router 实例 + _detect_image_mime / _store_imported_card_image
profile.py     — 个人主页 profile(读/写)+ welcome-dismiss + usage + usage/timeline + stats + activity
achievements.py— 成就目录/用户态/seen + 公开成就墙(/api/achievements、/api/u/{username}/achievements)
preferences.py — 界面偏好 preference + 用户级 GM 风格(gm-style schema/读/写)
tasks.py       — 全局后台任务浮窗(含 _TASK_*_KIND_LABELS / _task_norm_status)
personas.py    — 用户级 persona + NPC 角色卡 CRUD
cards_public.py— 在线角色卡库(visibility/public 列表/clone)
tavern.py      — 酒馆角色卡兼容(import/export/png/json)+ 聊天记录导入(含 _truthy)
card_images.py — 人设图自动同步/生成/历史 + 头像上传(含 _MAX_IMAGE_BYTES)
account.py     — 账号级数据导出/导入(含 _MAX_ACCOUNT_IMPORT_BYTES)
credentials.py — 用户级 API 凭证 CRUD + embedder 状态 + 凭证自检(含 _PING_CACHE / _PING_TTL)
"""
from __future__ import annotations

# 原顶层 import 的名字(测试/调用方可能以 module.X 形式引用)—— 保持可见
import asyncio  # noqa: F401
import json  # noqa: F401
import secrets  # noqa: F401

from fastapi import APIRouter, Depends, File, Request, UploadFile  # noqa: F401
from psycopg.types.json import Jsonb  # noqa: F401

from ...db import connect  # noqa: F401
from ...security import normalize_username, public_user  # noqa: F401
from .._deps import SESSION_COOKIE, json_response, require_user  # noqa: F401
from ._shared import router, _detect_image_mime, _store_imported_card_image
from .account import (
    _MAX_ACCOUNT_IMPORT_BYTES,
    api_account_export,
    api_account_export_estimate,
    api_account_import,
)
from .achievements import (
    api_my_achievements,
    api_my_achievements_seen,
    api_public_achievements,
    api_public_wall,
)
from .card_images import (
    _MAX_IMAGE_BYTES,
    api_generate_persona_image,
    api_list_persona_images,
    api_set_auto_image_sync,
    api_set_card_avatar_url,
    api_set_current_persona_image,
    api_set_persona_image_url,
    api_upload_card_avatar,
    api_upload_persona_image,
)
from .cards_public import (
    api_clone_public_card,
    api_list_public_cards,
    api_set_card_visibility,
)
from .credentials import (
    _PING_CACHE,
    _PING_TTL,
    api_delete_credential,
    api_embedder_status,
    api_my_credentials,
    api_set_credential,
    api_test_credential,
)
from .personas import (
    api_delete_character_card,
    api_delete_persona,
    api_get_character_card,
    api_get_persona,
    api_my_character_cards,
    api_my_personas,
    api_upsert_character_card,
    api_upsert_persona,
)
from .preferences import (
    api_get_my_gm_style,
    api_gm_style_schema,
    api_set_my_gm_style,
    api_set_preference,
)
from .profile import (
    api_my_activity,
    api_my_profile,
    api_my_stats,
    api_my_usage,
    api_my_usage_timeline,
    api_patch_profile,
    api_welcome_dismiss,
)
from .tasks import (
    _TASK_IMAGE_KIND_LABELS,
    _TASK_IMPORT_KIND_LABELS,
    _task_norm_status,
    api_active_tasks,
)
from .tavern import (
    _truthy,
    api_export_tavern_card,
    api_export_tavern_png,
    api_import_json_card,
    api_import_tavern_card,
    api_import_tavern_chat,
)

__all__ = ["router"]
