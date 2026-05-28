"""routes._deps_fastapi — FastAPI Depends() dependency functions for routes/."""
from __future__ import annotations

from typing import Any

from fastapi import Request


def get_current_user(request: Request) -> dict[str, Any] | None:
    """返回当前 api_user (本地模式可能返回 None)。"""
    from app import _require_api_user
    return _require_api_user(request)


def get_current_admin(request: Request) -> dict[str, Any] | None:
    """返回当前 api_user，要求 admin 权限。"""
    from app import _require_api_user
    return _require_api_user(request, admin=True)


def get_payload_fn(request: Request):
    """返回 _payload 函数 (闭包了当前 user)。"""
    from app import _payload
    return _payload
