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
    with open(CHAR_IDX, encoding="utf-8") as f:
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
    with open(CHAR_IDX, encoding="utf-8") as f:
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
    """task 80: 通用底座 — 不再区分"默认 MuMu 剧本"。

    历史: 早期 .webnovel/*.db + indexes/*.json 是为单一柏林剧本预生成的本地数据,
    现在所有剧本数据都该在 postgres (chapter_facts + document_chunks +
    worldbook_entries + character_cards),按 script_id scope 严格隔离。
    特殊化"默认剧本"会让任何巧合命中 title 的脚本走到本地 sqlite 路径,
    引入污染。统一返 False = 永远走 postgres 路径。

    保留函数签名是为了下游 callers 兼容。
    """
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


# task 117: 算法层 phase 推导 — 不硬编码"第一章"/"火星"/"柏林"。
# 当 world.time 空 / state 干净时,从 save.active_phase_index + save_phase_digests
# 或 fallback 到 script 级 phase_digests 拿当前 phase 的 chapter_range,
# 让 BM25 / worldbook 检索被自动限制到正确的剧情阶段,而不是检索整本书。
# 通用于任意小说 — 只要剧本导入流程跑过 phase_digest 聚合 (task 85),就有数据。
def _resolve_active_phase_range(save_id: int | None, script_id: int | None) -> dict | None:
    """返回当前 phase 的 {chapter_min, chapter_max, phase_label, summary},
    或 None (DB 没数据时)。

    算法:
      1. 如果 save_id 给了 → 读 game_saves.active_phase_index
         - 如果该 index 在 save_phase_digests 有 row → 拿它的 phase_label 去
           script 级 phase_digests 查 chapter_min/max + summary
         - 否则继续到 step 2
      2. fallback: script 级 phase_digests 按 (chapter_min, chapter_max) ASC
         取第一个 → 这就是"剧本最早期的 phase"
    """
    if not script_id:
        return None
    try:
        from platform_app.db import connect as _conn
        from platform_app.db import init_db as _init
        _init()
        with _conn() as _db:
            active_phase_label = ""
            if save_id:
                _gs = _db.execute(
                    "select active_phase_index from game_saves where id = %s",
                    (save_id,),
                ).fetchone()
                if _gs and _gs.get("active_phase_index") is not None:
                    _spd = _db.execute(
                        "select phase_label from save_phase_digests "
                        "where save_id = %s and phase_index = %s limit 1",
                        (save_id, _gs["active_phase_index"]),
                    ).fetchone()
                    if _spd and _spd.get("phase_label"):
                        active_phase_label = _spd["phase_label"]
            # 优先精准匹配 active phase
            row = None
            if active_phase_label:
                row = _db.execute(
                    "select phase_label, chapter_min, chapter_max, summary "
                    "from phase_digests where script_id = %s and phase_label = %s "
                    "order by chapter_min asc limit 1",
                    (script_id, active_phase_label),
                ).fetchone()
            # fallback: 剧本最早期 phase (按 chapter_min asc, chapter_max asc)
            if not row:
                row = _db.execute(
                    "select phase_label, chapter_min, chapter_max, summary "
                    "from phase_digests where script_id = %s "
                    "and chapter_min is not null and chapter_max is not null "
                    "order by chapter_min asc, chapter_max asc limit 1",
                    (script_id,),
                ).fetchone()
            if row and row.get("chapter_min") and row.get("chapter_max"):
                return {
                    "chapter_min": int(row["chapter_min"]),
                    "chapter_max": int(row["chapter_max"]),
                    "phase_label": str(row.get("phase_label") or ""),
                    "summary": str(row.get("summary") or ""),
                }
    except Exception:
        pass
    return None


