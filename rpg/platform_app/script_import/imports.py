from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from chapter_splitter import chapter_splitter

from ..db import connect, expose, init_db
from ..library import decode_upload, safe_filename, unique_path
from ._base import BASE, MAX_SCRIPT_UPLOAD_BYTES, SCRIPT_ROOT
from .sync_jobs import _schedule_knowledge_sync
from .uploads import _consume_upload_chunks

logger = logging.getLogger(__name__)


# ReDoS 防护：长度上限 + 禁止嵌套量词模式（(.+)+  (.*)*  ([^x]+)+ 等）
_NESTED_QUANTIFIER_RE = __import__("re").compile(r"\([^)]*[+*][^)]*\)[+*]")


def _validate_custom_pattern(pattern: str) -> None:
    """校验用户自定义正则，防止 ReDoS。"""
    import re as _re
    if len(pattern) > 200:
        raise ValueError("正则过长（上限 200 字符）")
    if _NESTED_QUANTIFIER_RE.search(pattern):
        raise ValueError("custom_pattern 含嵌套量词，可能导致 ReDoS，拒绝")
    try:
        _re.compile(pattern)
    except Exception as exc:
        raise ValueError(f"custom_pattern 不是合法正则：{exc}") from exc


def import_script(
    user_id: int,
    file_item: dict[str, Any] | None = None,
    *,
    split_rule: str = "auto",
    custom_pattern: str = "",
    title: str = "",
    upload_id: str = "",
) -> dict[str, Any]:
    """导入剧本。两种来源：
    - file_item: 单次 POST 的 base64（≤8MB 直接走这条）
    - upload_id: 已通过 init_upload + put_chunk + finish_upload 完成的分片
    """
    init_db()
    if upload_id:
        raw = _consume_upload_chunks(user_id, upload_id, peek=False)
        original_name = safe_filename(file_item.get("name") if file_item else None or Path(upload_id).name + ".txt")
    elif file_item:
        original_name = safe_filename(file_item.get("name") or "script.txt")
        raw = decode_upload(file_item)
    else:
        raise ValueError("请提供 file 或 upload_id")
    if len(raw) > MAX_SCRIPT_UPLOAD_BYTES:
        raise ValueError(f"剧本文件过大：{original_name}")

    text, encoding = chapter_splitter.decode_bytes(raw)
    cleaned = chapter_splitter.clean_text(text)
    if not cleaned:
        raise ValueError("剧本文本为空")

    script_title = (title or Path(original_name).stem or "未命名剧本").strip()[:160]

    # 自定义正则提前校验，避免坏正则被静默回退到 auto
    if (split_rule or "").strip() == "custom":
        if not (custom_pattern or "").strip():
            raise ValueError("split_rule=custom 时必须提供 custom_pattern")
        _validate_custom_pattern(custom_pattern)

    chapters, report = chapter_splitter.split_chapters_with_report(
        text,  # 传未清洗文本: with_report 内部 _normalize_encoding + sanitize 并计入 cleaning 报告
        split_rule=split_rule or "auto",
        custom_pattern=custom_pattern or "",
        source_name=original_name,
        title=script_title,
    )
    # 用户明确选了某种模式但实际走了另一种，要在报告里标出，并拒绝静默回退。
    # ⚠️ chapter_splitter 命中命名规则时返回的 mode 带 `rule_` 前缀(如 split_rule=chapter_cn
    #    → report.mode='rule_chapter_cn',见 chapter_splitter.py:187/212),而早先这里只拿裸名
    #    {split_rule} 比对 → 任何非 auto 规则恒不匹配被假拒("无法用 X 切分"),用户反馈"除自动外全报禁止"。
    #    修:把 `rule_<split_rule>` 也算达成;custom 的 realize mode 是 'custom_pattern'。
    #    真·回退(用户选 chapter_cn 但实际落到 adaptive_fusion/别的规则)仍会被拒,提示换规则或用自动。
    _expected_modes = {split_rule, f"rule_{split_rule}"}
    if split_rule == "custom":
        _expected_modes.add("custom_pattern")
    if (split_rule or "auto") not in {"", "auto"} and report.get("mode") not in _expected_modes:
        raise ValueError(f"无法用 {split_rule} 规则切分该文本：实际只能用 {report.get('mode')}")
    if not chapters:
        raise ValueError("没有识别到可导入章节")

    user_dir = SCRIPT_ROOT / f"user_{user_id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    target_path = unique_path(user_dir / original_name)
    target_path.write_bytes(raw)

    report = {
        **report,
        "encoding": encoding,
        "source_name": original_name,
        "storage_path": str(target_path.relative_to(BASE)),
    }
    total_words = sum(len(chapter.get("content") or "") for chapter in chapters)
    description = f"导入剧本 · {len(chapters)}章 · {report.get('mode_label', report.get('mode'))} · 置信 {report.get('confidence')}"

    with connect() as db:
        script = db.execute(
            """
            insert into scripts(owner_id, title, description, source_path, chapter_count, word_count,
                                 import_report, review_status)
            values (%s, %s, %s, %s, %s, %s, %s, 'unreviewed')
            returning *
            """,
            (user_id, script_title, description, str(target_path.relative_to(BASE)), len(chapters), total_words, Jsonb(report)),
        ).fetchone()
        with db.cursor() as cur:
            cur.executemany(
                """
                insert into script_chapters(
                  script_id, chapter_index, title, content, word_count,
                  volume_title, source_marker, confidence,
                  is_author_note, exclude_from_extraction, title_confidence, content_descriptor
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        script["id"],
                        index,
                        str(chapter.get("title") or f"第{index}章")[:200],
                        str(chapter.get("content") or ""),
                        len(str(chapter.get("content") or "")),
                        str(chapter.get("volume_title") or ""),
                        str(chapter.get("source_marker") or ""),
                        float(report.get("confidence") or 0),
                        bool(chapter.get("is_author_note", False)),
                        bool(chapter.get("exclude_from_extraction", False)),
                        float(chapter.get("title_confidence", 1.0)),
                        str(chapter.get("content_descriptor") or ""),
                    )
                    for index, chapter in enumerate(chapters, start=1)
                ],
            )

    # 登记 user_assets（失败只 log，不影响导入主流程）
    try:
        from platform_app.assets_registry import register_asset  # lazy import
        from platform_app.storage import PLATFORM_DATA_ROOT as _PDATA_ROOT
        # storage_key = "scripts/{relative_from_PLATFORM_DATA_ROOT}"
        # target_path 在 SCRIPT_ROOT/user_{id}/filename = PLATFORM_DATA_ROOT/scripts/user_{id}/filename
        _script_rel = str(target_path.relative_to(_PDATA_ROOT))  # e.g. scripts/user_1/foo.txt
        register_asset(
            user_id=int(user_id),
            kind="script_txt",
            storage_key=_script_rel,
            url="/api/storage/" + _script_rel,
            source="script_import",
            ref_kind="script",
            ref_id=int(script["id"]),
            mime="text/plain",
            size=len(raw),
            meta={"name": original_name},
        )
    except Exception as _reg_exc:
        logger.warning(
            "[script_import] register_asset failed script_id=%s: %s",
            script["id"], _reg_exc,
        )

    # phase_backend: 不再起 kind='knowledge_sync' 旧任务。
    # 上传完成就直接 schedule_full_import (kind='full_pipeline'),前端订阅 SSE 看真进度。
    # 老的 knowledge_sync 路径只在用户没配 LLM 凭证、或被显式 /knowledge/sync 调用时才走 fallback。
    # 这样 wizard 不再出现"toast 导入成功 → 任务静默死掉"的撕裂。
    try:
        from ..import_pipeline import schedule_full_import
        sched = schedule_full_import(user_id, script["id"])
        job_id = sched.get("job_id")
        kind = "full_pipeline"
    except Exception as exc:
        # 没配 user LLM 凭证 / 别的 ValueError → 退到老的零 LLM 路径
        # (sync_script_knowledge 把 facts/cards 从词典聚合,不调 LLM)。
        # 不 silent swallow:把 exc 记到 import_report,前端能看到为什么走了 fallback。
        logger.warning(
            "import_script: schedule_full_import failed (%s), fallback to knowledge_sync",
            exc, exc_info=True,
        )
        job_id = _schedule_knowledge_sync(user_id, script["id"])
        kind = "knowledge_sync"
    return {
        "script": expose(script),
        "report": report,
        "knowledge": {
            "ok": True, "job_id": job_id, "status": "pending",
            "async": True, "kind": kind,
        },
        "preview": _chapter_preview(chapters),
    }


def _chapter_preview(chapters: list[dict], limit: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "chapter_index": index,
            "title": str(chapter.get("title") or f"第{index}章"),
            "volume_title": str(chapter.get("volume_title") or ""),
            "word_count": len(str(chapter.get("content") or "")),
            "content_preview": str(chapter.get("content") or "").replace("\n", " ")[:120],
        }
        for index, chapter in enumerate(chapters[:limit], start=1)
    ]


# ══════════════════════════════════════════════════════════════════════
#  Dry-run 预切（不入库）
# ══════════════════════════════════════════════════════════════════════
def preview_split(
    file_item: dict[str, Any] | None = None,
    *,
    split_rule: str = "auto",
    custom_pattern: str = "",
    upload_id: str = "",
    user_id: int | None = None,
    sample_limit: int = 20,
) -> dict[str, Any]:
    """前端调参用：返回切分预览但不入库。

    输入：file_item（base64 同 /api/scripts/import）或 upload_id（已分片上传完的文件）
    """
    if upload_id:
        raw = _consume_upload_chunks(user_id, upload_id, peek=True)
    elif file_item:
        raw = decode_upload(file_item)
    else:
        raise ValueError("需要 file 或 upload_id")
    if len(raw) > MAX_SCRIPT_UPLOAD_BYTES:
        raise ValueError("剧本文件过大")

    text, encoding = chapter_splitter.decode_bytes(raw)
    cleaned = chapter_splitter.clean_text(text)
    if not cleaned:
        raise ValueError("剧本文本为空")

    if (split_rule or "").strip() == "custom":
        if not (custom_pattern or "").strip():
            raise ValueError("split_rule=custom 时必须提供 custom_pattern")
        _validate_custom_pattern(custom_pattern)

    chapters, report = chapter_splitter.split_chapters_with_report(
        text, split_rule=split_rule or "auto",  # 传未清洗文本: with_report 内部清洗并计入 cleaning 报告
        custom_pattern=custom_pattern or "",
        source_name=str(file_item and file_item.get("name") or "preview.txt"),
        title="preview",
    )
    return {
        "ok": True,
        "encoding": encoding,
        "report": report,
        "total_chapters": len(chapters),
        "total_words": sum(len(c.get("content") or "") for c in chapters),
        "preview": _chapter_preview(chapters, limit=sample_limit),
    }
