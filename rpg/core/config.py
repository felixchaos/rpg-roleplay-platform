"""core.config — 项目配置加载入口。

汇集分散在各处的 os.getenv 调用,提供类型化访问。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def load_dotenv_once() -> None:
    """加载项目根目录 .env (rpg/ 的上一级)。幂等。"""
    try:
        from dotenv import load_dotenv
        # core/config.py 在 rpg/core/ 下，.env 在 rpg 的上一级
        # parent = rpg/core，parent.parent = rpg，parent.parent.parent = 项目根
        load_dotenv(Path(__file__).parent.parent.parent / ".env")
    except ImportError:
        pass


# 常用环境变量的类型化访问
def deployment_mode() -> str:
    return os.getenv("RPG_DEPLOYMENT_MODE", "local")

def require_auth() -> bool:
    return os.getenv("RPG_REQUIRE_AUTH", "0") == "1"

def debug_ui() -> bool:
    return bool(os.getenv("RPG_DEBUG_UI"))

def cors_origins() -> str | None:
    return os.getenv("RPG_CORS_ORIGINS")

def trusted_proxies() -> str | None:
    return os.getenv("RPG_TRUSTED_PROXIES")

def master_key() -> str | None:
    return os.getenv("RPG_MASTER_KEY")