# task 125: 强制拉 anchor 章节的真实原文,解决 GM "拿到标题没拿到内容"问题。
# 不依赖 BM25 命中 (开场 turn=0 时 query 太弱),直接按 chapter_index 取 chunks。
def _load_anchor_chapter_text(script_id: int, chapter_min: int, chapter_max: int | None = None, max_chars: int = 2400) -> str:
    """取 chapter_min..chapter_max 范围内前几章的实际原文 (从 document_chunks),
    供 GM 在开场/低 turn 时严格基于原著重写,不凭空捏造。
    """
    if not script_id or not chapter_min:
        return ""
    cmax = chapter_max if chapter_max is not None else chapter_min
    # 限制窗口:开场只需要 anchor 当前章 + 紧邻 1-2 章
    cmax = min(int(cmax), int(chapter_min) + 2)
    try:
        from platform_app.db import connect as _connect
        with _connect() as db:
            rows = db.execute(
                """
                select chapter_index, chunk_index, content
                from document_chunks
                where script_id = %s and chapter_index between %s and %s
                order by chapter_index asc, chunk_index asc
                limit 12
                """,
                (int(script_id), int(chapter_min), int(cmax)),
            ).fetchall() or []
        if not rows:
            return ""
        # 按章节聚合,每章拼 2-3 个 chunk,但总长度限 max_chars
        out_lines = []
        used = 0
        last_ch = None
        for r in rows:
            ch = int(r["chapter_index"])
            content = (r["content"] or "").strip()
            if not content:
                continue
            if ch != last_ch:
                out_lines.append(f"--- 第 {ch} 章原文片段 ---")
                last_ch = ch
            piece = content[: max(0, max_chars - used)]
            out_lines.append(piece)
            used += len(piece)
            if used >= max_chars:
                break
        return "\n".join(out_lines)
    except Exception:
        return ""


def _extract_style_sample(text: str, n_sentences: int = 5, max_chars: int = 500) -> str:
    """task 131-B: 从锚点章节原文抽 5 句作 style anchor 给 GM 学句法 / 节奏 / 词汇。
    简单算法 — 不依赖 LLM,直接按句号切,挑长度适中的句子(避免短促对白和长段景物):
      · 10 < len(s) < 60 (有信息密度,不是 '。' 或 '嗯。')
      · 不要句首是描写性符号(去掉对话/旁白引导)
      · 优先取前 N 段(不要从结尾抽,通常是高潮段不代表整本)
    通用 — 适用任何小说,不挑特定书。
    """
    if not text or len(text) < 80:
        return ""
    import re as _re
    # 去除 markdown 头 / 元数据
    body = _re.sub(r"^---.*?---\s*", "", text, flags=_re.DOTALL).strip()
    body = _re.sub(r"^#+\s*[^\n]+\n", "", body, count=2).strip()  # 剥 ## 第 X 章 标题
    # 拿前 1500 字
    body = body[:1500]
    sentences = _re.split(r"(?<=[。！？.!?])\s*", body)
    picked = []
    used = 0
    for s in sentences:
        s = s.strip().lstrip("【】").strip()
        if 10 <= len(s) <= 60 and not s.startswith(("---", "#", "【")):
            piece = s
            if used + len(piece) + 4 > max_chars:
                break
            picked.append(piece)
            used += len(piece) + 1
            if len(picked) >= n_sentences:
                break
    return "\n".join(picked) if picked else ""


