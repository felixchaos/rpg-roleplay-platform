"""platform_app.api.admin —— /api/admin/*、/api/internal/allowlist/bulk 路由(需 admin 角色,包化)。

原单文件(1463 行)按资源族拆为子包;本 __init__ 是薄门面:import 全部子模块触发装配
(各子模块 `from ._shared import router` 后用 `@router.<verb>` 注册,共享同一 APIRouter
实例),再逐名 re-export 原模块的全部公开名(含 router / 全部 admin_*/api_* 端点 / 下划线辅助与
常量),让 `from platform_app.api.admin import router` 与既有引用零改动。

── 2026-07-15 拆包说明(纯机械搬家,零行为变化)────────────────────────────
_shared.py     — 共享的单一 router 实例 + log + _require_admin + _get_app_config/_set_app_config/_write_audit
users.py       — 2.2 用户管理(list/update/deactivate/reactivate/force-logout)
usage.py       — 2.3 全局用量(/api/admin/usage)
audit.py       — 2.4 审计日志(/api/admin/audit)
health.py      — 2.5 系统健康(/api/admin/health)
logs.py        — 2.6 日志(/api/admin/logs)
registration.py— 2.7 注册与邀请(registration 配置 + invite-codes CRUD;含 _REGISTRATION_CFG_KEY/_DEFAULT_REGISTRATION)
security.py    — 2.8 安全配置(security-config 读/写;含 _SECURITY_CFG_KEY/_DEFAULT_SECURITY)
maintenance.py — 2.9 维护模式 + 2.10 服务重启(含 _MAINTENANCE_CFG_KEY/_DEFAULT_MAINTENANCE)
dmca.py        — 3.1 DMCA 下架队列 + 3.2 DMCA Strike 管理
csam.py        — 3.3 CSAM 举报管理
aup.py         — 3.4 AUP 账户暂停/解封/终止
allowlist.py   — 2.x 注册白名单批量导入 + /api/internal/allowlist/bulk 跨服务推送
co_builders.py — Beta Co-builders 名单(/api/admin/co-builders)
achievements.py— 成就目录管理(含 _re/_ACHV_SLUG/_ACHV_TIERS/_achv_validate_payload)
"""
from __future__ import annotations

# 原顶层 import 的名字(测试/调用方可能以 module.X 形式引用)—— 保持可见
import logging  # noqa: F401
import os  # noqa: F401
import secrets  # noqa: F401
import signal  # noqa: F401
import string  # noqa: F401
import sys  # noqa: F401
import time  # noqa: F401
from datetime import datetime, timezone  # noqa: F401
from typing import Optional  # noqa: F401

from fastapi import APIRouter, Depends, HTTPException, Request  # noqa: F401
from psycopg.types.json import Jsonb  # noqa: F401

from ...db import connect  # noqa: F401
from ...dmca import increment_strike, queue_account_termination  # noqa: F401
from .._deps import _client_ip, json_response, require_admin  # noqa: F401
from ._shared import (
    _get_app_config,
    _require_admin,
    _set_app_config,
    _write_audit,
    log,
    router,
)
from .achievements import (
    _ACHV_SLUG,
    _ACHV_TIERS,
    _achv_validate_payload,
    admin_achv_create,
    admin_achv_delete,
    admin_achv_list,
    admin_achv_update,
)
from .allowlist import (
    api_bulk_allowlist,
    api_internal_allowlist_bulk,
    api_list_allowlist,
)
from .audit import admin_audit_log
from .aup import (
    admin_suspend_user,
    admin_terminate_user,
    admin_unsuspend_user,
)
from .co_builders import admin_co_builders
from .csam import (
    admin_csam_decision,
    admin_csam_list,
)
from .dmca import (
    admin_dmca_action,
    admin_dmca_counter,
    admin_dmca_create,
    admin_dmca_list,
    admin_dmca_strike_increment,
    admin_dmca_strikes_list,
)
from .health import admin_health
from .logs import admin_logs
from .maintenance import (
    _DEFAULT_MAINTENANCE,
    _MAINTENANCE_CFG_KEY,
    admin_get_maintenance,
    admin_restart,
    admin_set_maintenance,
)
from .registration import (
    _DEFAULT_REGISTRATION,
    _REGISTRATION_CFG_KEY,
    admin_create_invite_codes,
    admin_delete_invite_code,
    admin_get_registration,
    admin_list_invite_codes,
    admin_set_registration,
)
from .security import (
    _DEFAULT_SECURITY,
    _SECURITY_CFG_KEY,
    admin_get_security_config,
    admin_set_security_config,
)
from .usage import admin_usage
from .users import (
    admin_deactivate_user,
    admin_force_logout_user,
    admin_list_users,
    admin_reactivate_user,
    admin_update_user,
)

__all__ = ["router"]
