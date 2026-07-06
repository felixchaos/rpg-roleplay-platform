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

from core.logging import get_logger
from timeline_index import bootstrap_timeline_from_summaries, timeline_filter_for_label
from config.glossary import get_leak_filter_tokens

log = get_logger(__name__)

BASE     = Path(__file__).parent
DB_PATH  = BASE.parent / ".webnovel" / "vectors.db"
FACT_DB  = BASE.parent / ".webnovel" / "chapter_facts.db"
CHAR_IDX = BASE / "indexes" / "characters.json"
WORLD_IDX= BASE / "indexes" / "world.json"
SUM_IDX  = BASE / "indexes" / "summaries.json"

# 旧版默认剧本的本地角色索引已停用；运行期角色卡/世界书走数据库按 script_id scope 读取。
_CHAR_ALIASES: dict[str, str] = {}   # lazy-loaded
_TIMELINE_READY = False

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
# IP terms loaded from config/novel_glossary.json (gitignored) or .example.json.
# Do NOT hardcode novel-specific names here; edit novel_glossary.json instead.
_DEFAULT_NOVEL_LEAK_TOKENS = get_leak_filter_tokens()


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
                limit 48
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
    is_default = _is_default_mumu_script(script_id)
    timeline_filter = None
    # BUG-2/BUG-3: 玩家进度 + 元知识模式,函数级缓存供层级图过滤 / entity 召回天花板用。
    # spoiler-safe 默认:progress=1(绝不 None,否则 _reveal_clause 放行全书=剧透)、mode=none。
    _progress_chapter = 1
    _foreknowledge_mode: str = "none"
    # 剧情引导强度(rail=贴原著 / guided=软引导默认 / free=自由),从 game_sessions.worldline
    # jsonb 读(与 foreknowledge_mode 同源)。rail 档下,下方「锚点章节原文」会确定性注入当前
    # 进度章节的原著正文(含对话)并指示忠实重现;guided/free 维持活世界默认(原文仅作风格参考)。
    _steering_strength: str = "guided"
    _save_id_prog: int | None = None  # P4(S5):前沿门控需要;在进度同步块里赋值,此处先兜底防 NameError
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
        # ── BUG-3: 把"时间线派生的当前章节"materialize 进 progress_chapter ──────────
        # 病灶:gm_serving.settings.advance_progress 全库零调用 → 新存档 progress 恒=1,
        # Phase D(canon_repo._reveal_clause)与层级图永远只看第 1 章实体。
        # 真·进度信号 = get_progress_window 的 chapter_min(玩家当前所处章节:已满足锚点+1 /
        # world.time 标签映射章 / fallback=1),它对存量存档也有效(不依赖从未写过的 progress_chapter)。
        # 每回合幂等同步(advance_progress 取 max 只增不减),并顺手读 foreknowledge_mode。
        # 剧透方向:用 chapter_min(当前位置)而非 chapter_max(+50 前瞻窗口),绝不超前揭示。
        if script_id:
            try:
                _save_id_prog = _resolve_save_id_from_user(user_id)
                if _save_id_prog:
                    from gm_serving.settings import advance_progress as _adv_prog
                    from platform_app.db import connect as _conn_prog
                    with _conn_prog() as _db_prog:
                        _sess_prog = _db_prog.execute(
                            "select worldline from game_sessions where save_id=%s", (_save_id_prog,)
                        ).fetchone()
                        _wl_prog = (_sess_prog or {}).get("worldline") if _sess_prog else None
                        if isinstance(_wl_prog, dict):
                            _foreknowledge_mode = _wl_prog.get("foreknowledge_mode") or "none"
                            _steering_strength = _wl_prog.get("steering_strength") or "guided"
                        # 进度真源 = 存档已写的 progress_chapter(权威)+ 已满足锚点最大原著章(reliable)。
                        # 【绝不】再用 world.time→timeline 映射(旧 get_progress_window.chapter_min)
                        # materialize 进度:story_time_label 是不可靠的「章节标题当时间」(见
                        # project_timeline_world_model),会把 progress bogus-jump 到远章(实测 occ=0
                        # 的存档被推到 77/89);advance_progress 又 max-only 不可逆 → 用户卡死。
                        # 只按【已确认锚点】(occurred/variant)的最大原著章确定性推进。
                        try:
                            _progress_chapter = max(1, int((_wl_prog or {}).get("progress_chapter") or 1))
                        except (TypeError, ValueError):
                            _progress_chapter = 1
                        _msat = _db_prog.execute(
                            "select coalesce(max(source_chapter), 0) as c from save_anchor_states "
                            "where save_id = %s and status in ('occurred', 'variant')",
                            (_save_id_prog,),
                        ).fetchone()
                        _last_sat = int((_msat or {}).get("c") or 0)
                        if _last_sat >= 1:
                            _adv_prog(_db_prog, _save_id_prog, _last_sat)
                            _progress_chapter = max(_progress_chapter, _last_sat)
                        # P4(S7):flag on 时进度改由【前沿派生】——丢弃可能被旧猜章器冲高的 worldline 标量
                        # (over-shoot 根源),但绝不低于「已确认锚点」可靠底 _last_sat。正常档 derived==_last_sat,
                        # 等价旧行为;over-shoot 档则收敛回真实章。shadow 记 标量↔派生 供切换前核对。
                        from kb.reveal import (_frontier_on as _fr_on,
                                               _frontier_shadow as _fr_shadow,
                                               derived_progress_chapter as _dpc)
                        if _fr_on(_save_id_prog):
                            try:
                                _derived = _dpc(_save_id_prog, db=_db_prog)
                                if _fr_shadow():
                                    log.warning("[shadow] progress scalar=%s derived=%s floor=%s",
                                                _progress_chapter, _derived, _last_sat)
                                _progress_chapter = max(1, _last_sat, int(_derived))
                            except Exception as _dpc_exc:
                                log.warning("[retrieval] derived_progress_chapter 跳过(非致命): %s", _dpc_exc)
                        elif _fr_shadow():
                            try:
                                log.warning("[shadow] progress scalar=%s derived=%s floor=%s",
                                            _progress_chapter, _dpc(_save_id_prog, db=_db_prog), _last_sat)
                            except Exception:
                                pass
                        # ── 时间线战役批次1:揭示天花板估章钳制。生产实锤(save 268):
                        # occurred 冻结 ch7 十天、估章把 progress_chapter 顶到 17 →
                        # ch8-17 世界书/实体/NPC 被超前揭示(反馈#82 后期NPC)、剧情被当
                        # 已发生。确定性地板 = max(occurred/variant 最大章, 玩家 /set
                        # 显式地板 user_progress_floor);有地板才钳(纯发散无锚档不钳,
                        # 发散解冻语义保留);玩家显式跳章走地板放行(逃生阀 d50eb926a)。
                        # env RPG_REVEAL_ESTIMATE_LOOKAHEAD 默认 3,<=0 关闭钳制。
                        try:
                            import os as _os_clamp
                            from gm_serving.settings import clamp_reveal_progress as _clamp_rp
                            _user_floor = int((_wl_prog or {}).get("user_progress_floor") or 0)
                            _det_floor = max(int(_last_sat or 0), _user_floor)
                            _lookahead = int(_os_clamp.environ.get("RPG_REVEAL_ESTIMATE_LOOKAHEAD", "3") or 3)
                            _clamped = _clamp_rp(_progress_chapter, _det_floor, _lookahead)
                            if _clamped != _progress_chapter:
                                log.info("[retrieval] 揭示进度钳制: %s → %s (floor=%s, lookahead=%s)",
                                         _progress_chapter, _clamped, _det_floor, _lookahead)
                                _progress_chapter = _clamped
                        except Exception as _clamp_err:
                            log.warning(f"[retrieval] 揭示钳制跳过(非致命): {_clamp_err}")
            except Exception as _prog_err:
                log.warning(f"[retrieval] progress_chapter 同步跳过(非致命): {_prog_err}")
            # C 修(反馈「主线不更新」):main_quest 从当前 phase 派生(锚点系统已知阶段),不再靠 GM
            # 记得发【主线】→ 主线永不 stale。非破坏:仅当 main_quest 为空、或仍是上次自动派生值
            # (=没被玩家 set_main_quest / GM【主线】手改)时刷新,保护手写主线。
            try:
                _mqp = _resolve_active_phase_range(_save_id_prog, script_id)
                if _mqp and (_mqp.get("phase_label") or _mqp.get("summary")):
                    _pl = (_mqp.get("phase_label") or "").strip()
                    _ps = (_mqp.get("summary") or "").strip()
                    _derived_mq = (f"{_pl} — {_ps}" if _pl and _ps else (_pl or _ps))[:200]
                    _mem = state.data.setdefault("memory", {})
                    _cur_mq = str(_mem.get("main_quest") or "").strip()
                    _last_mq = str((state.data.get("player_private") or {}).get("_derived_main_quest") or "")
                    if _derived_mq and (not _cur_mq or _cur_mq == _last_mq):
                        _mem["main_quest"] = _derived_mq
                        state.data.setdefault("player_private", {})["_derived_main_quest"] = _derived_mq
            except Exception:
                pass
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
        # 群反馈(行者无疆,rail 档"原文完全没注入"):timeline.chapter_min/max 是 /set 时间
        # 跳跃(resolve_timeline_anchor)持续更新的【当前锚定章】,anchor_chapter_range 是
        # 出生点建档写死的一次性遗留(此后无任何代码更新它)。旧序=range 绝对优先 →
        # /set 跳到 ch17 后 rail 每回合仍按建档 range[1,1] 注入第 1 章原文。
        # 新序=鲜活锚定优先,range 只作建档兜底。
        try:
            _tl_cmin = int(timeline.get("chapter_min") or 0)
            _tl_cmax = int(timeline.get("chapter_max") or 0)
        except (TypeError, ValueError):
            _tl_cmin = _tl_cmax = 0
        if _tl_cmin > 0:
            anchor_min = _tl_cmin
            anchor_max = _tl_cmax if _tl_cmax >= _tl_cmin else _tl_cmin
        elif isinstance(anchor_range, list) and len(anchor_range) >= 1:
            try:
                anchor_min = int(anchor_range[0])
                anchor_max = int(anchor_range[1]) if len(anchor_range) > 1 else anchor_min
            except (TypeError, ValueError):
                pass
        # turn=0 / 空 history → 也走章节原文注入 (用 phase 起始章)
        is_opening = (int(state.data.get("turn", 0) or 0) == 0
                      and not (state.data.get("history") or []))
        # 修复 ongoing 回合饥饿:原来只有 is_opening 才从时间线派生 anchor_min,
        # 正常游戏回合 anchor_min=None → 章节原文整段不注入,GM 每轮只拿 bm25 碎片,
        # 拿不到当前章节原著正文 → 写不出原著文风/细节。现在任何回合只要有时间线窗口
        # 都注入当前窗口原文。
        if anchor_min is None and (timeline_filter or {}).get("chapter_min"):
            anchor_min = int(timeline_filter["chapter_min"])
            anchor_max = int(timeline_filter.get("chapter_max") or anchor_min)
        # 兜底:时间线没精确命中章节时,用世界线收束的"进度→章节"窗口(get_progress_window),
        # 这才是权威的当前进度章节段(ch1..30 等)。保证每轮都注入当前进度的原著正文,
        # 不再因 timeline 未命中就整段不注入。
        if anchor_min is None and script_id:
            try:
                from agents.anchor_seed_agent import get_progress_window as _gpw
                _sid2 = _resolve_save_id_from_user(user_id)
                if _sid2:
                    _pw = _gpw(_sid2, world_time_label=(world.get("time") or "").strip(),
                               script_id=int(script_id), window_size=50)
                    if _pw and _pw.get("chapter_min"):
                        anchor_min = int(_pw["chapter_min"])
                        anchor_max = int(_pw.get("chapter_max") or anchor_min)
            except Exception:
                pass
        # 开场兜底:若所有派生都没给出 anchor_min(timeline 未命中 + 无进度窗口),
        # 开局(turn=0)必须仍从序章(第1章)起注入原著正文 —— 否则 GM 收不到任何原著开篇,
        # 即便开了「贴原著」也会凭训练数据自由发挥开局(用户反馈:序章脱离原著、自设剧情)。
        if anchor_min is None and is_opening and script_id:
            anchor_min, anchor_max = 1, 3
        # world_key scope(批次3b-2):把锚点原文窗口 clamp 到玩家当前世界的连续章节段,
        # 防跨副本串味(副本A 的回合不注入副本B 的原著正文)。书未做世界切分时
        # resolve_world_bounds 返回 None → 不 clamp(现网所有书 worldline_key 全 null
        # = 数学 no-op,行为逐字节不变);只有 3a 命中或 3b-1 --apply 后的书才生效。
        if anchor_min and script_id:
            try:
                from kb.world_scope import clamp_window_to_world, resolve_world_bounds
                from platform_app.db import connect as _wc_connect
                with _wc_connect() as _wc_db:
                    _wb = resolve_world_bounds(_wc_db, int(script_id), int(_progress_chapter))
                if _wb is not None:
                    _am2, _ax2 = clamp_window_to_world(anchor_min, anchor_max, _wb)
                    if (_am2, _ax2) != (anchor_min, anchor_max):
                        log.info("[retrieval] world scope: 锚点窗 [%s,%s]→[%s,%s] world_bounds=%s",
                                 anchor_min, anchor_max, _am2, _ax2, _wb)
                        anchor_min, anchor_max = _am2, _ax2
            except Exception as _wc_err:
                log.warning(f"[retrieval] world scope 跳过(非致命): {_wc_err}")
        if anchor_min and script_id:
            _rail = (_steering_strength == "rail")
            # rail(贴原著)档多给预算,让当前章原著的对话/桥段尽量完整进 GM 上下文。
            anchor_text = _load_anchor_chapter_text(
                int(script_id), anchor_min, anchor_max, max_chars=14000 if _rail else 9000)
            if anchor_text:
                if _rail:
                    # 贴原著(rail)档:确定性把当前进度章节的原著正文(含对话)喂进 GM,并指示
                    # 忠实重现关键对话与桥段。用户主动选了「贴原著」,本段优先级高于下方 master.py
                    # 「原文=风格参考 / 发生方式可变」的活世界默认说明(用反馈:原著对话/关键情节被跳)。
                    # 确定性部分=原文已注入+预算更大;能否复现到位仍取决于模型,故此为 rail 档语义。
                    parts.append(
                        "=== 锚点章节原文 · 贴原著档(最高优先) ===\n"
                        "本回合处于【贴原著】引导强度。以下是当前进度章节的原著正文。\n"
                        "**你必须忠实重现其中的关键对话与桥段**:原著存在的人物对话尽量保留原话 / 原意,\n"
                        "原著发生的关键情节(冲突 / 死亡 / 相遇 / 转折等)不得跳过或一笔带过。\n"
                        "玩家的输入决定切入视角与节奏,但不得让剧情脱离本章原著走向。\n"
                        "本段指示优先于其他「原文仅供风格参考 / 发生方式可变」的说明。\n"
                        "**注意:即使下方原著正文中夹带英文 / 德文等外语台词,你的叙事语言仍跟随本剧本既定的\n"
                        "主体语言(通常即原著正文的主体语言),不要因此切换;外语台词可在角色说话时原样保留,\n"
                        "但旁白 / 动作 / 心理 / 场景描写保持既定语言。**\n\n"
                        + anchor_text
                    )
                else:
                    # task 131(活世界默认):原文标记"风格 + 骨架参考,不是必须复现的戏剧强度"
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
            # fork 收编:此收束段(待发生锚点清单 + "偏离1-3轮内命运式拉回"指令)之前无视
            # _steering_strength,free 档也照注 → 与 steering.py 三档区分被架空、发散局仍被强推
            # canon(行者无疆「永远默认在修炼」)。free 档下整段跳过,只保留 steering.py 一层的正确区分。
            if _sid_for_anchors and _steering_strength != "free":
                from agents.anchor_seed_agent import (
                    get_progress_window,
                    list_pending_for_phase,
                    summarize_save_anchor_state,
                )
                # 按"游戏进度"算章节窗口,不一股脑塞全局 top-K:
                #   1. save_anchor_states 已 occurred/variant 的最大章节 + 1..+50
                #   2. /set time 时 world.time 匹配 anchor 表 story_time_label 的章节段
                #   3. fallback [1, 30] 剧本开头
                _world_time = (world.get("time") or "").strip()
                _progress = get_progress_window(
                    _sid_for_anchors, world_time_label=_world_time,
                    script_id=script_id, window_size=50,
                )
                _ch_min = _progress["chapter_min"]
                _ch_max = _progress["chapter_max"]
                # 按 chapter asc 排:剧情往前走的下一个 ~10 个,不是 importance 全局 top
                # limit=10 而非 5: ch1 通常 8+ entities(主角+地点+概念+物品+配角),
                # 取前 5 会漏掉关键配角(如卡切尔 imp=42 排第 6)。
                anchors = list_pending_for_phase(
                    _sid_for_anchors, None, limit=10,
                    chapter_min=_ch_min, chapter_max=_ch_max,
                    order_by_chapter=True,
                )
                # 窗口内空 + last_satisfied 存在 → 整本书后续可能没 pending 了,
                # 退到全局按 chapter asc(让 GM 看到下一个未触发的远端锚点,而不是空)
                if not anchors and _progress["last_satisfied_chapter"]:
                    anchors = list_pending_for_phase(
                        _sid_for_anchors, None, limit=5,
                        chapter_min=_progress["last_satisfied_chapter"] + 1,
                        order_by_chapter=True,
                    )
                summary = summarize_save_anchor_state(_sid_for_anchors)
                if anchors:
                    _src_tag = {
                        "satisfied": f"按已推进进度(原著 ch{_progress['last_satisfied_chapter']}+1..+{50})",
                        "progress_chapter": f"按玩家显式进度(progress_chapter)锁定 ch{_ch_min}..{_ch_max}",
                        "label": f"按当前时间标签 '{_world_time}' 锁定 ch{_ch_min}..{_ch_max}",
                        "fallback": "剧本开头 ch1..30(玩家未推进任何锚点)",
                    }.get(_progress["source"], "未知")
                    # iter#7: 反查 history,标已被改写的 pending 锚点
                    try:
                        from agents.save_history import find_history_for_pending
                        _ak_list = [a["anchor_key"] for a in anchors if a.get("anchor_key")]
                        _drift_map = find_history_for_pending(_sid_for_anchors, _ak_list)
                    except Exception:
                        _drift_map = {}
                    lines = [
                        "=== 世界线收束·接下来的锚点 ===",
                        f"窗口来源: {_src_tag}",
                        f"整体状态: pending={summary['pending']} occurred={summary['occurred']} "
                        f"variant={summary['variant']} superseded={summary['superseded']} "
                        f"avg_drift={summary['avg_drift']}",
                        "按章节顺序、原著在此窗口内必须发生的事件 (发生方式可变,结果不可省):",
                    ]
                    for i, a in enumerate(anchors, 1):
                        fatal_tag = "【死神来了·必发生】" if a.get("is_fatal") else ""
                        mp = a.get("must_preserve") or []
                        mv = a.get("may_vary") or []
                        # 反查 history:如果该 anchor 已被 history 改写,标 ⚠
                        drift_hist = _drift_map.get(a.get("anchor_key", ""), [])
                        drift_marker = ""
                        if drift_hist:
                            top = drift_hist[0]
                            drift_marker = (
                                f"\n   ⚠ 已被存档历史改写 (turn {top['turn']}): "
                                f"{top['summary'][:120]}\n"
                                f"   → 该 pending 状态本应已 satisfied,但 save_anchor_states 还是 pending — "
                                f"应跳过本条,不要再触发。如有遗漏可调 mark_anchor_satisfied 补登。"
                            )
                        lines.append(
                            f"{i}. [chapter {a['chapter']}, importance {a['importance']}, "
                            f"key={a['anchor_key']}] {fatal_tag}\n"
                            f"   {a['summary']}\n"
                            f"   · 必须保留: {', '.join(str(x) for x in mp) or '(参见事件描述)'}\n"
                            f"   · 可变: {', '.join(str(x) for x in mv) or '(地点/时机/旁观者)'}"
                            + drift_marker
                        )
                    lines.append(
                        "操作指引: 当锚点自然发生时调 mark_anchor_satisfied(anchor_key, "
                        "how_it_happened, drift_score)。玩家偏离时,1-3 轮内用命运式手段"
                        "(巧合/误会/他人介入)把剧情拉回最近锚点。当玩家 /set 跳时间时,"
                        "本窗口会重算到新的章节段。"
                    )
                    parts.append("\n".join(lines))
                elif summary.get("total", 0) > 0:
                    parts.append(
                        "=== 世界线收束·进度 ===\n"
                        f"本进度窗口 (ch{_ch_min}..{_ch_max}) 无 pending 锚点。"
                        f"整体: occurred={summary['occurred']} "
                        f"variant={summary['variant']} avg_drift={summary['avg_drift']}"
                    )
        except Exception as _anchor_err:
            log.warning(f"[retrieval] pending_anchors 注入失败 (非致命): {_anchor_err}")

        # ── 存档独立时间线·历史锚点 (跟上面【世界线收束·接下来的锚点】平行的另一套) ──
        # 上面那段 = 原著未来 (玩家还没推进到的剧本必然事件)
        # 下面这段 = 玩家创造的过去 (玩家在这个世界线已经做过的重要事)
        # 一定要分清:防止 GM 把【pending 原著未来】误叙为【已发生历史】=记忆污染。
        try:
            _sid_for_hist = _resolve_save_id_from_user(user_id)
            if _sid_for_hist:
                from agents.save_history import history_summary, list_recent_history
                hist = list_recent_history(_sid_for_hist, limit=6, min_importance=0)
                hsum = history_summary(_sid_for_hist)
                if hist:
                    hlines = [
                        "=== 存档独立时间线·玩家创造的历史 (过去时态) ===",
                        f"本存档共积累 {hsum['total']} 条历史锚点 (GM 写 {hsum['gm_count']} / "
                        f"玩家声明 {hsum['player_count']}),最高 importance={hsum['max_importance']}",
                        "下面是最近 6 条 (turn 倒序),必须当作【已经发生的事实】,",
                        "不要重复触发、不要描述成『接下来要发生』:",
                    ]
                    for i, h in enumerate(hist, 1):
                        tag_str = ", ".join(h["tags"]) if h["tags"] else ""
                        chars_str = ", ".join(h["characters"]) if h["characters"] else ""
                        link_str = ""
                        if h["linked_anchors"]:
                            link_str = f" [改写原著锚点: {', '.join(h['linked_anchors'])}]"
                        hlines.append(
                            f"{i}. [turn {h['turn']}, importance {h['importance']}]{link_str}\n"
                            f"   {h['summary']}\n"
                            f"   · 涉及: {chars_str or '(未标注)'}"
                            + (f" · 标签: {tag_str}" if tag_str else "")
                        )
                    hlines.append(
                        "操作指引: 玩家本轮做出 importance ≥60 的事时,调 record_history_anchor 留档。"
                        "需要追溯某角色的历史时调 list_recent_history(character_filter='XX')。"
                    )
                    parts.append("\n".join(hlines))
                else:
                    # 没历史 → 提示 GM 这是早期 turn,主动留档高 importance 事件
                    parts.append(
                        "=== 存档独立时间线·玩家创造的历史 ===\n"
                        "本存档暂无历史锚点。当玩家做出 importance ≥60 的事 "
                        "(改 NPC 关系/势力立场,或改写原著锚点) 时,调 record_history_anchor 留档,"
                        "下次 GM 就能查 list_recent_history 看自己创造了什么。"
                    )
                # 永恒记忆·情景召回(episodic_recall flag 默认关):按当前情境从【全程】游戏历史
                # 语义召回最相关的往事,补足"近因 6 条"覆盖不到的远期记忆。分支安全(谱系 CTE)、
                # 无 embedder/pgvector 静默返空。写在玩家创造的历史块,绝不碰 script 域。
                try:
                    from core.feature_flags import feature_enabled
                    if feature_enabled("episodic_recall", user_id):
                        from platform_app.db import connect as _epi_connect
                        with _epi_connect() as _edb:
                            _cm = _edb.execute(
                                "select active_commit_id from game_saves where id=%s", (_sid_for_hist,),
                            ).fetchone()
                        _commit = int((_cm or {}).get("active_commit_id") or 0)
                        if _commit:
                            from kb.episodic import retrieve_episodic
                            _epi = retrieve_episodic(_sid_for_hist, _commit, user_id, user_input, k=5)
                            if _epi:
                                _el = ["=== 相关往事·语义召回 (玩家亲历的过去,与本回合最相关) ==="]
                                for _i, _e in enumerate(_epi, 1):
                                    _meta = " · ".join(x for x in [
                                        (_e.get("story_time") or "").strip(),
                                        (_e.get("location") or "").strip()] if x)
                                    _el.append(f"{_i}. {_e.get('summary') or ''}" + (f"  ({_meta})" if _meta else ""))
                                _el.append("以上按当前情境从全程历史召回,当作【已发生事实】参考,勿复述成新发生。")
                                parts.append("\n".join(_el))
                                log.info("[retrieval] episodic recall 注入 %d 条 (save=%s)", len(_epi), _sid_for_hist)
                except Exception as _epi_err:
                    log.warning(f"[retrieval] episodic recall 注入失败 (非致命): {_epi_err}")
        except Exception as _hist_err:
            log.warning(f"[retrieval] history_anchors 注入失败 (非致命): {_hist_err}")

        # P0 大改 #5:组织层级图注入 — 让 GM 一眼看清"X 是 Y 下属 / Y 下辖 A B C"
        # 不再让 GM 看到 12 个平级 token 不知道层级关系。
        try:
            if script_id:
                from platform_app.db import connect as _connect_tree
                from kb.canon_repo import _reveal_clause as _rc_fn
                # BUG-2: 层级图注入 kb_canon_entities 时必须按"已揭示集合"过滤,否则
                # `order by importance desc limit 60` 会把全书后期势力/地点塞给早章玩家 = 剧透。
                # 复用 canon_repo._reveal_clause(与 Phase D 同语义,单一真源):
                #   CTE 实体用裸列;parent self-join 用 p. 前缀,防"早章子实体的后期父势力名"泄漏
                #   (父若未揭示 → join 不命中 → 该实体退化为顶级独立项,不显示父名)。
                # P4(S5):flag on 且有 save_id → 前沿门控(占位符个数不变:标量章号 → save_id)。
                from kb.reveal import (_frontier_on, _frontier_shadow, _shadow_diff_log,
                                       reveal_clause_v2 as _rc_v2)
                _use_v2_tree = bool(_save_id_prog) and _frontier_on(_save_id_prog)
                if _use_v2_tree:
                    # 遗漏修复(审计 P1,休眠于 RPG_TKB_FRONTIER off):v2 分支漏传 progress_chapter →
                    # reveal_clause_v2 无「锚点章≤当前进度章」兜底 OR,save_visible_anchors 为空(新档)时
                    # 带 reveal_anchor_key 的实体全被过滤、层级树空。与 else 分支(旧门控)一样带上进度章。
                    _rc, _rc_p = _rc_v2(int(_save_id_prog), _foreknowledge_mode, prefix="",
                                        progress_chapter=_progress_chapter)
                    _rc_par, _rc_par_p = _rc_v2(int(_save_id_prog), _foreknowledge_mode, prefix="p.",
                                                progress_chapter=_progress_chapter)
                else:
                    _rc, _rc_p = _rc_fn(_progress_chapter, _foreknowledge_mode)
                    _rc_par, _rc_par_p = _rc_fn(_progress_chapter, _foreknowledge_mode, prefix="p.")
                with _connect_tree() as _db_tree:
                    # 拉前 25 个 importance 最高的有 parent_logical_key 的实体 + 它们的 parent
                    # 再拉前 8 个无 parent 但有 children 的顶级 entity
                    rows = _db_tree.execute(
                        f"""
                        with top_entities as (
                          select logical_key, name, type, entity_subtype, parent_logical_key, importance
                          from kb_canon_entities
                          where script_id = %s
                            and type in ('faction', 'location', 'concept')
                            and entity_subtype != ''
                            and {_rc}
                          order by importance desc
                          limit 60
                        )
                        select e.logical_key, e.name, e.type, e.entity_subtype,
                               e.parent_logical_key, e.importance,
                               p.name as parent_name, p.entity_subtype as parent_subtype
                        from top_entities e
                        left join kb_canon_entities p
                          on p.script_id = %s and p.logical_key = e.parent_logical_key
                          and {_rc_par}
                        order by e.importance desc
                        """,
                        (script_id, *_rc_p, script_id, *_rc_par_p),
                    ).fetchall()
                    # 影子比对:top_entities 在旧 vs 新门控下放行的 logical_key 集合(隔离主剧透面)。
                    if _frontier_shadow() and _save_id_prog:
                        _top_sql = ("select logical_key from kb_canon_entities where script_id=%s "
                                    "and type in ('faction','location','concept') and entity_subtype != '' "
                                    "and {clause} order by importance desc limit 60")
                        _o_rc, _o_p = _rc_fn(_progress_chapter, _foreknowledge_mode)
                        # shadow 比对也带上 progress_chapter,否则 diff 恒因漏参不同、掩盖真实行为差异。
                        _n_rc, _n_p = _rc_v2(int(_save_id_prog), _foreknowledge_mode, prefix="",
                                             progress_chapter=_progress_chapter)
                        _old_keys = {r["logical_key"] for r in _db_tree.execute(
                            _top_sql.format(clause=_o_rc), (script_id, *_o_p)).fetchall()}
                        _new_keys = {r["logical_key"] for r in _db_tree.execute(
                            _top_sql.format(clause=_n_rc), (script_id, *_n_p)).fetchall()}
                        _shadow_diff_log("hierarchy top_entities", _old_keys, _new_keys)
                if rows:
                    # 建邻接:parent_lk → [(name, subtype, importance), ...]
                    by_parent: dict[str, list[dict]] = {}
                    top_level: list[dict] = []
                    for r in rows:
                        plk = (r.get("parent_logical_key") or "").strip()
                        rec = {
                            "name": r["name"], "subtype": r.get("entity_subtype") or "",
                            "type": r["type"], "imp": int(r["importance"] or 0),
                            "parent_name": r.get("parent_name") or "",
                        }
                        if plk and r.get("parent_name"):
                            by_parent.setdefault(plk, []).append(rec)
                        else:
                            top_level.append(rec)
                    tree_lines = [
                        "=== 组织/势力/地点 层级图 (取重要度前 60) ===",
                        "格式 [子类型] 名称 (importance);缩进表示从属关系。",
                        "GM 引用时务必尊重层级:'铁人团是德军下属'不要写成两个独立势力。",
                    ]
                    # 先输出有 parent 的实体按 parent group
                    parents_with_children = sorted(
                        by_parent.items(),
                        key=lambda kv: -sum(c["imp"] for c in kv[1]),
                    )[:8]
                    for parent_lk, children in parents_with_children:
                        # parent 信息从任一 child 拿
                        parent_name = children[0]["parent_name"]
                        tree_lines.append(f"\n【{parent_name}】 下辖:")
                        for ch in children[:8]:
                            stag = f"[{ch['subtype']}]" if ch["subtype"] else f"[{ch['type']}]"
                            tree_lines.append(f"  └─ {stag} {ch['name']} (imp {ch['imp']})")
                    # 再输出顶层独立实体(没 parent 但 importance 高)
                    top_solo = sorted(top_level, key=lambda r: -r["imp"])[:10]
                    if top_solo:
                        tree_lines.append("\n【顶级/独立实体】(无明确归属):")
                        for r in top_solo:
                            stag = f"[{r['subtype']}]" if r["subtype"] else f"[{r['type']}]"
                            tree_lines.append(f"  · {stag} {r['name']} (imp {r['imp']})")
                    parts.append("\n".join(tree_lines))
        except Exception as _tree_err:
            log.warning(f"[retrieval] hierarchy tree 注入失败 (非致命): {_tree_err}")

        try:
            from platform_app.knowledge import retrieve_runtime_context

            # task 52: 之前 chapter_min/max 只在 is_default 时传 → 非默认剧本
            # (包括用户导入的全部小说)retrieve 拿不到时间线边界,GM 看到全书
            # 所有 chunks/entities,第 1 章玩家被召回第 800 章人物剧透。
            # 修:**无条件**传 timeline_filter 的边界 — 它本身已经是 anchor
            # 解析结果,跟剧本是否默认无关。
            #
            # task 53: worldline divergence — 玩家分支偏离原书后,GM 不该再用
            # 原书 divergence_chapter 之后的 chunks/entities 当"确定信息"。
            # 实际 chapter_max = min(timeline.chapter_max, worldline.divergence_chapter)。
            _ch_max = timeline_filter.get("chapter_max")
            try:
                _div = (state.data.get("worldline") or {}).get("divergence_chapter") if state else None
                if isinstance(_div, int) and _div > 0:
                    _ch_max = _div if _ch_max is None else min(_ch_max, _div)
            except Exception:
                pass

            pg_context = retrieve_runtime_context(
                user_input,
                chapter_min=timeline_filter.get("chapter_min"),
                chapter_max=_ch_max,
                top_k=3,
                user_id=user_id,
                progress_chapter=_progress_chapter,  # BUG-1: entity 召回剧透天花板钳到玩家进度
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
            top_k=8,
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
            _wb_ids: set = set()
            wb_text = _load_worldbook_for_retrieval(
                script_id, user_input, top_k=3, current_chapter_max=_wb_chmax,
                seen_out=_wb_ids,
                save_id=_resolve_save_id_from_user(user_id),
            )
            # 把本路径已注入的世界书条目【唯一 id】挂到 state(瞬态属性,不落库),供
            # NovelWorldbookProvider 跳过重叠;属性缺失时 provider 照旧全注入(无回归)。
            try:
                if state is not None:
                    setattr(state, "_rag_wb_ids", _wb_ids)
            except Exception:
                pass
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
        log.info(f"[召回] script_id={script_id}  BM25片段：{len(snippets)}条")

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
