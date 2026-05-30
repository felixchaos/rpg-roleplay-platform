"""platform_app/knowledge/llm_extract.py — 平台侧 LLM 提取入口(Phase A 接通)。

把 extract.pipeline(Pass0-3)接到平台:查 script+book,跑 LLM 管线写 kb_canon_* +
constant worldbook + 时间线 + 实体嵌入。**替代/补强** _extract_fact 关键词管线。

成本铁律:默认便宜模型(gemini-3.5-flash);全书回填(866 章)是显式运营动作。
默认 import 不自动跑(避免每次 import 烧钱);由作业/管理触发或带 sample 小验。
"""
from __future__ import annotations

from typing import Any, Callable

from platform_app.db import connect
from platform_app.knowledge._sync import _ensure_book


def run_llm_extraction(
    user_id: int,
    script_id: int,
    *,
    author_era: str = "",
    author_power_system: list[str] | None = None,
    author_worldlines: list[dict] | None = None,
    model: str = "gemini-3.5-flash",
    api_id: str = "vertex_ai",
    sample_chapters: int | None = None,
    progress_cb: Callable[[str, dict], None] | None = None,
) -> dict[str, Any]:
    """对一本已导入剧本跑 LLM 提取管线,产出规范层 KB。

    sample_chapters: 限前 N 章(小验/控成本);None=全书(Phase H 回填)。
    """
    from extract.pipeline import run_extraction

    # 短连接只为查 script + ensure book(不跨 LLM 持连)
    with connect() as db:
        script = db.execute(
            "select * from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not script:
            raise ValueError("无权访问该剧本")
        book_id = _ensure_book(db, script)["id"]

    # run_extraction 内部自管连接(LLM 期间不持连)
    return run_extraction(
        script_id, book_id,
        user_id=user_id, author_era=author_era,
        author_power_system=author_power_system, author_worldlines=author_worldlines,
        model=model, api_id=api_id, sample_chapters=sample_chapters,
        progress_cb=progress_cb,
    )
