"""retrieval — 两段式召回(拆包薄门面)。

原 rpg/retrieval.py(1298 行,GM 上下文检索热路径:rail 原著注入/进度窗口/防剧透闸)
于 2026-07 按职责拆为子包,纯机械搬家、行为零变化。本 __init__ 是薄门面,逐名 re-export
原模块全部顶层名(含下划线名 + 顶层 import 的名),使全仓 `import retrieval` /
`from retrieval import X` / `retrieval.X` 均零改动:
  _common.py      — log + BASE/路径常量(BASE .parent.parent trap-2 修正)
  defaults.py     — _is_default_mumu_script + 默认小说泄漏行过滤
  progress.py     — 进度窗口族(_resolve_save_id_from_user / _resolve_active_phase_range)
  anchor_prose.py — rail 原文注入族(_load_anchor_chapter_text / _extract_style_sample)
  sources.py      — RAG 召回族(bm25/摘要/facts/worldbook/角色卡 + 角色检测)
  assemble.py     — 组装入口(retrieve_context + _ensure_timeline_ready)

注:mutable 全局 _CHAR_ALIASES(sources)/_TIMELINE_READY(assemble)住在各自子模块;
本门面上的同名是 import 时快照,运行期真值以子模块为准(全仓无跨模块读者,仅 dir() 兼容)。
"""
from __future__ import annotations

# 原顶层 import 的名字(调用方可能以 retrieval.X 形式引用)——保持可见
import json  # noqa: F401
import re  # noqa: F401
import sqlite3  # noqa: F401
from pathlib import Path  # noqa: F401

from config.glossary import get_leak_filter_tokens  # noqa: F401
from core.logging import get_logger  # noqa: F401
from timeline_index import (  # noqa: F401
    bootstrap_timeline_from_summaries,
    timeline_filter_for_label,
)

from ._common import (  # noqa: F401
    BASE,
    CHAR_IDX,
    DB_PATH,
    FACT_DB,
    SUM_IDX,
    WORLD_IDX,
    log,
)
from .anchor_prose import (  # noqa: F401
    _extract_style_sample,
    _load_anchor_chapter_text,
)
from .assemble import (  # noqa: F401
    _TIMELINE_READY,
    _ensure_timeline_ready,
    retrieve_context,
)
from .defaults import (  # noqa: F401
    _DEFAULT_NOVEL_LEAK_TOKENS,
    _is_default_mumu_script,
    _strip_default_novel_leakage,
)
from .progress import (  # noqa: F401
    _resolve_active_phase_range,
    _resolve_save_id_from_user,
)
from .sources import (  # noqa: F401
    _CHAR_ALIASES,
    _entry_chapter_min,
    _load_aliases,
    _load_script_character_cards,
    _load_worldbook_for_retrieval,
    _sqlite_available,
    bm25_search,
    detect_mentioned_characters,
    load_chapter_facts,
    load_character_cards,
    load_recent_summaries,
    load_summaries_window,
)
