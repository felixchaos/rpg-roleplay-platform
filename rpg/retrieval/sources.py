"""retrieval.sources — RAG 召回族:本地 SQLite/JSON 源 + postgres worldbook/角色卡。

拆包(纯机械搬家):自 rpg/retrieval.py 逐字搬来,函数体零改动。
mutable 全局 _CHAR_ALIASES 与其读写方(_load_aliases / detect_mentioned_characters)同居本文件。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ._common import DB_PATH, FACT_DB, SUM_IDX

# 旧版默认剧本的本地角色索引已停用；运行期角色卡/世界书走数据库按 script_id scope 读取。
_CHAR_ALIASES: dict[str, str] = {}   # lazy-loaded


def _load_aliases():
    global _CHAR_ALIASES
    _CHAR_ALIASES = {}


def detect_mentioned_characters(text: str) -> list[str]:
    """返回文本中提到的规范角色名列表（去重）"""
    _load_aliases()
    found = set()
    for alias, canonical in _CHAR_ALIASES.items():
        if alias in text:
            found.add(canonical)
    return list(found)


def load_character_cards(names: list[str]) -> str:
    """Legacy local character cards are disabled; script-scoped cards come from Postgres."""
    return ""


def _sqlite_available(path: Path) -> bool:
    """SQLite 文件 + 父目录都得真实存在，避免 sqlite3.connect 自动创建空文件或抛错。"""
    try:
        return path.exists() and path.is_file() and path.stat().st_size > 0
    except Exception:
        return False


def bm25_search(query: str, top_k: int = 4, chapter_min: int | None = None, chapter_max: int | None = None) -> list[str]:
    """从 vectors.db 以 LIKE 关键词匹配，返回内容片段列表"""
    if not _sqlite_available(DB_PATH):
        return []
    # 提取 2+ 字的词元（中文直接切2-char n-gram，跳过标点）
    tokens = set()
    clean = re.sub(r"[^一-鿿\w]", " ", query)
    words = clean.split()
    for w in words:
        if len(w) >= 2:
            tokens.add(w)
    # 补充2-char n-grams（对中文短词友好）
    for i in range(len(clean) - 1):
        bg = clean[i:i+2]
        if re.match(r"[一-鿿]{2}", bg):
            tokens.add(bg)
    if not tokens:
        return []

    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur  = conn.cursor()
        results: list[tuple[str, str, int]] = []  # (chapter, content, score)
        seen_chunks: set[str] = set()

        # 合并为单条 SQL（原 N+1 循环最多 8 次 SELECT，现改为 1 次）
        tok_list = list(tokens)[:8]  # 最多用8个词元
        like_clauses = " OR ".join("content LIKE ?" for _ in tok_list)
        params: list[object] = [f"%{tok}%" for tok in tok_list]
        where = f"({like_clauses})"
        if chapter_min is not None:
            where += " AND chapter >= ?"
            params.append(chapter_min)
        if chapter_max is not None:
            where += " AND chapter <= ?"
            params.append(chapter_max)
        cur.execute(
            f"SELECT chapter, content, chunk_id FROM vectors WHERE {where} LIMIT {len(tok_list) * 6}",
            params,
        )
        for chapter, content, chunk_id in cur.fetchall():
            if chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            # 简单评分：命中词元数
            score = sum(1 for t in tokens if t in content)
            results.append((chapter, content, score))

        # 按评分排序，取 top_k
        results.sort(key=lambda x: x[2], reverse=True)
        snippets = []
        for chapter, content, _ in results[:top_k]:
            # 截取前300字防止 token 超限
            snippet = content[:300].strip()
            snippets.append(f"[第{chapter}章片段]\n{snippet}")
        return snippets
    except Exception:
        return []
    finally:
        # 修复连接泄漏:原 conn.close() 在 try 内,cur.execute/fetchall 抛异常时
        # 被 except 吞掉而跳过 close → SQLite 连接(fd + 读锁)泄漏,重复失败累积
        # 可致 fd 耗尽 / "database is locked"。移到 finally 保证所有路径释放。
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def load_recent_summaries(n: int = 3) -> str:
    """加载最近 n 章的摘要"""
    with open(SUM_IDX, encoding="utf-8") as f:
        data = json.load(f)
    summaries = data.get("summaries", {})
    # 按章节号降序取最近 n 个
    keys = sorted(summaries.keys(), key=lambda x: int(x), reverse=True)[:n]
    lines = []
    for k in reversed(keys):
        lines.append(f"第{k}章：{summaries[k]}")
    return "\n".join(lines)


def load_summaries_window(chapter_min: int | None, chapter_max: int | None, fallback_n: int = 3) -> str:
    """Load summaries near the resolved timeline anchor instead of always using book-tail chapters."""
    if chapter_min is None or chapter_max is None:
        return load_recent_summaries(n=fallback_n)
    with open(SUM_IDX, encoding="utf-8") as f:
        summaries = json.load(f).get("summaries", {})
    selected = []
    for key in sorted(summaries.keys(), key=lambda x: int(x)):
        chapter = int(key)
        if chapter_min <= chapter <= chapter_max:
            selected.append(f"第{key}章：{summaries[key]}")
    return "\n".join(selected[:6])


def load_chapter_facts(chapter_min: int | None, chapter_max: int | None, limit: int = 12) -> str:
    # task 79: 新存档 world.time 为空 → timeline_filter 没有 anchor → chapter_min/max=None。
    # 之前直接返 "" 导致 GM 收不到任何原著 ChapterFact,凭训练数据瞎编开局
    # (柏林 1914 / Aldnoah / 界冢伊奈帆 等都属于这种幻觉)。
    # 修: 至少回退到原著前 5 章,让新开局的 GM 拿到真正的开局事实。
    if chapter_min is None or chapter_max is None:
        chapter_min = 1
        chapter_max = 5
    if not _sqlite_available(FACT_DB):
        return ""
    try:
        conn = sqlite3.connect(str(FACT_DB))
    except Exception:
        return ""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT chapter, title, story_time_label, summary, events_json
            FROM chapter_facts
            WHERE chapter BETWEEN ? AND ?
            ORDER BY chapter
            LIMIT ?
        """, (chapter_min, chapter_max, limit))
        lines = []
        for chapter, title, time_label, summary, events_json in cur.fetchall():
            # events_json 是 LLM 抽取列,可能畸形 JSON 或非 dict 列表。逐章 try:单章坏
            # 只丢该章事件(仍出摘要),不让一个坏行丢掉整段章节摘要(与 worldbook 同隔离粒度)。
            try:
                events = json.loads(events_json or "[]")
                event_text = "；".join(event.get("event", "") for event in events[:2] if isinstance(event, dict) and event.get("event"))
            except (json.JSONDecodeError, TypeError, ValueError):
                event_text = ""
            lines.append(
                f"第{chapter}章《{title}》｜{time_label}\n"
                f"摘要：{summary[:180]}\n"
                f"事件：{event_text[:220]}"
            )
        return "\n\n".join(lines)
    except Exception:
        return ""
    finally:
        conn.close()


