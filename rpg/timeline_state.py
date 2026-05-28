"""
timeline_state.py - Runtime timeline jump protocol.

Time jumps are a two-step transaction:
1. Player requests a target time -> pending transition.
2. GM must confirm or reject -> locked timeline anchor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class TimeDirective:
    target: str
    raw: str


def detect_time_directives(text: str) -> list[TimeDirective]:
    patterns = [
        r"(?:时间线|时间|剧情|镜头|场景)?\s*(?:跳到|跳转到|快进到|切到|来到|推进到|过渡到|直接到|直接进入|进入|等到|等至|直到|跳过到|略过到|越过到)\s*([^，。！？\n]{2,48})",
        r"(?:/time|/timeline)\s+([^\n]{2,80})",
        r"(?:跳到|跳转到|快进到|切到|来到|进入)?\s*(第\s*\d{1,5}\s*章[^，。！？\n]{0,24})",
        r"(?:跳到|跳转到|快进到|切到|来到|进入)?\s*((?:公元)?\d{3,5}\s*年[^，。！？\n]{0,24})",
    ]
    out: list[TimeDirective] = []
    for pattern in patterns:
        for match in re.findall(pattern, text or ""):
            target = clean_time_value(match)
            if looks_like_time_value(target) and target not in [x.target for x in out]:
                out.append(TimeDirective(target=target, raw=text))
    return out


def clean_time_value(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text).strip(" \n\t:：-—")).strip()
    value = re.sub(r"^(?:到|至|在)\s*", "", value)
    value = re.sub(r"(?:后?再)?(?:行动|出发|继续|调查|处理|会合|潜入|开场|开始)$", "", value)
    return re.sub(r"\s+", " ", value.strip(" \n\t:：-—")).strip()


def looks_like_time_value(value: str) -> bool:
    if not (2 <= len(value) <= 80):
        return False
    return bool(re.search(r"日|天|夜|晨|早|午|晚|周|月|年|后|前|翌|次|清晨|傍晚|深夜|黎明|柏林|图卢兹|基地|第\s*\d{1,5}\s*章", value))


def is_time_key(key: str) -> bool:
    return any(marker in key for marker in ("当前时间线", "时间线", "当前时间", "时间跳转", "时间推进", "跳转时间", "时点"))
