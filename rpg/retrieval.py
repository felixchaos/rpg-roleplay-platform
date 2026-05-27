"""
retrieval.py — 两段式召回
  1. 检测输入中提到的角色 → 注入角色卡（characters.json）
  2. BM25 关键词搜索 vectors.db → 注入相关章节片段
"""
from __future__ import annotations
import json
import re
import sqlite3
from pathlib import Path
from timeline_index import bootstrap_timeline_from_summaries, timeline_filter_for_label

BASE     = Path(__file__).parent
DB_PATH  = BASE.parent / ".webnovel" / "vectors.db"
FACT_DB  = BASE.parent / ".webnovel" / "chapter_facts.db"
CHAR_IDX = BASE / "indexes" / "characters.json"
WORLD_IDX= BASE / "indexes" / "world.json"
SUM_IDX  = BASE / "indexes" / "summaries.json"

# 角色全名 + 别名 → 规范名
_CHAR_ALIASES: dict[str, str] = {}   # lazy-loaded
_TIMELINE_READY = False

def _load_aliases():
    global _CHAR_ALIASES
    if _CHAR_ALIASES:
        return
    with open(CHAR_IDX, "r", encoding="utf-8") as f:
        chars = json.load(f)["characters"]
    for name, info in chars.items():
        _CHAR_ALIASES[name] = name
        for alias in info.get("aliases", []):
            _CHAR_ALIASES[alias] = name


def detect_mentioned_characters(text: str) -> list[str]:
    """返回文本中提到的规范角色名列表（去重）"""
    _load_aliases()
    found = set()
    for alias, canonical in _CHAR_ALIASES.items():
        if alias in text:
            found.add(canonical)
    return list(found)


def load_character_cards(names: list[str]) -> str:
    """将角色卡格式化为可注入的文本块"""
    if not names:
        return ""
    with open(CHAR_IDX, "r", encoding="utf-8") as f:
        chars = json.load(f)["characters"]
    lines = []
    for name in names:
        if name not in chars:
            continue
        c = chars[name]
        sample = "；".join(c.get("sample_dialogue", [])[:3])
        lines.append(
            f"【{name}】\n"
            f"  身份：{c['identity']}\n"
            f"  性格：{c['personality']}\n"
            f"  说话风格：{c['speech_style']}\n"
            f"  当前状态：{c['current_status']}\n"
            f"  台词示例：{sample}"
        )
    return "\n\n".join(lines)


def _ensure_timeline_ready():
    global _TIMELINE_READY
    if _TIMELINE_READY:
        return
    try:
        bootstrap_timeline_from_summaries()
    except Exception:
        pass
    _TIMELINE_READY = True


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

    try:
        conn = sqlite3.connect(str(DB_PATH))
        cur  = conn.cursor()
        results: list[tuple[str, str, int]] = []  # (chapter, content, score)
        seen_chunks: set[str] = set()

        for tok in list(tokens)[:8]:  # 最多用8个词元
            params: list[object] = [f"%{tok}%"]
            where = "content LIKE ?"
            if chapter_min is not None:
                where += " AND chapter >= ?"
                params.append(chapter_min)
            if chapter_max is not None:
                where += " AND chapter <= ?"
                params.append(chapter_max)
            cur.execute(
                f"SELECT chapter, content, chunk_id FROM vectors WHERE {where} LIMIT 6",
                params,
            )
            for chapter, content, chunk_id in cur.fetchall():
                if chunk_id in seen_chunks:
                    continue
                seen_chunks.add(chunk_id)
                # 简单评分：命中词元数
                score = sum(1 for t in tokens if t in content)
                results.append((chapter, content, score))

        conn.close()
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


def load_recent_summaries(n: int = 3) -> str:
    """加载最近 n 章的摘要"""
    with open(SUM_IDX, "r", encoding="utf-8") as f:
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
    with open(SUM_IDX, "r", encoding="utf-8") as f:
        summaries = json.load(f).get("summaries", {})
    selected = []
    for key in sorted(summaries.keys(), key=lambda x: int(x)):
        chapter = int(key)
        if chapter_min <= chapter <= chapter_max:
            selected.append(f"第{key}章：{summaries[key]}")
    return "\n".join(selected[:6])


