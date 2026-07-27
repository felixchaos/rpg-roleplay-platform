"""retrieval.progress — 进度窗口族:active save / 剧情 phase 章节窗口解析。

拆包(纯机械搬家):自 rpg/retrieval.py 逐字搬来,函数体零改动。
DB 访问全为函数内局部 import(platform_app.db),无模块级外部依赖。
"""
from __future__ import annotations

import re


# task 117: 算法层 phase 推导 — 不硬编码"第一章"/"火星"/"柏林"。
# 当 world.time 空 / state 干净时,从 save.active_phase_index + save_phase_digests
# 或 fallback 到 script 级 phase_digests 拿当前 phase 的 chapter_range,
# 让 BM25 / worldbook 检索被自动限制到正确的剧情阶段,而不是检索整本书。
# 通用于任意小说 — 只要剧本导入流程跑过 phase_digest 聚合 (task 85),就有数据。
# `phase_digests.summary` **不是摘要**——生产实测全站 521/521 条都是「第N章 · <该章开头正文>」
# 的拼接(单条 3000 字,首行还是一整排 `====` 分隔线)。两个下游都会被它毒到:
#   · 「当前剧情阶段 (phase fallback)」块注入它前 600 字 = 一屏 ==== 加第 1-3 章原文;
#   · assemble 的 main_quest 派生取 `f"{label} — {summary}"[:200]` → 玩家可见的【主线】
#     会变成「开端 — 第1章 · ============…」(仅在主线为空/仍是上次派生值时才写,所以
#     手写过主线的档侥幸没中招)。
# 判据取**形状**不取关键词:以「第N章 ·」开头,或含长连续分隔线 —— 命中就当没有摘要,
# 只用 phase_label。真·摘要(LLM 产的散文)不会长这样。
_CHAPTER_CONCAT = re.compile(r"^\s*第\s*\d+\s*章\s*·|={10,}|-{20,}")


def _usable_phase_summary(raw: object) -> str:
    s = str(raw or "").strip()
    if not s or _CHAPTER_CONCAT.search(s):
        return ""
    return s


def _resolve_active_phase_range(save_id: int | None, script_id: int | None,
                                progress_chapter: int | None = None) -> dict | None:
    """返回当前 phase 的 {chapter_min, chapter_max, phase_label, summary},
    或 None (DB 没数据时)。

    算法:
      1. 如果 save_id 给了 → 读 game_saves.active_phase_index
         - 如果该 index 在 save_phase_digests 有 row → 拿它的 phase_label 去
           script 级 phase_digests 查 chapter_min/max + summary
         - 否则继续到 step 2
      2. **progress_chapter 给了 → 取包住它的那个 phase**(下方新增)
      3. fallback: script 级 phase_digests 按 (chapter_min, chapter_max) ASC
         取第一个 → 这就是"剧本最早期的 phase"

    ⚠️ step 2 是 v1.73.1 补的。原来 step 1 一断就直接掉到 step 3,而 step 1 断得很容易——
    `game_saves.active_phase_index` 可以指向 `save_phase_digests` 里**根本不存在的行**
    (生产实证 save 268:active_phase_index=2,该表只有 phase 0/1)→ 静默退到「最早期
    phase」= 第 1-78 章。玩家在第 67 章,拿到的阶段概要和检索窗口全是开篇。
    本函数的返回值还喂给 assemble 的 main_quest 派生,所以这个洞同时让「主线永远停在开端」。
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
            # 进度已知 → 取包住玩家当前章的 phase(active_phase_index 断链时的正确兜底)
            if not row and progress_chapter and int(progress_chapter) >= 1:
                row = _db.execute(
                    "select phase_label, chapter_min, chapter_max, summary "
                    "from phase_digests where script_id = %s "
                    "and chapter_min is not null and chapter_max is not null "
                    "and chapter_min <= %s and chapter_max >= %s "
                    "order by chapter_min desc limit 1",
                    (script_id, int(progress_chapter), int(progress_chapter)),
                ).fetchone()
                if not row:
                    # 进度超出所有 phase 区间(发散档/估章跑过头)→ 取不晚于进度的最后一个 phase,
                    # 宁可停在玩家已走过的阶段,也绝不跳到更后面的 phase(剧透方向只退不进)。
                    row = _db.execute(
                        "select phase_label, chapter_min, chapter_max, summary "
                        "from phase_digests where script_id = %s "
                        "and chapter_min is not null and chapter_max is not null "
                        "and chapter_min <= %s "
                        "order by chapter_min desc limit 1",
                        (script_id, int(progress_chapter)),
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
                    "summary": _usable_phase_summary(row.get("summary")),
                }
    except Exception:
        pass
    return None


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
