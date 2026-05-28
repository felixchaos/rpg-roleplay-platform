"""_deps.py — 跨路由共享依赖的延迟导入辅助。

所有 helpers 的实现仍在 app.py（持有模块级状态变量）。
为避免循环 import（app.py 在初始化时 import routes，routes 在 module 级 import app），
这里提供惰性函数而不是直接 re-export。

路由文件可以直接使用这里的包装函数，也可以在路由函数体内直接：
    from app import _require_api_user, _payload, ...
"""
from __future__ import annotations


def get_require_api_user():
    from app import _require_api_user
    return _require_api_user


def get_payload():
    from app import _payload
    return _payload


def get_resolve_persist_target():
    from app import _resolve_persist_target
    return _resolve_persist_target


def get_ensure_loaded():
    from app import _ensure_loaded
    return _ensure_loaded


def get_persist_runtime_checkpoint():
    from app import _persist_runtime_checkpoint
    return _persist_runtime_checkpoint
