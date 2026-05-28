"""state/parsers.py — 文本解析 helpers (_split_label, _split_items, _clean_item, _split_relation, _parse_assignment, _parse_question)"""
from __future__ import annotations
import re


def _clean_item(text: str) -> str:
    return re.sub(r"\s+", " ", str(text).strip(" \n\t:：-—")).strip()


def _split_label(text: str) -> tuple[str, str]:
    for sep in ("：", ":"):
        if sep in text:
            key, value = text.split(sep, 1)
            return _clean_item(key), _clean_item(value)
    return text, text


def _split_items(text) -> list[str]:
    """Bug 5 / retest #5：
    - value 已是 list / tuple → 直接清洗（不再切）
    - 字符串切分策略：
      * 顿号「、」、分号「; ；」、换行 → 总是切（明确列表分隔）
      * 中英文逗号「, ，」→ 只在结果都是『短词』（每段 ≤ 12 字、不含其他标点）时才切，
        避免完整事件句『Cinder 在东侧轨道触发巨响，惊动了不明生物』被劈成两条。
    """
    if isinstance(text, (list, tuple)):
        return [_clean_item(str(x)) for x in text if _clean_item(str(x))]
    if text is None:
        return []
    raw = str(text).strip()
    if not raw:
        return []
    # 1. 先用强分隔符切：顿号/分号/换行
    strong_parts = [p for p in re.split(r"[、;；\n]\s*", raw) if p.strip()]
    # 2. 在 strong parts 上做逗号细切，但仅当每一片都"短词样"
    out: list[str] = []
    for part in strong_parts:
        sub_parts = [p for p in re.split(r"[,，]\s*", part) if p.strip()]
        if len(sub_parts) > 1 and all(
            len(_clean_item(s)) <= 12 and not re.search(r"[。！？!?]", s)
            for s in sub_parts
        ):
            # 短词列表：例 "Torch ×1, Shortsword, Shortbow ×1"
            out.extend(_clean_item(s) for s in sub_parts if _clean_item(s))
        else:
            # 含完整句号/问号/感叹号，或片段超长 → 当作整句保留
            cleaned = _clean_item(part)
            if cleaned:
                out.append(cleaned)
    return out


def _split_relation(text: str) -> tuple[str, str]:
    for sep in ("：", ":", "->", "→", "-"):
        if sep in text:
            left, right = text.split(sep, 1)
            return _clean_item(left), _clean_item(right)
    return "", ""


def _parse_assignment(text: str) -> tuple[str, str]:
    from state.path_ops import _clean_path
    text = _clean_item(text)
    for sep in ("+=", "=", "：", ":"):
        if sep in text:
            left, right = text.split(sep, 1)
            return _clean_path(left), _clean_item(right)
    return "", text


def _parse_question(value: str) -> tuple[str, list[str]]:
    text = _clean_item(value)
    if not text:
        return "", []
    question = text
    option_text = ""
    if "｜" in text:
        question, option_text = text.split("｜", 1)
    elif "|" in text:
        question, option_text = text.split("|", 1)
    if not option_text:
        match = re.search(r"(.*?)(?:选项|可选|choices?)[:：]\s*(.+)$", text, re.I)
        if match:
            question = match.group(1)
            option_text = match.group(2)
    if option_text:
        option_text = re.sub(r"^(?:选项|可选|choices?)[:：]\s*", "", option_text, flags=re.I)
    options = [_clean_item(x) for x in re.split(r"[、,，/]|(?:\s+or\s+)", option_text) if _clean_item(x)]
    return _clean_item(question), options[:4]