def _entry_chapter_min(row: dict) -> int:
    """task 122: 从 metadata 拿 entry 的 chapter_min (首次相关的章节)。
    没标过默认 chapter_min=1 (向后兼容,通用设定)。
    """
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        try:
            import json as _j
            meta = _j.loads(meta)
        except Exception:
            meta = {}
    try:
        v = (meta or {}).get("chapter_min")
        if v is not None:
            return int(v)
    except (TypeError, ValueError):
        pass
    return 1


def _load_worldbook_for_retrieval(
    script_id: int,
    query: str,
    top_k: int = 3,
    current_chapter_max: int | None = None,
    seen_out: set | None = None,
    save_id: int | None = None,
) -> str:
    """通用 worldbook 注入:
    - 高优先级 entries (priority>=80) 永远进 (世界观 / 设定集类)
    - 其它按 key 匹配命中 + priority 排序拿 top_k

    task 122: current_chapter_max 给定时 (当前 phase 的 chapter_max),
    过滤掉 metadata.chapter_min > current_chapter_max 的 entries —
    防止玩家在剧本早期看到后期专属世界设定(柏林暗流/特洛耶德 etc)。
    """
    from platform_app.db import connect as _connect
    try:
        with _connect() as db:
            # 存档级世界书 overlay(群反馈实锤:玩家在游戏内加的条目 GM 不认识——
            # 注入只查剧本表,save_worldbook_overlays 从未并入)。addition=玩家权威新增,
            # 不受章节泄漏过滤;retirement=剧本条目在本档退役。
            _ov_add: list = []
            _retired_ids: set = set()
            if save_id:
                try:
                    _ov_rows = db.execute(
                        "select kind, title, content, keys, priority, retired_entry_id "
                        "from save_worldbook_overlays where save_id=%s",
                        (int(save_id),),
                    ).fetchall() or []
                    for _o in _ov_rows:
                        if _o.get("kind") == "addition":
                            _ov_add.append(_o)
                        elif _o.get("kind") == "retirement" and _o.get("retired_entry_id"):
                            _retired_ids.add(int(_o["retired_entry_id"]))
                except Exception:
                    pass
            high = db.execute(
                "select id, title, content, metadata from worldbook_entries "
                "where script_id=%s and enabled=true and priority>=80 "
                "order by priority desc, id asc limit 10",
                (script_id,),
            ).fetchall() or []
            # task 122: 用当前 chapter 过滤
            if current_chapter_max is not None:
                high = [r for r in high if _entry_chapter_min(r) <= current_chapter_max]
            if _retired_ids:
                high = [r for r in high if int(r.get("id") or 0) not in _retired_ids]
            high = high[:5]  # 过滤后取 top 5
            # 存档新增·高优先(≥80)直接进常驻池(玩家权威,置顶)
            for _o in _ov_add:
                if int(_o.get("priority") or 0) >= 80:
                    high.insert(0, {"id": None, "title": _o.get("title"),
                                    "content": _o.get("content"), "metadata": {}})
            # 按 key 匹配
            matched = []
            if query and query.strip() and query != "开场":
                matched = db.execute(
                    "select id, title, content, keys, priority, metadata from worldbook_entries "
                    "where script_id=%s and enabled=true and priority<80 "
                    "order by priority desc, id asc limit 40",
                    (script_id,),
                ).fetchall() or []
                if current_chapter_max is not None:
                    matched = [r for r in matched if _entry_chapter_min(r) <= current_chapter_max]
                if _retired_ids:
                    matched = [r for r in matched if int(r.get("id") or 0) not in _retired_ids]
                matched = matched[:20]
                # 存档新增·普通优先级进 keys 匹配池(同结构参赛)
                for _o in _ov_add:
                    if int(_o.get("priority") or 0) < 80:
                        matched.insert(0, {"id": None, "title": _o.get("title"),
                                           "content": _o.get("content"),
                                           "keys": _o.get("keys") or [],
                                           "priority": _o.get("priority") or 50, "metadata": {}})
            picks: list[dict] = list(high)
            seen_titles = {r["title"] for r in picks}
            for r in matched:
                if r["title"] in seen_titles:
                    continue
                keys = r.get("keys") or []
                hit = any(isinstance(k, str) and k and k in query for k in keys)
                if hit:
                    picks.append(r)
                    seen_titles.add(r["title"])
                if len(picks) >= top_k + len(high):
                    break
        # dedup 用:把本函数选中条目的【唯一 id】记给 caller(非 title —— worldbook 常有同名/空 title,
        # 见 formatters._active_worldbook 注释),让 NovelWorldbookProvider 跳过重叠、不重复注入同一条。
        if seen_out is not None:
            for _r in picks:
                _rid = _r.get("id")
                if _rid is not None:
                    seen_out.add(f"db_{_rid}")
        if not picks:
            return ""
        lines = []
        for r in picks:  # type: ignore[assignment]
            lines.append(f"【{r['title']}】\n{(r['content'] or '')[:500]}")
        return "\n\n".join(lines)
    except Exception:
        return ""


def _load_script_character_cards(script_id: int, query: str, top_k: int = 5) -> str:
    """通用角色卡注入: 取该剧本的 character_cards, 命中 query 的优先, 否则取前 N。"""
    from platform_app.db import connect as _connect
    try:
        with _connect() as db:
            rows = db.execute(
                "select name, identity, personality, appearance "
                "from character_cards where script_id=%s and enabled=true "
                "order by priority desc, id asc limit 20",
                (script_id,),
            ).fetchall() or []
        if not rows:
            return ""
        # 命中 query 的优先
        scored = []
        for r in rows:
            name = (r.get("name") or "")
            score = 5 if (name and name in (query or "")) else 0
            scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        picks = [r for _, r in scored[:top_k]]
        lines = []
        for r in picks:
            bits = [r.get("name", "")]
            if r.get("identity"):
                bits.append(r["identity"])
            if r.get("personality"):
                bits.append(r["personality"][:120])
            lines.append("· " + " | ".join(b for b in bits if b))
        return "\n".join(lines)
    except Exception:
        return ""