def load_chapter_facts(chapter_min: int | None, chapter_max: int | None, limit: int = 5) -> str:
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
            events = json.loads(events_json or "[]")
            event_text = "；".join(event.get("event", "") for event in events[:2] if event.get("event"))
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


def _is_default_mumu_script(script_id: int | None) -> bool:
    """task 42：判断 script_id 是不是 MuMuAINovel 默认剧本。
    .webnovel/*.db + indexes/*.json + indexes/characters.json 这些 SQLite/JSON 文件都是
    给默认柏林剧本用的；新导入剧本不应该读它们。

    判定：scripts 行的 source_path 以 'rpg/indexes' 开头（workspace.ensure_default 写死的）
    或 title == BASE_TITLE《我蕾穆丽娜不爱你》。任何 DB/查询异常一律保守返回 False
    （宁可丢一点默认上下文也不能让导入剧本被污染）。
    """
    if not script_id:
        return False
    try:
        from platform_app.db import connect as _connect
        with _connect() as db:
            row = db.execute(
                "select title, source_path from scripts where id = %s",
                (int(script_id),),
            ).fetchone()
        if not row:
            return False
        src = str(row.get("source_path") or "")
        title = str(row.get("title") or "")
        if src.startswith("rpg/indexes"):
            return True
        if title == "《我蕾穆丽娜不爱你》":
            return True
        return False
    except Exception:
        return False


# task 42：postgres chapter_facts.story_time_label 在过去的索引器跑里被错误地
# 复制了默认柏林剧情的 label（如"图卢兹失守后次日，柏林内城"）到导入剧本的行上。
# 数据迁移修不掉所有历史脏数据，retrieve 时再防一道——非默认 script 读到的 fact
# 如果 story_time_label 含柏林 token，就抹掉这个字段，避免泄漏到 GM 上下文。
_DEFAULT_NOVEL_LEAK_TOKENS = (
    "柏林", "图卢兹", "哈布斯堡", "蛇信", "薇瑟", "扎兹巴鲁姆",
    "蕾穆丽娜", "斯雷因", "伊奈帆", "甲胄骑士", "Kataphrakt",
    "调令伪造", "娅赛兰", "韵子", "黎骨月", "迪卡亚",
    "赫克勒斯", "特洛耶德", "薛克",
)


