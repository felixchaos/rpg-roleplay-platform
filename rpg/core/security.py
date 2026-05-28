"""core.security — 安全相关 re-export 入口 (实际实现在 platform_app.auth)。"""
from platform_app.auth import (
    RateLimited,
    admin_unlock,
    get_user,
    login,
    logout,
    register,
    update_profile,
    user_from_token,
)

__all__ = [
    "RateLimited",
    "admin_unlock",
    "register",
    "login",
    "logout",
    "user_from_token",
    "get_user",
    "update_profile",
]