def _resolve_save_id_from_user(user_id: int | None) -> int | None:
    """从 user_id 拿 active save_id (runtime_checkouts)。"""
    if not user_id:
        return None
    try:
        from platform_app.db import connect as _conn
        from platform_app.db import init_db as _init
        _init()
        with _conn() as _db:
            r = _db.execute(
                "select save_id from runtime_checkouts where user_id = %s order by updated_at desc limit 1",
                (user_id,),
            ).fetchone()
            return int(r["save_id"]) if r and r.get("save_id") else None
    except Exception:
        return None


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
    # task 117: 算法 phase fallback — 当 state.world.time 空(turn=0 等)时 timeline_filter
    # 拿不到 chapter window,从 phase_digests 拿该 save 当前 phase 的 chapter_range。
    # 这样 BM25/worldbook 不会全文检索整本书。
    phase_range = None
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
        # 仍然拿不到 chapter window → 走 phase 算法 fallback
        if not timeline_filter.get("chapter_min") or not timeline_filter.get("chapter_max"):
            _sid_for_phase = _resolve_save_id_from_user(user_id)
            phase_range = _resolve_active_phase_range(_sid_for_phase, script_id)
            if phase_range:
                # 覆盖 timeline_filter 的 chapter 范围,让下游 BM25/worldbook 检索按 phase 限制
                timeline_filter = dict(timeline_filter or {})
                timeline_filter["chapter_min"] = phase_range["chapter_min"]
                timeline_filter["chapter_max"] = phase_range["chapter_max"]
                # 注入 phase 摘要,给 GM 当前阶段的整体描述
                if phase_range.get("phase_label") or phase_range.get("summary"):
                    parts.append(
                        "=== 当前剧情阶段 (phase fallback) ===\n"
                        f"阶段: {phase_range.get('phase_label', '')}\n"
                        f"章节范围: 第{phase_range['chapter_min']}-{phase_range['chapter_max']}章\n"
                        f"阶段概要: {(phase_range.get('summary') or '')[:600]}"
                    )
        # task 125: 注入 anchor 章节的真实原文片段 — 解决 GM 只拿到标题没拿到内容,
        # 自由发挥编出"防空洞 / Kataphrakt"这种与原著无关的设定。
        # 当 state.world.timeline.anchor_chapter_range 给定 (用户选了 birthpoint),
        # 或者 turn=0 / history 空时,强制拉前 1-3 章原文。
        anchor_range = (timeline.get("anchor_chapter_range") or [])
        anchor_min = None
        anchor_max = None
        if isinstance(anchor_range, list) and len(anchor_range) >= 1:
            try:
                anchor_min = int(anchor_range[0])
                anchor_max = int(anchor_range[1]) if len(anchor_range) > 1 else anchor_min
            except (TypeError, ValueError):
                pass
        # turn=0 / 空 history → 也走章节原文注入 (用 phase 起始章)
        is_opening = (int(state.data.get("turn", 0) or 0) == 0
                      and not (state.data.get("history") or []))
        if anchor_min is None and is_opening and (timeline_filter or {}).get("chapter_min"):
            anchor_min = int(timeline_filter["chapter_min"])
            anchor_max = anchor_min  # 只拉锚点 1 章
        if anchor_min and script_id:
            anchor_text = _load_anchor_chapter_text(int(script_id), anchor_min, anchor_max, max_chars=2400)
            if anchor_text:
                # task 131: 明确标记"风格 + 骨架参考,不是必须复现的戏剧强度"
                parts.append(
                    "=== 锚点章节原文 (双重用途, 严格区分) ===\n"
                    "【骨架用途】时空 / 角色 / 事件骨架 — 必须保持。\n"
                    "【风格用途】学作者句法 / 用词 / 节奏 — 模仿。\n"
                    "**不模仿情绪强度** — 原文极端事件密度高不代表你本轮要复制那种密度,\n"
                    "玩家本轮输入的戏剧强度才决定你本轮的戏剧强度。\n\n"
                    + anchor_text
                )
                # task 131-B: 抽出原文前几段当作"作者文风样本",最高优先级 style anchor
                style_sample = _extract_style_sample(anchor_text)
                if style_sample:
                    parts.append(
                        "=== 作者文风样本 (style anchor, 仅学句法/词汇/节奏, 不学情绪强度) ===\n"
                        + style_sample
                    )
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
        # task 136: 世界线收束机制 — 注入【当前阶段待发生锚点】
        # 让 GM 知道接下来原著该发生哪几个关键事件,主动设计场景把剧情往那里引。
        # 玩家可以改变事件发生方式,但 GM 必须想办法让锚点的【核心结果】发生。
        try:
            _sid_for_anchors = _resolve_save_id_from_user(user_id)
            if _sid_for_anchors:
                from agents.anchor_seed_agent import (
                    list_pending_for_phase,
                    summarize_save_anchor_state,
                )
                # 优先按当前 phase 过滤; 没有 phase 信息时按 chapter window 过滤
                _phase_label = (phase_range or {}).get("phase_label") if phase_range else None
                if not _phase_label:
                    _phase_label = (timeline_filter or {}).get("phase_label")
                _ch_min = (timeline_filter or {}).get("chapter_min")
                _ch_max = (timeline_filter or {}).get("chapter_max")
                anchors = list_pending_for_phase(
                    _sid_for_anchors, _phase_label,
                    limit=6, chapter_min=_ch_min, chapter_max=_ch_max,
                )
                if not anchors and (_ch_min or _ch_max):
                    # phase 过滤无结果时, 退到只按 chapter 范围
                    anchors = list_pending_for_phase(
                        _sid_for_anchors, None, limit=6,
                        chapter_min=_ch_min, chapter_max=_ch_max,
                    )
                summary = summarize_save_anchor_state(_sid_for_anchors)
                if anchors:
                    lines = [
                        "=== 世界线收束·当前阶段待发生锚点 ===",
                        f"整体状态: pending={summary['pending']} occurred={summary['occurred']} "
                        f"variant={summary['variant']} superseded={summary['superseded']} "
                        f"avg_drift={summary['avg_drift']}",
                        "原著在此阶段必须发生的事件 (发生方式可变,事件结果不可省):",
                    ]
                    for i, a in enumerate(anchors, 1):
                        fatal_tag = "【死神来了·必发生】" if a.get("is_fatal") else ""
                        mp = a.get("must_preserve") or []
                        mv = a.get("may_vary") or []
                        lines.append(
                            f"{i}. [chapter {a['chapter']}, importance {a['importance']}, "
                            f"key={a['anchor_key']}] {fatal_tag}\n"
                            f"   {a['summary']}\n"
                            f"   · 必须保留: {', '.join(str(x) for x in mp) or '(参见事件描述)'}\n"
                            f"   · 可变: {', '.join(str(x) for x in mv) or '(地点/时机/旁观者)'}"
                        )
                    lines.append(
                        "操作指引: 当锚点自然发生时调 mark_anchor_satisfied(anchor_key, "
                        "how_it_happened, drift_score)。玩家偏离时,1-3 轮内用命运式手段"
                        "(巧合/误会/他人介入)把剧情拉回最近锚点。"
                    )
                    parts.append("\n".join(lines))
                elif summary.get("total", 0) > 0:
                    parts.append(
                        "=== 世界线收束·当前阶段 ===\n"
                        f"本阶段无 pending 锚点(已全部发生或被绕过)。"
                        f"整体: occurred={summary['occurred']} "
                        f"variant={summary['variant']} avg_drift={summary['avg_drift']}"
                    )
        except Exception as _anchor_err:
            print(f"[retrieval] pending_anchors 注入失败 (非致命): {_anchor_err}")

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

    # task 80/82: 通用底座 — 任何剧本都从 postgres 拉 worldbook + 角色卡, 不再依赖
    # indexes/*.json (那是单一书的固化资源)。
    if script_id:
        try:
            # task 122: 把当前 phase 的 chapter_max 透传给 worldbook 过滤,
            # 防止柏林暗流/中后期专属设定泄漏到火星线早期玩家
            _wb_chmax = (timeline_filter or {}).get("chapter_max") if timeline_filter else None
            wb_text = _load_worldbook_for_retrieval(
                script_id, user_input, top_k=3, current_chapter_max=_wb_chmax,
            )
            if wb_text:
                parts.append("=== 世界设定 (worldbook) ===\n" + wb_text)
        except Exception:
            pass
        try:
            cc_text = _load_script_character_cards(script_id, user_input, top_k=5)
            if cc_text:
                parts.append("=== 相关角色 ===\n" + cc_text)
        except Exception:
            pass

    if verbose:
        print(f"[召回] script_id={script_id}  BM25片段：{len(snippets)}条")

    return "\n\n".join(parts)


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
            high = db.execute(
                "select title, content, metadata from worldbook_entries "
                "where script_id=%s and enabled=true and priority>=80 "
                "order by priority desc, id asc limit 10",
                (script_id,),
            ).fetchall() or []
            # task 122: 用当前 chapter 过滤
            if current_chapter_max is not None:
                high = [r for r in high if _entry_chapter_min(r) <= current_chapter_max]
            high = high[:5]  # 过滤后取 top 5
            # 按 key 匹配
            matched = []
            if query and query.strip() and query != "开场":
                matched = db.execute(
                    "select title, content, keys, priority, metadata from worldbook_entries "
                    "where script_id=%s and enabled=true and priority<80 "
                    "order by priority desc, id asc limit 40",
                    (script_id,),
                ).fetchall() or []
                if current_chapter_max is not None:
                    matched = [r for r in matched if _entry_chapter_min(r) <= current_chapter_max]
                matched = matched[:20]
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
        if not picks:
            return ""
        lines = []
        for r in picks:
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
