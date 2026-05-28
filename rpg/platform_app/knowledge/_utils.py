from __future__ import annotations

import re
from typing import Any


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _cursor_int(value: str | int | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _require_script(db, user_id: int, script_id: int) -> dict[str, Any]:
    row = db.execute("select * from scripts where id = %s and owner_id = %s", (script_id, user_id)).fetchone()
    if not row:
        raise ValueError("无权访问该剧本")
    return row


def _slugify(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z_\-一-鿿]+", "-", text.strip()).strip("-").lower()
    return slug or "book"


def _keys_for(title: str, content: str) -> list[str]:
    values = {title}
    values.update(re.findall(r"[一-鿿A-Za-z0-9]{2,12}", f"{title} {content or ''}")[:8])
    return [item for item in values if item][:10]


def _auto_extract_keys(title: str, content: str, max_keys: int = 20) -> list[str]:
    """task 80: 通用命名实体/关键词提取 — 不依赖任何特定书的词表。

    简单启发式:
    - 提取所有 markdown **加粗** 内容
    - 提取所有 3-8 个汉字 (或 2-4 个英文词) 的"专名样本": 出现 >=2 次,
      非常见动词/形容词,看起来像专名 (大写首字母 / 中间无标点)
    - 加上 title 本身作为 key
    """
    keys: list[str] = []
    if title:
        keys.append(title)
    # 加粗词
    for m in re.findall(r"\*\*([^*\n]{2,12})\*\*", content):
        if m and m not in keys:
            keys.append(m)
    # 中文专名: 连续 2-6 个汉字, 出现 >=2 次
    from collections import Counter
    cn_terms = re.findall(r"[一-鿿]{2,6}", content)
    counter = Counter(cn_terms)
    # 过滤一些极常见的非专名 (通用,任何书都适用)
    stop_words = {"的", "了", "和", "是", "在", "就是", "我们", "他们", "这个", "那个",
                  "什么", "怎么", "可以", "因为", "所以", "但是", "不过", "如果", "已经",
                  "应该", "需要", "可能", "或者", "甚至", "于是", "其中"}
    for term, freq in counter.most_common(60):
        if freq < 2 or term in stop_words:
            continue
        if len(term) >= 2 and term not in keys:
            keys.append(term)
        if len(keys) >= max_keys:
            break
    # 英文专名 (大写开头, 长度 >=3, 出现 >=2)
    en_terms = re.findall(r"\b[A-Z][A-Za-z]{2,}\b", content)
    en_counter = Counter(en_terms)
    for term, freq in en_counter.most_common(20):
        if freq < 2:
            continue
        if term not in keys:
            keys.append(term)
        if len(keys) >= max_keys:
            break
    return keys[:max_keys]


def _wb(title: str, keys: list[str], priority: int, content: str) -> dict[str, Any]:
    return {
        "title": title,
        "keys": keys,
        "regex_keys": [],
        "priority": priority,
        "token_budget": 600,
        "insertion_position": "worldbook",
        "sticky_turns": 0,
        "cooldown_turns": 0,
        "probability": 100.0,
        "character_filter": [],
        "scene_filter": [],
        "content": content or "",
    }


def _chunk_text(text: str) -> list[str]:
    import re as _re
    from platform_app.knowledge._constants import CHUNK_CHARS, CHUNK_OVERLAP
    text = _re.sub(r"\n{3,}", "\n\n", text or "").strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        if end < len(text):
            window = text[start:end]
            split_at = max(window.rfind("\n\n"), window.rfind("。"), window.rfind("！"), window.rfind("？"))
            if split_at > CHUNK_CHARS // 2:
                end = start + split_at + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _query_tokens(query: str) -> list[str]:
    import re as _re
    text = _re.sub(r"[^一-鿿A-Za-z0-9_]", " ", query or "")
    tokens = {part for part in text.split() if len(part) >= 2}
    compact = _re.sub(r"\s+", "", text)
    for index in range(max(0, len(compact) - 1)):
        bg = compact[index:index + 2]
        if _re.fullmatch(r"[一-鿿]{2}", bg):
            tokens.add(bg)
    return sorted(tokens, key=len, reverse=True)[:16]


def _retrieved_chunks_payload(text: str) -> list[dict[str, Any]]:
    blocks = [block.strip() for block in (text or "").split("\n\n") if block.strip()]
    return [{"preview": block[:240], "chars": len(block)} for block in blocks[:12]]


def _worldbook_seed_entries(world: dict[str, Any]) -> list[dict[str, Any]]:
    """task 80: 通用底座 — 支持两种 world 形态:
    1) 新格式 (script_id scoped): {"entries": [{title, content}, ...]} — 直接落库,
       keys 由 _auto_extract_keys 通用提取(命名实体/高频专名)。
    2) 老格式 (柏林书 indexes/world.json): {setting, current_situation, key_factions,
       key_concepts, current_berlin} — 保留兼容路径,但不再硬编码 keys。
    """
    entries: list[dict[str, Any]] = []
    # 路径 1: 新格式 (worldbook_entries 已经存在的剧本)
    if isinstance(world.get("entries"), list):
        for i, ent in enumerate(world["entries"]):
            title = (ent.get("title") or "").strip() or f"设定条目 {i+1}"
            content = (ent.get("content") or "").strip()
            if not content:
                continue
            # 通用 keys: title 拆词 + content 抽专名
            keys = _auto_extract_keys(title, content)
            entries.append(_wb(title, keys, 90, content))
        return entries
    # 路径 2: 老格式 兼容 (柏林 indexes/world.json)
    if world.get("setting"):
        entries.append(_wb("世界基础设定", _auto_extract_keys("世界基础设定", world["setting"]),
                           100, world["setting"]))
    if world.get("current_situation"):
        entries.append(_wb("当前局势", _auto_extract_keys("当前局势", world["current_situation"]),
                           96, world["current_situation"]))
    for title, content in (world.get("key_factions") or {}).items():
        entries.append(_wb(title, _keys_for(title, content), 82, content))
    for title, content in (world.get("key_concepts") or {}).items():
        entries.append(_wb(title, _keys_for(title, content), 78, content))
    return entries
