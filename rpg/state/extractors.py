"""state/extractors.py — 从文本提取指令的 helpers"""
from __future__ import annotations
import re

from timeline_state import detect_time_directives, clean_time_value, looks_like_time_value
from state.parsers import _clean_item, _parse_assignment


def _extract_player_time_directives(text: str) -> list[str]:
    return [d.target for d in detect_time_directives(text or "")]


def _extract_set_directive(text: str) -> str:
    raw = str(text or "").strip()
    match = re.match(r"^/(?:set|设定|设置)\s+(.+)$", raw, re.I | re.S)
    if not match:
        return ""
    return _clean_item(match.group(1))


def _extract_set_assignments(text: str) -> list[str]:
    assignments: list[str] = []
    chunks: list[str] = []
    for segment in re.split(r"[；;\n]+", text or ""):
        chunks.extend(re.split(r"[，,]\s*(?=[^，,。！？；;\n]{1,32}(?:=|：|:))", segment))
    for raw in chunks:
        item = _clean_item(raw)
        if not item or not any(sep in item for sep in ("=", "：", ":")):
            continue
        path, value = _parse_assignment(item)
        if path and value:
            assignments.append(f"{path}={value}")
    return assignments


def _extract_location_override(text: str) -> str:
    patterns = [
        r"(?:当前位置|地点|位置)\s*(?:改为|设为|设置为|切到|跳到|在|位于|=|：|:)\s*([^，。！？\n；;]{1,48})",
        r"(?:现在|当前)\s*(?:在|位于)\s*([^，。！？\n；;]{1,48})",
        r"(?:不在|不是)\s*[^，。！？\n；;]{1,32}[，,；; ]+(?:而是|现在在|应在|改在)\s*([^，。！？\n；;]{1,48})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text or "")
        if match:
            value = _clean_item(match.group(1))
            if value:
                return value
    return ""


def _extract_set_time_targets(text: str) -> list[str]:
    """从 /set 自然语言里抽时间目标。

    task 86 (修复):
    用户在 /set 命令里**已经明示**是时间设置意图(写了"时间"/"时间线"/"时点"
    等关键词 + 设置动词),所以不再用 looks_like_time_value 启发式过滤目标值
    —— RPG 是通用底座,不应硬编码"日/天/夜/柏林/图卢兹/基地"才算时间值。
    用户写"火星·扬陆城内"/"剧情月球时期"/"魔王城地下三层"这些都应被接受。

    覆盖两类句法:
      · 时间(线)+动词+值:  "时间改为X" / "时间线=X" / "时点设为X"
      · 动词+时间(线)+介词+值: "设置时间为X" / "设时间到X" / "切换时间线到X"
    """
    values: list[str] = []
    # 1) detect_time_directives 路径 — 自然语言"跳到X/快进到X/进入X章"
    # 这类隐含意图,仍保留 looks_like_time_value 启发式过滤(避免误抓)。
    for value in _extract_player_time_directives(text):
        if value not in values:
            values.append(value)
    # 2) 显式 /set 路径 — 用户已明示"时间"+"设置动词",直接信任用户给的值。
    patterns = [
        r"(?:当前时间线|时间线|当前时间|时间|时点)\s*(?:改为|设为|设置为|锁定为|=|：|:)\s*([^，,。！？\n；;]{2,80})",
        # 动词在前: 设置/设定/设/锁定/改/更改/更新/切换/跳转/切 + 时间(线/点) + 为/到/至/=/:
        r"(?:设置|设定|设|锁定|改|更改|更新|切换|切换到|跳转到|切到)\s*"
        r"(?:当前时间线|时间线|当前时间|时间|时点)\s*"
        r"(?:为|到|至|改为|设为|=|：|:)\s*([^，,。！？\n；;]{2,80})",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text or ""):
            value = clean_time_value(match)
            if value and 2 <= len(value) <= 80 and value not in values:
                values.append(value)
    return values


def _extract_explicit_time_updates(text: str) -> list[str]:
    patterns = [
        r"(?:时间线|时间|剧情|镜头|场景)\s*(?:跳到|跳转到|快进到|切到|来到|推进到|过渡到|直接进入|进入)\s*([^，。！？\n]{2,40})",
        r"(?:时间来到|时间推进至|时间推进到|时间跳至|时间跳到|镜头切到|画面切到|场景切到|场景来到)\s*([^，。！？\n]{2,40})",
    ]
    return _extract_time_matches(text, patterns)


def _extract_time_matches(text: str, patterns: list[str]) -> list[str]:
    values: list[str] = []
    for pattern in patterns:
        for match in re.findall(pattern, text):
            value = clean_time_value(match)
            if looks_like_time_value(value) and value not in values:
                values.append(value)
    return values