def _strip_default_novel_leakage(text: str) -> str:
    """对一段已生成的检索文本做后处理：把含『默认柏林剧情』token 的行删掉。
    用于 retrieve_runtime_context 返回的 postgres 检索（如果 chapter_facts 行
    的 story_time_label 或 chunk content 残留默认柏林内容）。"""
    if not text:
        return text
    lines = text.splitlines()
    cleaned: list[str] = []
    for line in lines:
        if any(tok in line for tok in _DEFAULT_NOVEL_LEAK_TOKENS):
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def retrieve_context(user_input: str, verbose: bool = False, state=None, user_id: int | None = None,
                     script_id: int | None = None) -> str:
    """
    组合召回，返回注入 GM system prompt 的上下文字符串。
    预算约 800 token：角色卡 ~400 + 章节片段 ~300 + 摘要 ~100

    task 42：传入 script_id 后会判断是否是 MuMuAINovel 默认剧本。
    不是默认剧本（用户导入的剧本）→ 跳过所有 .webnovel SQLite + indexes JSON 来源
    （那些都是默认剧本的原文/角色卡/摘要/ChapterFact，混入会污染导入剧本的 GM 上下文）。
    只保留 postgres 来源（已按 script_id 严格 scope）+ 时间线锚点说明。
    """
    parts: list[str] = []
    _ensure_timeline_ready()
    is_default = _is_default_mumu_script(script_id) if script_id else True  # 兼容老 caller 不传 script_id 时按默认走
    timeline_filter = None
    if state is not None:
        world = state.data.get("world", {})
        timeline = world.get("timeline", {})
        pending = timeline.get("pending_jump") or {}
        label = pending.get("to") or world.get("time", "")
        timeline_filter = timeline_filter_for_label(label)
        if not timeline_filter.get("anchor_chapter"):
            previous = (timeline.get("last_transition") or {}).get("from")
            if previous:
                timeline_filter = timeline_filter_for_label(previous)
        if is_default:
            # 默认 MuMu 剧本才显示『原著锚点』和章节窗口；非默认剧本这些字段都是 None/无意义。
            parts.append(
                "=== 时间线检索锚点 ===\n"
                f"当前时间：{world.get('time', '')}\n"
                f"待确认跳跃：{pending.get('to', '无')}\n"
                f"本轮检索标签：{label}\n"
                f"原著锚点：第{timeline_filter.get('anchor_chapter')}章 · {timeline_filter.get('anchor_event')}\n"
                f"检索章节窗口：{timeline_filter.get('chapter_min')} - {timeline_filter.get('chapter_max')}"
            )
        else:
            parts.append(
                "=== 时间线检索锚点 ===\n"
                f"当前时间：{world.get('time', '')}\n"
                f"待确认跳跃：{pending.get('to', '无')}\n"
                f"本轮检索标签：{label}\n"
                "来源：当前导入剧本（不读默认 MuMu 原著时间线）"
            )

        # SQLite ChapterFact 只给默认剧本（.webnovel/chapter_facts.db 是 MuMu 原著）
        if is_default:
            facts_text = load_chapter_facts(timeline_filter.get("chapter_min"), timeline_filter.get("chapter_max"))
            if facts_text:
                parts.append("=== ChapterFact时间线 ===\n" + facts_text)
        try:
            from platform_app.knowledge import retrieve_runtime_context

            pg_context = retrieve_runtime_context(
                user_input,
                chapter_min=timeline_filter.get("chapter_min") if is_default else None,
                chapter_max=timeline_filter.get("chapter_max") if is_default else None,
                top_k=3,
                user_id=user_id,
            )
            if pg_context:
                # 非默认剧本：抹掉历史脏数据里残留的默认柏林 token 行（防御性）
                if not is_default:
                    pg_context = _strip_default_novel_leakage(pg_context)
                if pg_context.strip():
                    parts.append(pg_context)
        except Exception:
            pass

    # 1. 角色卡（默认 indexes/characters.json 是 MuMu 角色；非默认剧本跳过，避免泄漏）
    snippets: list[str] = []
    if is_default:
        char_names = detect_mentioned_characters(user_input)
        char_text  = load_character_cards(char_names)
        if char_text:
            parts.append("=== 相关角色 ===\n" + char_text)

        # 2. BM25 章节片段（.webnovel/vectors.db 是 MuMu 原著 chunks，仅默认走）
        snippets = bm25_search(
            user_input,
            top_k=3,
            chapter_min=timeline_filter.get("chapter_min") if timeline_filter else None,
            chapter_max=timeline_filter.get("chapter_max") if timeline_filter else None,
        )
        if snippets:
            parts.append("=== 相关原文片段 ===\n" + "\n\n".join(snippets))

        # 3. 章节摘要（indexes/summaries.json 是 MuMu，仅默认走）
        recent = load_summaries_window(
            timeline_filter.get("chapter_min") if timeline_filter else None,
            timeline_filter.get("chapter_max") if timeline_filter else None,
        )
        if recent:
            parts.append("=== 最近剧情摘要 ===\n" + recent)
    else:
        char_names = []  # 留作 verbose 日志兼容

    if verbose:
        print(f"[召回] 默认剧本：{is_default} 角色：{char_names if is_default else '(跳过)'}  BM25片段：{len(snippets)}条")

    return "\n\n".join(parts)
