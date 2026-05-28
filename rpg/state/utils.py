"""state/utils.py — 通用工具函数 (_deep_update, _latest_assistant_text, _hit_score, _player_action_text)"""
from __future__ import annotations
import re

from state.parsers import _clean_item


def _deep_update(target: dict, source: dict):
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(target[key], value)
        else:
            target[key] = value


def _latest_assistant_text(history: list[dict]) -> str:
    for msg in reversed(history or []):
        if msg.get("role") == "assistant":
            return str(msg.get("content") or "")
    return ""


def _hit_score(context: str, needles: tuple[str, ...]) -> int:
    return sum(8 for needle in needles if needle and needle in context)


def _player_action_text(text: str) -> str:
    text = _clean_item(text)
    text = re.sub(r"^(?:我|我们|你)\s*", "", text)
    text = re.sub(r"^(先|然后)\s*(?:我|我们|你)\s*", r"\1", text)
    return text.rstrip("。.!！?？")
