"""state/permissions.py — 权限相关 helpers (_normalize_permission_mode, _permission_label)"""
from __future__ import annotations


def _normalize_permission_mode(mode: str) -> str:
    text = str(mode or "").strip().lower()
    mapping = {
        # task 53：新增 read_only（对齐 codex 的 suggest 模式）
        "只读": "read_only",
        "只读模式": "read_only",
        "suggest": "read_only",
        "read": "read_only",
        "read_only": "read_only",
        "plan": "read_only",
        "默认权限": "default",
        "default": "default",
        "auto": "auto_review",
        "自动审查": "auto_review",
        "auto_review": "auto_review",
        "review": "auto_review",
        "完全访问权限": "full_access",
        "full": "full_access",
        "full_access": "full_access",
    }
    return mapping.get(text, "full_access")


def _permission_label(mode: str) -> str:
    return {
        "read_only": "只读模式（仅叙事）",
        "default": "默认权限",
        "auto_review": "自动审查",
        "full_access": "完全访问权限",
    }.get(_normalize_permission_mode(mode), "完全访问权限")
